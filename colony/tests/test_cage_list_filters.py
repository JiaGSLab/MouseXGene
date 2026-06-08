from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from colony.forms import CageForm
from colony.models import Cage, Mouse, StrainLine
from core.models import Project
from users.models import UserProfile


class CageListOwnerFilterTests(TestCase):
    def setUp(self):
        self.user_a = get_user_model().objects.create_user(username="owner_a", password="x")
        self.user_b = get_user_model().objects.create_user(username="owner_b", password="x")
        UserProfile.objects.filter(user=self.user_a).update(role=UserProfile.Role.MANAGER)
        self.client = Client()
        self.client.login(username="owner_a", password="x")
        self.strain = StrainLine.objects.create(line_name="FilterStrain", name="FilterStrain")
        self.project_a = Project.objects.create(name="Project A", owner=self.user_a)
        self.project_b = Project.objects.create(name="Project B", owner=self.user_b)
        self.cage_a = Cage.objects.create(cage_id="OWN-CAGE-A", room="Room101")
        self.cage_b = Cage.objects.create(cage_id="OWN-CAGE-B", room="Room202")
        Mouse.objects.create(
            mouse_uid="M-OWN-A",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.strain,
            project=self.project_a,
            current_cage=self.cage_a,
        )
        Mouse.objects.create(
            mouse_uid="M-OWN-B",
            sex=Mouse.Sex.MALE,
            strain_line=self.strain,
            project=self.project_b,
            current_cage=self.cage_b,
        )

    def test_cage_list_defaults_to_current_owner(self):
        url = reverse("colony:cage_list")
        response = self.client.get(url)
        self.assertContains(response, "OWN-CAGE-A")
        self.assertNotContains(response, "OWN-CAGE-B")

    def test_cage_list_filters_by_owner(self):
        url = reverse("colony:cage_list")
        response = self.client.get(url, {"owner": str(self.user_b.pk)})
        self.assertNotContains(response, "OWN-CAGE-A")
        self.assertContains(response, "OWN-CAGE-B")

    def test_cage_list_all_owners_shows_every_cage(self):
        response = self.client.get(reverse("colony:cage_list"), {"owner": "all"})
        self.assertContains(response, "OWN-CAGE-A")
        self.assertContains(response, "OWN-CAGE-B")

    def test_cage_list_admin_defaults_to_all_owners(self):
        admin = get_user_model().objects.create_user(username="cageadmin", password="x")
        UserProfile.objects.filter(user=admin).update(role=UserProfile.Role.ADMIN)
        self.client.login(username="cageadmin", password="x")
        response = self.client.get(reverse("colony:cage_list"))
        self.assertContains(response, "OWN-CAGE-A")
        self.assertContains(response, "OWN-CAGE-B")

    def test_cage_list_owner_filter_options_include_project_owners(self):
        response = self.client.get(reverse("colony:cage_list"))
        self.assertContains(response, f'value="{self.user_a.pk}"')
        self.assertContains(response, f'value="{self.user_b.pk}"')


class CageFormRoomTests(TestCase):
    def test_save_existing_room_from_dropdown(self):
        Cage.objects.create(cage_id="ROOM-REF", room="Lab-A")
        form = CageForm(
            {
                "cage_id": "ROOM-NEW",
                "room": "Lab-A",
                "room_custom": "",
                "cage_type": Cage.CageType.STANDARD,
                "purpose": Cage.Purpose.HOLDING,
                "status": Cage.Status.ACTIVE,
                "rack": "",
                "position": "",
                "notes": "",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        cage = form.save()
        self.assertEqual(cage.room, "Lab-A")

    def test_save_custom_room(self):
        form = CageForm(
            {
                "cage_id": "ROOM-CUSTOM",
                "room": "__custom__",
                "room_custom": "Basement-3",
                "cage_type": Cage.CageType.STANDARD,
                "purpose": Cage.Purpose.HOLDING,
                "status": Cage.Status.ACTIVE,
                "rack": "",
                "position": "",
                "notes": "",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        cage = form.save()
        self.assertEqual(cage.room, "Basement-3")

    def test_edit_selects_existing_room_in_dropdown(self):
        cage = Cage.objects.create(cage_id="ROOM-EDIT", room="Special-Room")
        form = CageForm(instance=cage)
        self.assertEqual(form.initial.get("room"), "Special-Room")
        room_values = [value for value, _label in form.fields["room"].choices]
        self.assertIn("Special-Room", room_values)
        self.assertIn("__custom__", room_values)
