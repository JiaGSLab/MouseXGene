from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from colony.models import Cage, Mouse, StrainLine
from core.models import Project


class CagePrintCardTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="print-owner", password="x")
        self.project = Project.objects.create(name="Print Project", owner=self.user, is_active=True)
        self.strain = StrainLine.objects.create(line_name="Print Line", name="Print Line")
        self.cage = Cage.objects.create(
            cage_id="PRINT-CAGE-1",
            room="Room A",
            rack="Rack 1",
            position="P2",
            project=self.project,
            status=Cage.Status.ACTIVE,
        )
        self.mouse = Mouse.objects.create(
            mouse_uid="PRINT-M-1",
            sex=Mouse.Sex.MALE,
            birth_date="2026-06-01",
            status=Mouse.Status.ACTIVE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage,
        )
        self.client.force_login(self.user)

    def test_print_card_shows_project_owner_and_birth_date_without_qr_or_status(self):
        response = self.client.get(reverse("colony:cage_print", args=[self.cage.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PRINT-CAGE-1")
        self.assertContains(response, "<strong>Project:</strong>", html=True)
        self.assertContains(response, "Print Project")
        self.assertContains(response, "<strong>Owner:</strong>", html=True)
        self.assertContains(response, "print-owner")
        self.assertContains(response, "<th>DOB</th>", html=True)
        self.assertContains(response, "2026-06-01")
        self.assertNotContains(response, "qr-placeholder")
        self.assertNotContains(response, ">QR<")
        self.assertNotContains(response, "<strong>Status:</strong>", html=True)
