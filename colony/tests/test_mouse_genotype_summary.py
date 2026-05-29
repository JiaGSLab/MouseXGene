from django.test import TestCase

from django.contrib.auth import get_user_model

from colony.models import GENOTYPE_SUMMARY_UNCHARACTERIZED, Mouse, MouseGenotypeComponent, StrainLine
from core.models import Project


class MouseGenotypeSummaryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="gtuser", password="x")
        self.strain = StrainLine.objects.create(
            line_name="Compound",
            name="Compound",
            expected_loci_template="Pcbp1mut-KI\nLgr5-CreERT2",
            expected_loci_config=[
                {
                    "locus_name": "Pcbp1mut-KI",
                    "locus_type": "reporter_ki",
                    "chromosome_type": "autosomal",
                },
                {
                    "locus_name": "Lgr5-CreERT2",
                    "locus_type": "cre_transgene",
                    "chromosome_type": "autosomal",
                },
            ],
        )
        self.project = Project.objects.create(name="P1", owner=self.user)
        self.mouse = Mouse.objects.create(
            mouse_uid="M-GT-1",
            strain_line=self.strain,
            project=self.project,
        )

    def test_summary_lists_all_template_loci_with_nd_for_blanks(self):
        MouseGenotypeComponent.objects.create(
            mouse=self.mouse,
            strain_line=self.strain,
            locus_name="Pcbp1mut-KI",
            allele_display_1="+",
            allele_display_2="+",
            sort_order=1,
        )
        summary = self.mouse.compute_genotype_summary()
        self.assertIn("Pcbp1mut-KI:+/+", summary)
        self.assertIn(f"Lgr5-CreERT2:{GENOTYPE_SUMMARY_UNCHARACTERIZED}", summary)

    def test_summary_marks_empty_component_as_nd(self):
        MouseGenotypeComponent.objects.create(
            mouse=self.mouse,
            strain_line=self.strain,
            locus_name="Pcbp1mut-KI",
            sort_order=1,
        )
        MouseGenotypeComponent.objects.create(
            mouse=self.mouse,
            strain_line=self.strain,
            locus_name="Lgr5-CreERT2",
            zygosity="+/-",
            sort_order=2,
        )
        summary = self.mouse.compute_genotype_summary()
        self.assertIn(f"Pcbp1mut-KI:{GENOTYPE_SUMMARY_UNCHARACTERIZED}", summary)
        self.assertIn("Lgr5-CreERT2:+/-", summary)

    def test_summary_without_components_shows_all_template_nd(self):
        summary = self.mouse.compute_genotype_summary()
        self.assertEqual(
            summary,
            f"Pcbp1mut-KI:{GENOTYPE_SUMMARY_UNCHARACTERIZED}; "
            f"Lgr5-CreERT2:{GENOTYPE_SUMMARY_UNCHARACTERIZED}",
        )
