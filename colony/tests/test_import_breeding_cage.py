from datetime import date
from io import BytesIO

import pandas as pd
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from breeding.models import Breeding
from colony.importers import parse_mouse_import
from colony.models import Cage, Mouse, StrainLine
from colony.views import MouseImportExecutionError, MouseImportOptions, _execute_two_pass_mouse_import
from core.models import Project, ProjectMembership
from users.models import UserProfile


class BreedingCageImportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="bcimport", password="x")
        UserProfile.objects.filter(user=self.user).update(role=UserProfile.Role.MANAGER)
        self.strain = StrainLine.objects.create(line_name="BC-Strain", name="BC-Strain")
        self.project = Project.objects.create(name="BC-Project", owner=self.user)
        ProjectMembership.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectMembership.Role.MANAGER,
        )
        self.breeding_cage = Cage.objects.create(cage_id="BC-CAGE-1", purpose=Cage.Purpose.BREEDING)
        self.sire = Mouse.objects.create(
            mouse_uid="BC-SIRE",
            sex=Mouse.Sex.MALE,
            strain_line=self.strain,
            project=self.project,
        )
        self.dam1 = Mouse.objects.create(
            mouse_uid="BC-DAM-1",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.strain,
            project=self.project,
        )
        self.dam2 = Mouse.objects.create(
            mouse_uid="BC-DAM-2",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.strain,
            project=self.project,
        )
        self.breeding = Breeding.objects.create(
            breeding_code="BC-BR-1",
            cage=self.breeding_cage,
            male=self.sire,
            female_1=self.dam1,
            female_2=self.dam2,
            start_date=date(2026, 1, 1),
            active=True,
        )

    def _parse_xlsx(self, extra_columns: dict[str, str] | None = None) -> list[dict]:
        row = {
            "mouse_uid": "M-BC-1",
            "sex": "F",
            "birth_date": "2026-02-01",
            "status": "active",
            "strain_line": "BC-Strain",
            "current_cage": "",
            "project": "BC-Project",
            "ear_tag": "",
            "toe_tag": "",
            "origin": "",
            "coat_color": "",
            "notes": "",
            "breeding_cage": "BC-CAGE-1",
            "sire": "",
            "dam": "",
            **(extra_columns or {}),
        }
        buf = BytesIO()
        pd.DataFrame([row]).to_excel(buf, index=False)
        buf.seek(0)
        buf.name = "mice.xlsx"
        result = parse_mouse_import(buf, update_existing=True)
        self.assertEqual(result.errors, [], result.errors)
        return result.rows

    def test_parse_reads_breeding_cage_column(self):
        rows = self._parse_xlsx()
        self.assertEqual(rows[0]["breeding_cage_id"], "BC-CAGE-1")

    def test_execute_import_links_breeding_and_sire(self):
        rows = self._parse_xlsx()
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
        mouse = Mouse.objects.get(mouse_uid="M-BC-1")
        self.assertEqual(mouse.source_breeding_id, self.breeding.pk)
        self.assertEqual(mouse.sire_id, self.sire.pk)
        self.assertIsNone(mouse.dam_id)

    def test_execute_import_rejects_dam_when_breeding_cage_set(self):
        rows = self._parse_xlsx({"dam": "BC-DAM-1"})
        with self.assertRaises(MouseImportExecutionError):
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

    def test_legacy_sire_dam_still_import(self):
        csv_content = (
            "mouse_uid,sex,birth_date,status,strain_line,current_cage,project,"
            "ear_tag,toe_tag,origin,coat_color,notes,breeding_cage,sire,dam\n"
            "M-LEG-1,M,2026-02-01,active,BC-Strain,,BC-Project,,,,,,,BC-SIRE,BC-DAM-1\n"
        )
        upload = SimpleUploadedFile("m.csv", csv_content.encode("utf-8"), content_type="text/csv")
        result = parse_mouse_import(upload, update_existing=True)
        self.assertEqual(result.errors, [])
        _execute_two_pass_mouse_import(
            result.rows,
            options=MouseImportOptions(
                auto_create_missing_strain_lines=True,
                auto_create_missing_projects=True,
                auto_create_missing_cages=True,
                resolve_pedigree_within_file=True,
            ),
            import_date=timezone.localdate(),
            acting_user=self.user,
        )
        mouse = Mouse.objects.get(mouse_uid="M-LEG-1")
        self.assertIsNone(mouse.source_breeding_id)
        self.assertEqual(mouse.sire_id, self.sire.pk)
        self.assertEqual(mouse.dam_id, self.dam1.pk)
