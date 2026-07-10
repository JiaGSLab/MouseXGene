from datetime import date

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from colony.models import Cage, CageMembership, Mouse, MouseGenotypeComponent, StrainLine
from core.models import Project


class ColonyDatabaseIntegrityTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username="db-integrity")
        self.project = Project.objects.create(name="DB Integrity", owner=user)
        self.strain = StrainLine.objects.create(line_name="DB Integrity Line")
        self.mouse = Mouse.objects.create(
            mouse_uid="DB-MOUSE",
            project=self.project,
            strain_line=self.strain,
        )

    def test_mouse_has_only_one_current_membership(self):
        cage_a = Cage.objects.create(cage_id="DB-CAGE-A", project=self.project)
        cage_b = Cage.objects.create(cage_id="DB-CAGE-B", project=self.project)
        CageMembership.objects.create(mouse=self.mouse, cage=cage_a, start_date=date(2026, 1, 1))

        with self.assertRaises(IntegrityError), transaction.atomic():
            CageMembership.objects.create(mouse=self.mouse, cage=cage_b, start_date=date(2026, 2, 1))

    def test_construct_suffix_cannot_duplicate_logical_locus(self):
        MouseGenotypeComponent.objects.create(
            mouse=self.mouse,
            strain_line=self.strain,
            locus_name="Gpnmb",
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            MouseGenotypeComponent.objects.create(
                mouse=self.mouse,
                strain_line=self.strain,
                locus_name="Gpnmb flox",
            )

    def test_template_backfill_reuses_existing_logical_locus(self):
        self.strain.expected_loci_config = [{"locus_name": "Gpnmb", "chromosome_type": "autosomal"}]
        self.strain.save(update_fields=["expected_loci_config", "updated_at"])
        MouseGenotypeComponent.objects.create(
            mouse=self.mouse,
            strain_line=self.strain,
            locus_name="Gpnmb flox",
        )

        created = self.mouse.ensure_template_genotype_components()

        self.assertEqual(created, 0)
        self.assertEqual(self.mouse.genotype_components.count(), 1)
