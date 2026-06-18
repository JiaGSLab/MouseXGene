from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from colony.models import Cage, Mouse, StrainLine
from core.models import Project


class MousePickerApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="picker_user", password="x")
        self.client.force_login(self.user)
        self.project = Project.objects.create(name="Picker Project", owner=self.user)
        self.strain = StrainLine.objects.create(line_name="Picker Strain", is_active=True)
        self.active_mouse = Mouse.objects.create(
            mouse_uid="PICK-ACTIVE",
            sex=Mouse.Sex.MALE,
            status=Mouse.Status.ACTIVE,
            project=self.project,
            strain_line=self.strain,
        )
        self.archived_mouse = Mouse.objects.create(
            mouse_uid="PICK-ARCHIVED",
            sex=Mouse.Sex.FEMALE,
            status=Mouse.Status.ARCHIVED,
            project=self.project,
            strain_line=self.strain,
        )

    def test_mouse_picker_defaults_to_active_mice(self):
        response = self.client.get(reverse("mice:mouse_picker_api"))
        self.assertEqual(response.status_code, 200)
        uids = {row["uid"] for row in response.json()["mice"]}
        self.assertIn("PICK-ACTIVE", uids)
        self.assertNotIn("PICK-ARCHIVED", uids)

    def test_mouse_picker_can_include_inactive_mice(self):
        response = self.client.get(reverse("mice:mouse_picker_api"), {"include_inactive": "1"})
        self.assertEqual(response.status_code, 200)
        uids = {row["uid"] for row in response.json()["mice"]}
        self.assertIn("PICK-ACTIVE", uids)
        self.assertIn("PICK-ARCHIVED", uids)

    def test_mouse_picker_accepts_multiple_project_and_owner_filters(self):
        other_user = get_user_model().objects.create_user(username="picker_other")
        other_project = Project.objects.create(name="Picker Other Project", owner=other_user)
        other_mouse = Mouse.objects.create(
            mouse_uid="PICK-OTHER-PROJECT",
            sex=Mouse.Sex.FEMALE,
            status=Mouse.Status.ACTIVE,
            project=other_project,
            strain_line=self.strain,
        )
        excluded_project = Project.objects.create(name="Picker Excluded Project", owner=other_user)
        Mouse.objects.create(
            mouse_uid="PICK-EXCLUDED-PROJECT",
            sex=Mouse.Sex.MALE,
            status=Mouse.Status.ACTIVE,
            project=excluded_project,
            strain_line=self.strain,
        )

        response = self.client.get(
            reverse("mice:mouse_picker_api"),
            {
                "project_ids": f"{self.project.pk},{other_project.pk}",
                "owner_ids": [str(self.user.pk), str(other_user.pk)],
            },
        )

        self.assertEqual(response.status_code, 200)
        uids = {row["uid"] for row in response.json()["mice"]}
        self.assertIn(self.active_mouse.mouse_uid, uids)
        self.assertIn(other_mouse.mouse_uid, uids)
        self.assertNotIn("PICK-EXCLUDED-PROJECT", uids)

    def test_mouse_picker_accepts_multiple_strain_line_filters(self):
        other_strain = StrainLine.objects.create(line_name="Picker Other Strain", is_active=True)
        other_mouse = Mouse.objects.create(
            mouse_uid="PICK-OTHER-STRAIN",
            sex=Mouse.Sex.FEMALE,
            status=Mouse.Status.ACTIVE,
            project=self.project,
            strain_line=other_strain,
        )
        excluded_strain = StrainLine.objects.create(line_name="Picker Excluded Strain", is_active=True)
        Mouse.objects.create(
            mouse_uid="PICK-EXCLUDED-STRAIN",
            sex=Mouse.Sex.MALE,
            status=Mouse.Status.ACTIVE,
            project=self.project,
            strain_line=excluded_strain,
        )

        response = self.client.get(
            reverse("mice:mouse_picker_api"),
            {"strain_line_ids": [str(self.strain.pk), str(other_strain.pk)]},
        )

        self.assertEqual(response.status_code, 200)
        uids = {row["uid"] for row in response.json()["mice"]}
        self.assertIn(self.active_mouse.mouse_uid, uids)
        self.assertIn(other_mouse.mouse_uid, uids)
        self.assertNotIn("PICK-EXCLUDED-STRAIN", uids)

    def test_mouse_picker_keeps_selected_ids_outside_current_filters(self):
        other_user = get_user_model().objects.create_user(username="picker_selected_other")
        other_project = Project.objects.create(name="Picker Selected Other", owner=other_user)
        selected_mouse = Mouse.objects.create(
            mouse_uid="PICK-SELECTED-OUTSIDE-FILTER",
            sex=Mouse.Sex.FEMALE,
            status=Mouse.Status.ACTIVE,
            project=other_project,
            strain_line=self.strain,
        )

        response = self.client.get(
            reverse("mice:mouse_picker_api"),
            {
                "project_ids": str(self.project.pk),
                "selected_ids": str(selected_mouse.pk),
            },
        )

        self.assertEqual(response.status_code, 200)
        uids = {row["uid"] for row in response.json()["mice"]}
        self.assertIn(self.active_mouse.mouse_uid, uids)
        self.assertIn(selected_mouse.mouse_uid, uids)

    def test_mouse_picker_selected_only_returns_selected_mice_without_full_list(self):
        Mouse.objects.create(
            mouse_uid="PICK-UNSELECTED-ACTIVE",
            sex=Mouse.Sex.FEMALE,
            status=Mouse.Status.ACTIVE,
            project=self.project,
            strain_line=self.strain,
        )

        response = self.client.get(
            reverse("mice:mouse_picker_api"),
            {
                "selected_ids": str(self.active_mouse.pk),
                "selected_only": "1",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["uid"] for row in response.json()["mice"]],
            [self.active_mouse.mouse_uid],
        )

    def test_mouse_picker_selected_only_without_selected_ids_is_empty(self):
        response = self.client.get(reverse("mice:mouse_picker_api"), {"selected_only": "1"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mice"], [])

    def test_mouse_strain_map_can_limit_to_selected_ids(self):
        other_strain = StrainLine.objects.create(line_name="Other Picker Strain", is_active=True)
        other_mouse = Mouse.objects.create(
            mouse_uid="PICK-OTHER",
            project=self.project,
            strain_line=other_strain,
        )
        response = self.client.get(
            reverse("mice:mouse_strain_line_map_api"),
            {"ids": f"{self.active_mouse.pk},{other_mouse.pk}"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                str(self.active_mouse.pk): str(self.strain.pk),
                str(other_mouse.pk): str(other_strain.pk),
            },
        )


class CagePickerApiTests(TestCase):
    def setUp(self):
        self.user_a = get_user_model().objects.create_user(username="cage_picker_a", password="x")
        self.user_b = get_user_model().objects.create_user(username="cage_picker_b", password="x")
        self.client.force_login(self.user_a)
        self.project_a = Project.objects.create(name="Cage Picker A", owner=self.user_a)
        self.project_b = Project.objects.create(name="Cage Picker B", owner=self.user_b)
        self.strain = StrainLine.objects.create(line_name="Cage Picker Strain", is_active=True)
        self.empty_cage = Cage.objects.create(cage_id="PICK-EMPTY")
        self.matching_cage = Cage.objects.create(cage_id="PICK-MATCH")
        self.other_cage = Cage.objects.create(cage_id="PICK-OTHER")
        Mouse.objects.create(
            mouse_uid="PICK-CAGE-A",
            project=self.project_a,
            strain_line=self.strain,
            current_cage=self.matching_cage,
        )
        Mouse.objects.create(
            mouse_uid="PICK-CAGE-B",
            project=self.project_b,
            strain_line=self.strain,
            current_cage=self.other_cage,
        )

    def test_cage_picker_project_filter_keeps_empty_cages(self):
        response = self.client.get(reverse("colony:cage_picker_api"), {"project_id": self.project_a.pk})
        self.assertEqual(response.status_code, 200)
        cage_ids = {row["cage_id"] for row in response.json()["cages"]}
        self.assertIn("PICK-EMPTY", cage_ids)
        self.assertIn("PICK-MATCH", cage_ids)
        self.assertNotIn("PICK-OTHER", cage_ids)

    def test_cage_picker_includes_cage_use_metadata(self):
        response = self.client.get(reverse("colony:cage_picker_api"), {"q": "PICK-EMPTY"})
        self.assertEqual(response.status_code, 200)
        row = response.json()["cages"][0]
        self.assertEqual(row["cage_id"], "PICK-EMPTY")
        self.assertEqual(row["cage_use"], Cage.CageUse.HOLDING)
        self.assertEqual(row["cage_use_label"], "Holding")
        self.assertIn("purpose", row)
        self.assertIn("purpose_label", row)
