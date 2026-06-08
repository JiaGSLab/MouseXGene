from django.contrib.auth import get_user_model
from django.test import TestCase

from colony.models import Mouse, MouseGenotypeComponent, StrainLine
from colony.views import _apply_mouse_genotype_rows
from core.models import Project


class MouseGenotypeApplyTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="tgapply", password="x")
        self.strain = StrainLine.objects.create(
            line_name="TgLine",
            name="TgLine",
            expected_loci_template="MyTg",
            expected_loci_config=[
                {
                    "locus_name": "MyTg",
                    "locus_type": "tg_pos_neg",
                    "chromosome_type": "autosomal",
                }
            ],
        )
        self.project = Project.objects.create(name="TgProject", owner=self.user)
        self.mouse = Mouse.objects.create(
            mouse_uid="M-TG-1",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.strain,
            project=self.project,
        )

    def test_apply_pos_neg_without_slash(self):
        updated = _apply_mouse_genotype_rows(
            self.mouse,
            [{"locus": "MyTg", "genotype": "pos"}],
        )
        self.assertEqual(updated, 1)
        comp = MouseGenotypeComponent.objects.get(mouse=self.mouse, locus_name="MyTg")
        self.assertEqual(comp.zygosity, "pos")
        self.assertEqual(comp.allele_display_1, "")
        self.assertEqual(comp.allele_display_2, "")
