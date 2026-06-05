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

    def test_rename_syncs_legacy_name_fields(self):
        line = StrainLine.objects.create(
            line_name="Old-Line",
            name="Old-Line",
            display_name="Old display",
            key_name="Old-Line",
            expected_loci_template="LocusA",
            expected_loci_config=[
                {
                    "locus_name": "LocusA",
                    "locus_type": "custom",
                    "chromosome_type": "autosomal",
                }
            ],
        )
        data = {
            "name": "New-Line-Name",
            "species": "mouse",
            "source": "",
            "category": StrainLine.Category.COMPOUND_STRAIN,
            "background": StrainLine.BackgroundPreset.C57BL_6J,
            "expected_loci_template": "LocusA",
            "expected_loci_config": json.dumps(line.expected_loci_config),
            "is_active": "on",
            "notes": "",
        }
        form = StrainLineForm(data, instance=line)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        saved.refresh_from_db()
        self.assertEqual(saved.name, "New-Line-Name")
        self.assertEqual(saved.line_name, "New-Line-Name")
        self.assertEqual(saved.display_name, "New-Line-Name")
        self.assertEqual(saved.key_name, "New-Line-Name")

    def test_save_allows_empty_loci(self):
        line = StrainLine.objects.create(
            line_name="Empty-Loci",
            name="Empty-Loci",
            expected_loci_template="LocusA",
            expected_loci_config=[
                {
                    "locus_name": "LocusA",
                    "locus_type": "custom",
                    "chromosome_type": "autosomal",
                }
            ],
        )
        data = {
            "name": "Empty-Loci",
            "species": "mouse",
            "source": "",
            "category": StrainLine.Category.COMPOUND_STRAIN,
            "background": StrainLine.BackgroundPreset.C57BL_6J,
            "expected_loci_template": "",
            "expected_loci_config": "[]",
            "is_active": "on",
            "notes": "",
        }
        form = StrainLineForm(data, instance=line)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.expected_loci_list(), [])
        self.assertEqual(saved.expected_loci_config, [])

    def test_partial_save_with_name_updates_line_name(self):
        line = StrainLine.objects.create(
            line_name="Sync-Me",
            name="Sync-Me",
            expected_loci_template="LocusA",
        )
        line.name = "Sync-Me-Renamed"
        line.save(update_fields=["name"])
        line.refresh_from_db()
        self.assertEqual(line.line_name, "Sync-Me-Renamed")
        self.assertEqual(line.display_name, "Sync-Me-Renamed")
