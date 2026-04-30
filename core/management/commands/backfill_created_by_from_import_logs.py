"""
Backfill created_by / updated_by for records created via file import before actor
fields existed, or when bulk_create skipped model save().

Import audit rows use object_id=batch size, not per-row PKs, so the UI cannot
infer actors from AuditLog alone. This command uses ImportLog (user + time +
created_count) with non-overlapping time windows.

Run once after deploy:
  python manage.py backfill_created_by_from_import_logs

Dry run:
  python manage.py backfill_created_by_from_import_logs --dry-run

Also patch StrainLine / Project rows auto-created in the same mouse-import window
(where created_by is still null):
  python manage.py backfill_created_by_from_import_logs --also-strains-projects
"""

from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from colony.models import Cage, Mouse, StrainLine
from core.models import ImportLog, Project


class Command(BaseCommand):
    help = (
        "Set created_by / updated_b from successful ImportLog rows for mice and cages "
        "that still have null actors (best-effort using import timestamps)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print counts only; do not write the database.",
        )
        parser.add_argument(
            "--also-strains-projects",
            action="store_true",
            help="For each mouse import window, also update StrainLine and Project with null created_by in that window.",
        )

    def handle(self, *args, **options):
        dry = options["dry_run"]
        also_sp = options["also_strains_projects"]
        n_mice = self._backfill_mice(dry)
        n_cage = self._backfill_cages(dry)
        self.stdout.write(self.style.NOTICE(f"Mice rows updated: {n_mice}"))
        self.stdout.write(self.style.NOTICE(f"Cage rows updated: {n_cage}"))
        if also_sp:
            n_sl, n_pr = self._backfill_strains_projects(dry)
            self.stdout.write(self.style.NOTICE(f"StrainLine rows updated: {n_sl}"))
            self.stdout.write(self.style.NOTICE(f"Project rows updated: {n_pr}"))

    def _backfill_mice(self, dry: bool) -> int:
        logs = list(
            ImportLog.objects.filter(
                import_type=ImportLog.ImportType.MOUSE,
                success=True,
                user_id__isnull=False,
                created_count__gt=0,
            ).order_by("created_at", "pk")
        )
        total = 0
        for i, log in enumerate(logs):
            start, end = _window_for_log(logs, i)
            qs = (
                Mouse.objects.filter(created_by_id__isnull=True, created_at__gte=start, created_at__lte=end)
                .order_by("created_at", "pk")
                .values_list("pk", flat=True)[: log.created_count]
            )
            ids = list(qs)
            if not ids:
                continue
            self.stdout.write(
                f"  Mouse import log pk={log.pk} user={log.user_id} count={len(ids)}/{log.created_count} "
                f"window=[{start.isoformat()}, {end.isoformat()}]"
            )
            if not dry:
                total += Mouse.objects.filter(pk__in=ids).update(
                    created_by_id=log.user_id,
                    updated_by_id=log.user_id,
                )
            else:
                total += len(ids)
        return total

    def _backfill_cages(self, dry: bool) -> int:
        logs = list(
            ImportLog.objects.filter(
                import_type=ImportLog.ImportType.CAGE,
                success=True,
                user_id__isnull=False,
                created_count__gt=0,
            ).order_by("created_at", "pk")
        )
        total = 0
        for i, log in enumerate(logs):
            start, end = _window_for_log(logs, i)
            qs = (
                Cage.objects.filter(created_by_id__isnull=True, created_at__gte=start, created_at__lte=end)
                .order_by("created_at", "pk")
                .values_list("pk", flat=True)[: log.created_count]
            )
            ids = list(qs)
            if not ids:
                continue
            self.stdout.write(
                f"  Cage import log pk={log.pk} user={log.user_id} count={len(ids)}/{log.created_count} "
                f"window=[{start.isoformat()}, {end.isoformat()}]"
            )
            if not dry:
                total += Cage.objects.filter(pk__in=ids).update(
                    created_by_id=log.user_id,
                    updated_by_id=log.user_id,
                )
            else:
                total += len(ids)
        return total

    def _backfill_strains_projects(self, dry: bool) -> tuple[int, int]:
        logs = list(
            ImportLog.objects.filter(
                import_type=ImportLog.ImportType.MOUSE,
                success=True,
                user_id__isnull=False,
            ).order_by("created_at", "pk")
        )
        n_sl = 0
        n_pr = 0
        for i, log in enumerate(logs):
            start, end = _window_for_log(logs, i)
            if dry:
                n_sl += StrainLine.objects.filter(
                    created_by_id__isnull=True, created_at__gte=start, created_at__lte=end
                ).count()
                n_pr += Project.objects.filter(
                    created_by_id__isnull=True, created_at__gte=start, created_at__lte=end
                ).count()
            else:
                n_sl += StrainLine.objects.filter(
                    created_by_id__isnull=True, created_at__gte=start, created_at__lte=end
                ).update(created_by_id=log.user_id, updated_by_id=log.user_id)
                n_pr += Project.objects.filter(
                    created_by_id__isnull=True, created_at__gte=start, created_at__lte=end
                ).update(created_by_id=log.user_id, updated_by_id=log.user_id)
        return n_sl, n_pr


def _window_for_log(logs: list[ImportLog], index: int) -> tuple:
    """Inclusive time window [start, end] for rows created in the same import batch.

    Rows are inserted before ``ImportLog`` is written; keep a short pre-log window
    so we do not grab unrelated older rows that still have null ``created_by``.
    """
    log = logs[index]
    end = log.created_at + timedelta(seconds=45)
    if index == 0:
        start = log.created_at - timedelta(minutes=8)
    else:
        prev = logs[index - 1].created_at
        start = max(prev + timedelta(microseconds=1), log.created_at - timedelta(minutes=8))
    return start, end
