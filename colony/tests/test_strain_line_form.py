import json

from django.test import TestCase

from colony.forms import StrainLineForm
from colony.models import StrainLine


class StrainLineFormLociTests(TestCase):
    def test_save_preserves_locus_type(self):
        line = StrainLine.objects.create(
            line_name="Test Loci",
            name="Test Loci",
            expected_loci_template="Pcbp1mut-KI\nLgr5-CreERT2",
            expected_loci_config=[
                {
                    "locus_name": "Pcbp1mut-KI",
                    "locus_type": "custom",
                    "chromosome_type": "autosomal",
                },
                {
                    "locus_name": "Lgr5-CreERT2",
                    "locus_type": "custom",
                    "chromosome_type": "autosomal",
                },
            ],
        )
        config = [
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
        ]
        data = {
            "name": "Test Loci",
            "species": "mouse",
            "source": "",
            "category": StrainLine.Category.COMPOUND_STRAIN,
            "background": StrainLine.BackgroundPreset.C57BL_6J,
            "expected_loci_template": "Pcbp1mut-KI\nLgr5-CreERT2",
            "expected_loci_config": json.dumps(config),
            "is_active": "on",
            "notes": "",
        }
        form = StrainLineForm(data, instance=line)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        entries = saved.expected_loci_entries()
        self.assertEqual(entries[0]["locus_type"], "reporter_ki")
        self.assertEqual(entries[1]["locus_type"], "cre_transgene")
        self.assertIsInstance(saved.expected_loci_config, list)
