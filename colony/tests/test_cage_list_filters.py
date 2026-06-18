from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from colony.forms import CageForm
from colony.models import Cage, Mouse, StrainLine
from core.models import Project, ProjectMembership
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

    def test_cage_list_filters_by_cage_use(self):
        breeding_cage = Cage.objects.create(
            cage_id="OWN-BREEDING-CAGE",
            project=self.project_a,
            cage_type=Cage.CageType.BREEDING,
            purpose=Cage.Purpose.BREEDING,
        )

        response = self.client.get(reverse("colony:cage_list"), {"cage_use": Cage.CageUse.BREEDING})

        self.assertContains(response, breeding_cage.cage_id)
        self.assertNotContains(response, "OWN-CAGE-A")
        self.assertContains(response, "Cage Use")
        self.assertContains(response, "row-breeding-active")

    def test_cage_print_shows_owner_from_current_mice_project(self):
        response = self.client.get(reverse("colony:cage_print", args=[self.cage_a.pk]))

        self.assertContains(response, "Owner:")
        self.assertContains(response, self.user_a.username)


class CageFormRoomTests(TestCase):
    def test_save_existing_room_from_dropdown(self):
        Cage.objects.create(cage_id="ROOM-REF", room="Lab-A")
        form = CageForm(
            {
                "cage_id": "ROOM-NEW",
                "room": "Lab-A",
                "room_custom": "",
                "cage_use": Cage.CageUse.HOLDING,
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
                "cage_use": Cage.CageUse.HOLDING,
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


class CageCreateProjectAssignmentTests(TestCase):
    def _cage_data(self, cage_id="PROJECT-CAGE"):
        return {
            "cage_id": cage_id,
            "created_date": "",
            "project": "",
            "colony": "",
            "room": "",
            "room_custom": "",
            "rack": "",
            "position": "",
            "cage_use": Cage.CageUse.HOLDING,
            "status": Cage.Status.ACTIVE,
            "notes": "",
        }

    def test_non_admin_single_project_is_auto_assigned_on_create(self):
        user = get_user_model().objects.create_user(username="singleproject", password="x")
        UserProfile.objects.filter(user=user).update(role=UserProfile.Role.MEMBER)
        project = Project.objects.create(name="Single Project", owner=user)

        form = CageForm(self._cage_data(), user=user)

        self.assertTrue(form.is_valid(), form.errors)
        cage = form.save()
        self.assertEqual(cage.project_id, project.pk)

    def test_non_admin_defaults_own_project_when_also_member_elsewhere(self):
        user = get_user_model().objects.create_user(username="ownandmember", password="x")
        other_owner = get_user_model().objects.create_user(username="otherprojectowner", password="x")
        UserProfile.objects.filter(user=user).update(role=UserProfile.Role.MEMBER)
        own_project = Project.objects.create(name="Owned Project", owner=user)
        other_project = Project.objects.create(name="Other Project", owner=other_owner)
        ProjectMembership.objects.create(project=other_project, user=user, role=ProjectMembership.Role.MANAGER)

        form = CageForm(self._cage_data("OWN-DEFAULT-CAGE"), user=user)

        self.assertTrue(form.is_valid(), form.errors)
        cage = form.save()
        self.assertEqual(cage.project_id, own_project.pk)

    def test_non_admin_multiple_projects_must_select_project(self):
        user = get_user_model().objects.create_user(username="multiproject", password="x")
        UserProfile.objects.filter(user=user).update(role=UserProfile.Role.MEMBER)
        project_a = Project.objects.create(name="Multi Project A", owner=user)
        project_b = Project.objects.create(name="Multi Project B", owner=user)
        ProjectMembership.objects.create(project=project_a, user=user, role=ProjectMembership.Role.MANAGER)
        ProjectMembership.objects.create(project=project_b, user=user, role=ProjectMembership.Role.MEMBER)

        form = CageForm(self._cage_data(), user=user)

        self.assertFalse(form.is_valid())
        self.assertIn("Project is required for new cages", str(form.errors["project"]))

    def test_admin_can_create_cage_without_project(self):
        admin = get_user_model().objects.create_superuser(username="adminproject", password="x")
        UserProfile.objects.filter(user=admin).update(role=UserProfile.Role.ADMIN)

        form = CageForm(self._cage_data(), user=admin)

        self.assertTrue(form.is_valid(), form.errors)
        cage = form.save()
        self.assertIsNone(cage.project_id)


class CageEditFreezeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="cagefreezemember", password="x")
        self.admin = get_user_model().objects.create_superuser(
            username="cagefreezeadmin",
            email="cagefreezeadmin@example.test",
            password="x",
        )
        UserProfile.objects.filter(user=self.user).update(role=UserProfile.Role.MANAGER)
        UserProfile.objects.filter(user=self.admin).update(role=UserProfile.Role.ADMIN)
        self.cage = Cage.objects.create(cage_id="FREEZE-CAGE", room="OldRoom", status=Cage.Status.ACTIVE)
        self.client = Client()

    def test_non_admin_can_edit_location_and_holding_breeding_use_but_not_locked_identity(self):
        self.client.login(username="cagefreezemember", password="x")
        response = self.client.post(
            reverse("colony:cage_edit", args=[self.cage.pk]),
            {
                "cage_id": "FREEZE-CAGE-CHANGED",
                "created_date": "",
                "room": "__custom__",
                "room_custom": "NewRoom",
                "rack": "R2",
                "position": "A1",
                "cage_use": Cage.CageUse.BREEDING,
                "status": Cage.Status.RETIRED,
                "notes": "location corrected",
            },
        )
        self.assertRedirects(response, reverse("colony:cage_detail", args=[self.cage.pk]))
        self.cage.refresh_from_db()
        self.assertEqual(self.cage.cage_id, "FREEZE-CAGE")
        self.assertEqual(self.cage.cage_type, Cage.CageType.BREEDING)
        self.assertEqual(self.cage.purpose, Cage.Purpose.BREEDING)
        self.assertEqual(self.cage.status, Cage.Status.ACTIVE)
        self.assertEqual(self.cage.room, "NewRoom")
        self.assertEqual(self.cage.rack, "R2")

    def test_non_admin_cannot_change_other_cage_uses_without_admin_correction(self):
        self.client.login(username="cagefreezemember", password="x")
        response = self.client.post(
            reverse("colony:cage_edit", args=[self.cage.pk]),
            {
                "cage_id": "FREEZE-CAGE",
                "created_date": "",
                "room": "__custom__",
                "room_custom": "NewRoom",
                "rack": "R2",
                "position": "A1",
                "cage_use": Cage.CageUse.WEANING,
                "status": Cage.Status.ACTIVE,
                "notes": "attempt unsupported use change",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Users with cage edit access can switch Cage Use between Holding and Breeding")
        self.cage.refresh_from_db()
        self.assertEqual(self.cage.cage_type, Cage.CageType.STANDARD)
        self.assertEqual(self.cage.purpose, Cage.Purpose.HOLDING)
        self.assertEqual(self.cage.room, "OldRoom")

    def test_admin_locked_change_requires_reason(self):
        self.client.login(username="cagefreezeadmin", password="x")
        response = self.client.post(
            reverse("colony:cage_edit", args=[self.cage.pk]),
            {
                "admin_correction_unlocked": "1",
                "cage_id": "FREEZE-CAGE-CHANGED",
                "created_date": "",
                "room": "OldRoom",
                "room_custom": "",
                "rack": "",
                "position": "",
                "cage_use": Cage.CageUse.HOLDING,
                "status": Cage.Status.ACTIVE,
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Correction reason is required")
        self.cage.refresh_from_db()
        self.assertEqual(self.cage.cage_id, "FREEZE-CAGE")

    def test_admin_unlock_with_reason_can_change_locked_identity(self):
        self.client.login(username="cagefreezeadmin", password="x")
        response = self.client.post(
            reverse("colony:cage_edit", args=[self.cage.pk]),
            {
                "admin_correction_unlocked": "1",
                "admin_correction_reason": "Admin reviewed correction",
                "cage_id": "FREEZE-CAGE-CHANGED",
                "created_date": "",
                "room": "OldRoom",
                "room_custom": "",
                "rack": "",
                "position": "",
                "cage_use": Cage.CageUse.HOLDING,
                "status": Cage.Status.ACTIVE,
                "notes": "",
            },
        )
        self.assertRedirects(response, reverse("colony:cage_detail", args=[self.cage.pk]))
        self.cage.refresh_from_db()
        self.assertEqual(self.cage.cage_id, "FREEZE-CAGE-CHANGED")
