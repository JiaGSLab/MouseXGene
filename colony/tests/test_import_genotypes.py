from io import BytesIO

import pandas as pd
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from colony.importers import parse_mouse_import
from colony.models import Mouse, MouseGenotypeComponent, StrainLine
from colony.views import MouseImportOptions, _execute_two_pass_mouse_import
from core.models import Project, ProjectMembership
from users.models import UserProfile


class MouseImportGenotypeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="gtimport", password="x")
        UserProfile.objects.filter(user=self.user).update(role=UserProfile.Role.MANAGER)
        self.strain = StrainLine.objects.create(
            line_name="ImportStrain",
            name="ImportStrain",
            expected_loci_template="Foxp3\nCustomGene",
            expected_loci_config=[
                {"locus_name": "Foxp3", "locus_type": "other_custom", "chromosome_type": "autosomal"},
                {"locus_name": "CustomGene", "locus_type": "other_custom", "chromosome_type": "autosomal"},
            ],
        )
        self.project = Project.objects.create(name="ImportProject", owner=self.user)
        ProjectMembership.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectMembership.Role.MANAGER,
        )

    def _parse_xlsx(
        self,
        extra_columns: dict[str, str],
        *,
        extra_genotype_columns: dict[str, str] | None = None,
    ) -> list[dict]:
        row = {
            "mouse_uid": "M-GT-1",
            "sex": "F",
            "birth_date": "2026-01-01",
            "status": "active",
            "strain_line": "ImportStrain",
            "current_cage": "",
            "project": "ImportProject",
            "ear_tag": "",
            "toe_tag": "",
            "origin": "",
            "coat_color": "",
            "notes": "",
            "breeding_cage": "",
            "sire": "",
            "dam": "",
            **(extra_genotype_columns or {}),
            **extra_columns,
        }
        buf = BytesIO()
        pd.DataFrame([row]).to_excel(buf, index=False)
        buf.seek(0)
        buf.name = "mice.xlsx"
        result = parse_mouse_import(buf, update_existing=True)
        self.assertEqual(result.errors, [], result.errors)
        return result.rows

    def test_custom_locus_column_imports_genotype(self):
        rows = self._parse_xlsx({"MyCustomLocus": "+/-", "Foxp3": "fl/fl"})
        comps = rows[0]["genotype_components"]
        by_locus = {c["locus_name"]: c for c in comps}
        self.assertIn("MyCustomLocus", by_locus)
        self.assertEqual(by_locus["MyCustomLocus"]["zygosity_display"], "+/-")
        self.assertEqual(by_locus["Foxp3"]["allele_1"], "fl")

    def test_custom_free_text_genotype_value(self):
        rows = self._parse_xlsx({"Reporter": "mT/mG"})
        comps = rows[0]["genotype_components"]
        self.assertEqual(len(comps), 1)
        self.assertEqual(comps[0]["locus_name"], "Reporter")
        self.assertEqual(comps[0]["zygosity_display"], "mT/mG")

    def test_tg_pos_neg_import_aliases(self):
        rows = self._parse_xlsx({"MyTg": "pos", "OtherTg": "negative"})
        comps = {c["locus_name"]: c for c in rows[0]["genotype_components"]}
        self.assertEqual(comps["MyTg"]["zygosity_display"], "pos")
        self.assertEqual(comps["MyTg"]["allele_1"], "")
        self.assertEqual(comps["OtherTg"]["zygosity_display"], "neg")

    def test_genotype_slot_columns_import(self):
        rows = self._parse_xlsx(
            {},
            extra_genotype_columns={"genotype_1_locus": "SlotGene", "genotype_1_zygosity": "+/-"},
        )
        comps = rows[0]["genotype_components"]
        self.assertEqual(len(comps), 1)
        self.assertEqual(comps[0]["locus_name"], "SlotGene")
        self.assertEqual(comps[0]["zygosity_display"], "+/-")

    def test_empty_locus_column_adds_placeholder(self):
        rows = self._parse_xlsx({"Foxp3": "Cre/+", "CustomGene": ""})
        comps = rows[0]["genotype_components"]
        by_locus = {c["locus_name"]: c for c in comps}
        self.assertEqual(by_locus["Foxp3"]["zygosity_display"], "Cre/+")
        self.assertIn("CustomGene", by_locus)
        self.assertEqual(by_locus["CustomGene"]["zygosity_display"], "")

    def test_execute_import_creates_genotype_components(self):
        rows = self._parse_xlsx({"CustomGene": "Het", "Foxp3": "Cre/+"})
        stats = _execute_two_pass_mouse_import(
            rows,
            options=MouseImportOptions(
                auto_create_missing_strain_lines=True,
                auto_create_missing_projects=True,
                auto_create_missing_cages=True,
                resolve_pedigree_within_file=True,
            ),
            import_date=timezone.localdate(),
            acting_user=self.user,
        )
        self.assertEqual(stats["genotype_rows_created"], 0)
        self.assertEqual(stats["genotype_rows_updated"], 2)
        mouse = Mouse.objects.get(mouse_uid="M-GT-1")
        comps = {
            c.locus_name: c.zygosity
            for c in MouseGenotypeComponent.objects.filter(mouse=mouse)
        }
        self.assertEqual(comps["CustomGene"], "+/-")
        self.assertEqual(comps["Foxp3"], "Cre/+")

    def test_execute_import_seeds_empty_strain_template_loci(self):
        rows = self._parse_xlsx({"Foxp3": "Cre/+"})
        _execute_two_pass_mouse_import(
            rows,
            options=MouseImportOptions(
                auto_create_missing_strain_lines=True,
                auto_create_missing_projects=True,
                auto_create_missing_cages=True,
                resolve_pedigree_within_file=True,
            ),
            import_date=timezone.localdate(),
            acting_user=self.user,
        )
        mouse = Mouse.objects.get(mouse_uid="M-GT-1")
        comps = {
            c.locus_name: c.zygosity
            for c in MouseGenotypeComponent.objects.filter(mouse=mouse)
        }
        self.assertEqual(comps["Foxp3"], "Cre/+")
        self.assertIn("CustomGene", comps)
        self.assertEqual(comps["CustomGene"], "")

    def test_execute_import_keeps_empty_locus_column_placeholder(self):
        rows = self._parse_xlsx({"Foxp3": "Cre/+", "CustomGene": ""})
        _execute_two_pass_mouse_import(
            rows,
            options=MouseImportOptions(
                auto_create_missing_strain_lines=True,
                auto_create_missing_projects=True,
                auto_create_missing_cages=True,
                resolve_pedigree_within_file=True,
            ),
            import_date=timezone.localdate(),
            acting_user=self.user,
        )
        mouse = Mouse.objects.get(mouse_uid="M-GT-1")
        comps = {
            c.locus_name: c.zygosity
            for c in MouseGenotypeComponent.objects.filter(mouse=mouse)
        }
        self.assertEqual(comps["Foxp3"], "Cre/+")
        self.assertEqual(comps["CustomGene"], "")

    def test_execute_import_merges_construct_suffix_with_same_logical_locus(self):
        mouse = Mouse.objects.create(
            mouse_uid="M-GT-EXIST",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.strain,
            project=self.project,
        )
        mouse.ensure_template_genotype_components(include_strain_template=True)
        rows = self._parse_xlsx({"Foxp3 flox": "+/+"})
        rows[0]["mouse_uid"] = "M-GT-EXIST"
        rows[0]["_update"] = True
        stats = _execute_two_pass_mouse_import(
            rows,
            options=MouseImportOptions(
                auto_create_missing_strain_lines=True,
                auto_create_missing_projects=True,
                auto_create_missing_cages=True,
                resolve_pedigree_within_file=True,
            ),
            import_date=timezone.localdate(),
            acting_user=self.user,
        )
        self.assertEqual(stats["genotype_rows_updated"], 1)
        self.assertEqual(stats["genotype_rows_created"], 0)
        template_row = MouseGenotypeComponent.objects.get(mouse=mouse, locus_name="Foxp3")
        self.assertEqual(template_row.zygosity, "+/+")
        self.assertFalse(MouseGenotypeComponent.objects.filter(mouse=mouse, locus_name="Foxp3 flox").exists())

    def test_multi_strain_file_skips_unrelated_empty_locus_columns(self):
        strain_b = StrainLine.objects.create(
            line_name="OtherStrain",
            name="OtherStrain",
            expected_loci_template="OtherGene",
            expected_loci_config=[
                {"locus_name": "OtherGene", "locus_type": "other_custom", "chromosome_type": "autosomal"},
            ],
        )
        self.assertIsNotNone(strain_b.pk)
        row_a = {
            "mouse_uid": "M-GT-A",
            "sex": "F",
            "birth_date": "2026-01-01",
            "status": "active",
            "strain_line": "ImportStrain",
            "current_cage": "",
            "project": "ImportProject",
            "ear_tag": "",
            "toe_tag": "",
            "origin": "",
            "coat_color": "",
            "notes": "",
            "breeding_cage": "",
            "sire": "",
            "dam": "",
            "Foxp3": "Cre/+",
            "CustomGene": "",
            "OtherGene": "",
        }
        row_b = {
            "mouse_uid": "M-GT-B",
            "sex": "M",
            "birth_date": "2026-01-02",
            "status": "active",
            "strain_line": "OtherStrain",
            "current_cage": "",
            "project": "ImportProject",
            "ear_tag": "",
            "toe_tag": "",
            "origin": "",
            "coat_color": "",
            "notes": "",
            "breeding_cage": "",
            "sire": "",
            "dam": "",
            "Foxp3": "",
            "CustomGene": "",
            "OtherGene": "+/-",
        }
        buf = BytesIO()
        pd.DataFrame([row_a, row_b]).to_excel(buf, index=False)
        buf.seek(0)
        buf.name = "mice.xlsx"
        result = parse_mouse_import(buf, update_existing=True)
        self.assertEqual(result.errors, [], result.errors)
        by_mouse = {row["mouse_uid"]: row for row in result.rows}
        loci_a = {c["locus_name"] for c in by_mouse["M-GT-A"]["genotype_components"]}
        loci_b = {c["locus_name"] for c in by_mouse["M-GT-B"]["genotype_components"]}
        self.assertEqual(loci_a, {"Foxp3", "CustomGene"})
        self.assertEqual(loci_b, {"OtherGene"})

    def test_execute_import_updates_row_when_locus_name_matches_exactly(self):
        mouse = Mouse.objects.create(
            mouse_uid="M-GT-EXIST2",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.strain,
            project=self.project,
        )
        mouse.ensure_template_genotype_components(include_strain_template=True)
        rows = self._parse_xlsx({"Foxp3": "+/+"})
        rows[0]["mouse_uid"] = "M-GT-EXIST2"
        rows[0]["_update"] = True
        stats = _execute_two_pass_mouse_import(
            rows,
            options=MouseImportOptions(
                auto_create_missing_strain_lines=True,
                auto_create_missing_projects=True,
                auto_create_missing_cages=True,
                resolve_pedigree_within_file=True,
            ),
            import_date=timezone.localdate(),
            acting_user=self.user,
        )
        self.assertEqual(stats["genotype_rows_updated"], 1)
        self.assertEqual(stats["genotype_rows_created"], 0)
        comp = MouseGenotypeComponent.objects.get(mouse=mouse, locus_name="Foxp3")
        self.assertEqual(comp.zygosity, "+/+")
