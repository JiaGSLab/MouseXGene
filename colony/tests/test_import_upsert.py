from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from colony.importers import parse_cage_import, parse_mouse_import
from colony.models import Cage, Mouse, StrainLine
from core.models import Project


class ImportUpsertParseTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="importuser", password="x")
        self.strain = StrainLine.objects.create(line_name="TestStrain", name="TestStrain")
        self.project = Project.objects.create(name="P1", owner=self.user)
        self.cage = Cage.objects.create(cage_id="SYJ-GMZ-01")
        self.mouse = Mouse.objects.create(
            mouse_uid="M-UPSERT-1",
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage,
        )

    def test_cage_import_allows_existing_ids_when_update_enabled(self):
        csv_content = (
            "cage_id,created_date,room,rack,position,cage_type,purpose,status,notes\n"
            "SYJ-GMZ-01,2026-04-10,Room-B,Rack-2,B2,standard,holding,active,Updated note\n"
        )
        f = SimpleUploadedFile("c.csv", csv_content.encode("utf-8"), content_type="text/csv")
        result = parse_cage_import(f, update_existing=True)
        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.rows), 1)
        self.assertTrue(result.rows[0]["_update"])
        self.assertEqual(result.rows[0]["room"], "Room-B")

    def test_cage_import_rejects_existing_ids_when_update_disabled(self):
        csv_content = (
            "cage_id,created_date,room,rack,position,cage_type,purpose,status,notes\n"
            "SYJ-GMZ-01,2026-04-10,Room-B,Rack-2,B2,standard,holding,active,Updated note\n"
        )
        f = SimpleUploadedFile("c.csv", csv_content.encode("utf-8"), content_type="text/csv")
        result = parse_cage_import(f, update_existing=False)
        self.assertTrue(any("already used" in err for err in result.errors))

    def test_mouse_import_allows_existing_uid_when_update_enabled(self):
        csv_content = (
            "mouse_uid,sex,birth_date,status,strain_line,current_cage,project,ear_tag,toe_tag,origin,coat_color,notes,breeding_cage,sire,dam\n"
            "M-UPSERT-1,F,2026-01-01,active,TestStrain,SYJ-GMZ-01,P1,ET1,,lab,,Updated mouse,,\n"
        )
        f = SimpleUploadedFile("m.csv", csv_content.encode("utf-8"), content_type="text/csv")
        result = parse_mouse_import(f, update_existing=True)
        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.rows), 1)
        self.assertTrue(result.rows[0]["_update"])
        self.assertEqual(result.rows[0]["ear_tag"], "ET1")

    def test_mouse_import_rejects_existing_uid_when_update_disabled(self):
        csv_content = (
            "mouse_uid,sex,birth_date,status,strain_line,current_cage,project,ear_tag,toe_tag,origin,coat_color,notes,breeding_cage,sire,dam\n"
            "M-UPSERT-1,F,2026-01-01,active,TestStrain,SYJ-GMZ-01,P1,ET1,,lab,,Updated mouse,,\n"
        )
        f = SimpleUploadedFile("m.csv", csv_content.encode("utf-8"), content_type="text/csv")
        result = parse_mouse_import(f, update_existing=False)
        self.assertTrue(any("already used" in err for err in result.errors))
