"""One-shot backfill: StrainLine.owner <- created_by when owner is still empty."""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import F

from colony.models import StrainLine


class Command(BaseCommand):
    help = "Set StrainLine.owner to created_by where owner is null and created_by is set."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print how many rows would be updated; do not write.",
        )

    def handle(self, *args, **options):
        dry = options["dry_run"]
        qs = StrainLine.objects.filter(owner_id__isnull=True, created_by_id__isnull=False)
        n = qs.count()
        if dry:
            self.stdout.write(self.style.NOTICE(f"Would update {n} strain line(s)."))
            return
        updated = qs.update(owner_id=F("created_by_id"))
        self.stdout.write(self.style.SUCCESS(f"Updated {updated} strain line(s)."))
