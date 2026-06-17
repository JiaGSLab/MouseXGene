"""Repair active breeding members whose current cage is out of sync."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from breeding.consistency import active_breeding_cage_mismatches
from breeding.models import Breeding
from colony.cage_lifecycle import sync_breeding_member_cages


class Command(BaseCommand):
    help = "Move active breeding members into their breeding cage when Mouse.current_cage is out of sync."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report mismatches without changing cages.",
        )
        parser.add_argument(
            "--breeding-code",
            help="Limit repair to one breeding code.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        breeding_code = options.get("breeding_code")
        qs = (
            Breeding.objects.filter(active=True)
            .exclude(status=Breeding.Status.CLOSED)
            .exclude(cage_id__isnull=True)
            .select_related("cage")
            .order_by("breeding_code")
        )
        if breeding_code:
            qs = qs.filter(breeding_code=breeding_code)

        mismatches = active_breeding_cage_mismatches(qs)
        if not mismatches:
            self.stdout.write(self.style.SUCCESS("No active breeding cage mismatches found."))
            return

        total_rows = sum(len(getattr(breeding, "cage_mismatch_rows", [])) for breeding in mismatches)
        for breeding in mismatches:
            rows = getattr(breeding, "cage_mismatch_rows", [])
            details = ", ".join(
                f"{row['mouse'].mouse_uid}: {getattr(row['current_cage'], 'cage_id', 'no cage')} -> {breeding.cage.cage_id}"
                for row in rows
            )
            self.stdout.write(f"{breeding.breeding_code}: {details}")

        if dry_run:
            self.stdout.write(self.style.NOTICE(f"Would repair {total_rows} breeder cage assignment(s)."))
            return

        moved_total = 0
        for breeding in mismatches:
            moved_total += sync_breeding_member_cages(breeding)

        remaining = active_breeding_cage_mismatches(qs)
        if remaining:
            remaining_codes = ", ".join(breeding.breeding_code for breeding in remaining)
            raise SystemExit(f"Repair incomplete; remaining mismatch breeding(s): {remaining_codes}")

        self.stdout.write(self.style.SUCCESS(f"Repaired {moved_total} breeder cage assignment(s)."))
