from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from colony.models import Cage, Mouse, StrainLine
from core.models import Project, ProjectMembership
from users.models import UserProfile


class ImportOverwriteConfirmTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = get_user_model().objects.create_user(username="importmgr", password="x")
        UserProfile.objects.filter(user=self.user).update(role=UserProfile.Role.MANAGER)
        self.client.login(username="importmgr", password="x")
        self.strain = StrainLine.objects.create(line_name="TestStrain", name="TestStrain")
        self.project = Project.objects.create(name="P1", owner=self.user)
        ProjectMembership.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectMembership.Role.MANAGER,
        )
        self.cage = Cage.objects.create(cage_id="SYJ-GMZ-01")
        self.mouse = Mouse.objects.create(
            mouse_uid="M-UPSERT-1",
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage,
        )

    def test_cage_import_shows_warning_before_overwrite(self):
        csv_content = (
            "cage_id,created_date,room,rack,position,cage_type,purpose,status,notes\n"
            "SYJ-GMZ-01,2026-04-10,Room-B,Rack-2,B2,standard,holding,active,Updated note\n"
        )
        upload = SimpleUploadedFile("c.csv", csv_content.encode("utf-8"), content_type="text/csv")
        url = reverse("colony:cage_import")
        response = self.client.post(
            url,
            {
                "data_file": upload,
                "update_existing": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Overwrite warning")
        self.assertContains(response, "SYJ-GMZ-01")
        self.assertContains(response, "Confirm overwrite")
        self.cage.refresh_from_db()
        self.assertNotEqual(self.cage.room, "Room-B")

    def test_cage_import_overwrites_after_confirm(self):
        csv_content = (
            "cage_id,created_date,room,rack,position,cage_type,purpose,status,notes\n"
            "SYJ-GMZ-01,2026-04-10,Room-B,Rack-2,B2,standard,holding,active,Updated note\n"
        )
        upload = SimpleUploadedFile("c.csv", csv_content.encode("utf-8"), content_type="text/csv")
        url = reverse("colony:cage_import")
        self.client.post(url, {"data_file": upload, "update_existing": "on"})
        response = self.client.post(url, {"confirm_overwrite": "1"})
        self.assertRedirects(response, reverse("colony:cage_list"))
        self.cage.refresh_from_db()
        self.assertEqual(self.cage.room, "Room-B")
        self.assertEqual(self.cage.notes, "Updated note")

    def test_cage_import_cancel_clears_staged_overwrite(self):
        csv_content = (
            "cage_id,created_date,room,rack,position,cage_type,purpose,status,notes\n"
            "SYJ-GMZ-01,2026-04-10,Room-B,Rack-2,B2,standard,holding,active,Updated note\n"
        )
        upload = SimpleUploadedFile("c.csv", csv_content.encode("utf-8"), content_type="text/csv")
        url = reverse("colony:cage_import")
        self.client.post(url, {"data_file": upload, "update_existing": "on"})
        response = self.client.post(url, {"cancel_overwrite": "1"})
        self.assertRedirects(response, url)
        self.cage.refresh_from_db()
        self.assertNotEqual(self.cage.room, "Room-B")

    def test_mouse_import_shows_warning_before_overwrite(self):
        csv_content = (
            "mouse_uid,sex,birth_date,status,strain_line,current_cage,project,ear_tag,toe_tag,origin,coat_color,notes,breeding_cage,sire,dam\n"
            "M-UPSERT-1,F,2026-01-01,active,TestStrain,SYJ-GMZ-01,P1,ET-NEW,,lab,,Updated mouse,,\n"
        )
        upload = SimpleUploadedFile("m.csv", csv_content.encode("utf-8"), content_type="text/csv")
        url = reverse("mice:mouse_import")
        response = self.client.post(
            url,
            {
                "data_file": upload,
                "update_existing": "on",
                "auto_create_missing_strain_lines": "on",
                "auto_create_missing_projects": "on",
                "auto_create_missing_cages": "on",
                "resolve_pedigree_within_file": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Overwrite warning")
        self.assertContains(response, "M-UPSERT-1")
        self.mouse.refresh_from_db()
        self.assertNotEqual(self.mouse.ear_tag, "ET-NEW")

    def test_mouse_import_overwrites_after_confirm(self):
        csv_content = (
            "mouse_uid,sex,birth_date,status,strain_line,current_cage,project,ear_tag,toe_tag,origin,coat_color,notes,breeding_cage,sire,dam\n"
            "M-UPSERT-1,F,2026-01-01,active,TestStrain,SYJ-GMZ-01,P1,ET-NEW,,lab,,Updated mouse,,\n"
        )
        upload = SimpleUploadedFile("m.csv", csv_content.encode("utf-8"), content_type="text/csv")
        url = reverse("mice:mouse_import")
        self.client.post(
            url,
            {
                "data_file": upload,
                "update_existing": "on",
                "auto_create_missing_strain_lines": "on",
                "auto_create_missing_projects": "on",
                "auto_create_missing_cages": "on",
                "resolve_pedigree_within_file": "on",
            },
        )
        response = self.client.post(url, {"confirm_overwrite": "1"})
        self.assertRedirects(response, reverse("mice:mouse_list"))
        self.mouse.refresh_from_db()
        self.assertEqual(self.mouse.ear_tag, "ET-NEW")
        self.assertEqual(self.mouse.notes, "Updated mouse")
