from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from colony.importers import parse_cage_import, parse_mouse_import
from users.import_prefix import apply_import_prefix_to_id


class ImportPrefixHelpersTests(TestCase):
    def test_apply_import_prefix_idempotent(self):
        self.assertEqual(apply_import_prefix_to_id("M001", "JG"), "JG-M001")
        self.assertEqual(apply_import_prefix_to_id("JG-M001", "JG"), "JG-M001")

    def test_parse_cage_import_with_prefix(self):
        csv_content = (
            "cage_id,created_date,room,rack,position,cage_type,purpose,status,notes\n"
            "C001,2026-04-10,Room-A,Rack-1,A1,standard,holding,active,\n"
        )
        f = SimpleUploadedFile("c.csv", csv_content.encode("utf-8"), content_type="text/csv")
        result = parse_cage_import(f, id_prefix="JG")
        self.assertEqual(result.errors, [])
        self.assertEqual(result.rows[0]["cage_id"], "JG-C001")


class ImportPrefixMouseParseTests(TestCase):
    def test_mouse_import_prefixes_new_refs(self):
        csv_content = (
            "mouse_uid,sex,birth_date,status,strain_line,current_cage,project,"
            "ear_tag,toe_tag,origin,coat_color,notes,sire,dam\n"
            "M001,F,2026-01-15,active,TestStrain,C001,Proj,,,,,,,\n"
        )
        f = SimpleUploadedFile("m.csv", csv_content.encode("utf-8"), content_type="text/csv")
        result = parse_mouse_import(f, id_prefix="JG")
        self.assertEqual(result.errors, [])
        row = result.rows[0]
        self.assertEqual(row["mouse_uid"], "JG-M001")
        self.assertEqual(row["current_cage_id"], "JG-C001")
