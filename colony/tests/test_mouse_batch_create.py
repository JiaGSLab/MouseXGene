from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from colony.models import Cage, CageMembership, Mouse, StrainLine
from core.models import Project, ProjectMembership
from users.models import UserProfile


class MouseBatchCreateTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="batch_mouse_user", password="x")
        UserProfile.objects.filter(user=self.user).update(role=UserProfile.Role.MANAGER)
        self.client = Client()
        self.client.login(username="batch_mouse_user", password="x")
        self.strain = StrainLine.objects.create(line_name="BatchLine", name="BatchLine")
        self.project = Project.objects.create(name="Batch Project", owner=self.user, is_active=True)
        ProjectMembership.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectMembership.Role.MANAGER,
        )
        self.cage = Cage.objects.create(cage_id="BATCH-C1", status=Cage.Status.ACTIVE)

    def _shared_payload(self) -> dict[str, str]:
        return {
            "birth_date": "2026-03-01",
            "status": Mouse.Status.ACTIVE,
            "strain_line": str(self.strain.pk),
            "project": str(self.project.pk),
            "current_cage": str(self.cage.pk),
            "batch_row_count": "2",
            "batch_mouse_uid_0": "BATCH-M1",
            "batch_sex_0": Mouse.Sex.MALE,
            "batch_ear_tag_0": "E1",
            "batch_toe_tag_0": "",
            "batch_mouse_uid_1": "BATCH-M2",
            "batch_sex_1": Mouse.Sex.FEMALE,
            "batch_ear_tag_1": "E2",
            "batch_toe_tag_1": "",
            "genotype_row_count": "0",
            "form_action": "create",
        }

    def test_batch_create_two_mice(self):
        response = self.client.post(reverse("mice:mouse_create"), self._shared_payload())
        self.assertRedirects(response, reverse("mice:mouse_list"))
        self.assertTrue(Mouse.objects.filter(mouse_uid="BATCH-M1", ear_tag="E1").exists())
        self.assertTrue(Mouse.objects.filter(mouse_uid="BATCH-M2", ear_tag="E2").exists())
        self.assertEqual(CageMembership.objects.filter(cage=self.cage, is_current=True).count(), 2)

    def test_save_draft_stores_session(self):
        payload = self._shared_payload()
        payload["form_action"] = "draft"
        payload["batch_mouse_uid_1"] = ""
        response = self.client.post(reverse("mice:mouse_create"), payload)
        self.assertEqual(response.status_code, 200)
        session = self.client.session
        draft = session.get("mouse_create_draft_v1")
        self.assertIsNotNone(draft)
        self.assertEqual(draft["batch_rows"][0]["mouse_uid"], "BATCH-M1")

    def test_create_page_has_confirm_and_draft_buttons(self):
        response = self.client.get(reverse("mice:mouse_create"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('name="form_action" value="create"', html)
        self.assertIn('name="form_action" value="draft"', html)
        self.assertIn("window.confirm", html)
        self.assertIn("batch_mouse_uid_0", html)
