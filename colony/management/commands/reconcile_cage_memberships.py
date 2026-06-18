"""Repair drift between Mouse.current_cage and CageMembership current rows."""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from colony.cage_lifecycle import reconcile_mouse_cage_membership
from colony.models import Cage, Mouse


RECONCILE_REASON = "Admin cage history reconciliation."


class Command(BaseCommand):
    help = "Reconcile CageMembership current rows against Mouse.current_cage."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write repairs. Without this flag the command only reports what would change.",
        )
        parser.add_argument(
            "--mouse-uid",
            help="Limit reconciliation to one mouse UID.",
        )
        parser.add_argument(
            "--reopen-closed-active-cages",
            action="store_true",
            help="Also reopen closed cages that currently contain active mice.",
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        mouse_uid = (options.get("mouse_uid") or "").strip()
        reopen_closed = options["reopen_closed_active_cages"]
        repair_date = timezone.localdate()

        qs = (
            Mouse.objects.filter(Q(current_cage_id__isnull=False) | Q(cage_memberships__is_current=True))
            .select_related("current_cage")
            .distinct()
            .order_by("mouse_uid", "pk")
        )
        if mouse_uid:
            qs = qs.filter(mouse_uid=mouse_uid)

        results = []
        for mouse in qs:
            result = reconcile_mouse_cage_membership(
                mouse,
                repair_date=repair_date,
                reason=RECONCILE_REASON,
                apply=apply,
            )
            if result["changed"]:
                results.append(result)
                self.stdout.write(self._format_result(result, apply=apply))

        closed_active_qs = (
            Cage.objects.filter(status=Cage.Status.CLOSED, current_mice__status=Mouse.Status.ACTIVE)
            .distinct()
            .order_by("cage_id")
        )
        closed_active_cages = list(closed_active_qs)
        reopened_count = 0
        if closed_active_cages:
            labels = ", ".join(cage.cage_id for cage in closed_active_cages[:20])
            suffix = " ..." if len(closed_active_cages) > 20 else ""
            if reopen_closed:
                if apply:
                    reopened_count = closed_active_qs.update(status=Cage.Status.ACTIVE)
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Reopened {reopened_count} closed cage(s) with active current mice: {labels}{suffix}"
                        )
                    )
                else:
                    self.stdout.write(
                        self.style.WARNING(
                            f"Would reopen {len(closed_active_cages)} closed cage(s) with active current mice: "
                            f"{labels}{suffix}"
                        )
                    )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f"{len(closed_active_cages)} closed cage(s) still contain active current mice: "
                        f"{labels}{suffix}. Re-run with --reopen-closed-active-cages if these cages should be active."
                    )
                )

        summary = self._summary(results)
        if not results and not (closed_active_cages and reopen_closed):
            self.stdout.write(self.style.SUCCESS("No cage membership reconciliation issues found."))
            return

        if apply:
            self.stdout.write(
                self.style.SUCCESS(
                    "Reconciled "
                    f"{summary['mice']} mouse(s): closed {summary['closed']} current membership row(s), "
                    f"created {summary['created']} current membership row(s), "
                    f"cleaned {summary['terminal']} terminal mouse cage assignment(s)."
                )
            )
        else:
            self.stdout.write(
                self.style.NOTICE(
                    "Dry run: would reconcile "
                    f"{summary['mice']} mouse(s): close {summary['closed']} current membership row(s), "
                    f"create {summary['created']} current membership row(s), "
                    f"clean {summary['terminal']} terminal mouse cage assignment(s). "
                    "Re-run with --apply to write these repairs."
                )
            )

    def _format_result(self, result: dict, *, apply: bool) -> str:
        prefix = "Repaired" if apply else "Would repair"
        parts = [f"{prefix} {result['mouse_uid']} ({result['status']})"]
        target = result.get("target_cage") or "no current cage"
        parts.append(f"target={target}")
        closed = result.get("closed_membership_cages") or []
        if closed:
            parts.append("close current membership(s): " + ", ".join(closed))
        if result.get("created_membership"):
            parts.append("create matching current membership")
        if result.get("terminal_cleanup"):
            parts.append("clear terminal current cage")
        return "; ".join(parts)

    def _summary(self, results: list[dict]) -> dict[str, int]:
        return {
            "mice": len(results),
            "closed": sum(len(result.get("closed_membership_cages") or []) for result in results),
            "created": sum(1 for result in results if result.get("created_membership")),
            "terminal": sum(1 for result in results if result.get("terminal_cleanup")),
        }
