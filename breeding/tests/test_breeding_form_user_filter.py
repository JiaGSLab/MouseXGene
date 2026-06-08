from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from breeding.views import _breeder_mouse_choices_payload
from colony.models import Mouse, StrainLine
from core.models import Project
from users.models import UserProfile


class BreedingFormUserFilterTests(TestCase):
    def setUp(self):
        self.user_a = get_user_model().objects.create_user(username="breedusera", password="x")
        self.user_b = get_user_model().objects.create_user(username="breeduserb", password="x")
        UserProfile.objects.filter(user=self.user_a).update(display_name="Alice Lab")
        UserProfile.objects.filter(user=self.user_b).update(display_name="Bob Lab")
        self.project_a = Project.objects.create(name="Project A", owner=self.user_a)
        self.project_b = Project.objects.create(name="Project B", owner=self.user_b)
        self.strain = StrainLine.objects.create(line_name="FilterStrain", name="FilterStrain")
        Mouse.objects.create(
            mouse_uid="M-FILTER-A",
            sex=Mouse.Sex.MALE,
            project=self.project_a,
            strain_line=self.strain,
        )
        Mouse.objects.create(
            mouse_uid="M-FILTER-B",
            sex=Mouse.Sex.FEMALE,
            project=self.project_b,
            strain_line=self.strain,
        )

    def test_breeder_payload_includes_project_owner_fields(self):
        payload = _breeder_mouse_choices_payload()
        by_uid = {row["uid"]: row for row in payload}
        self.assertEqual(by_uid["M-FILTER-A"]["project_owner_id"], self.user_a.pk)
        self.assertEqual(by_uid["M-FILTER-A"]["project_owner_name"], "Alice Lab")
        self.assertEqual(by_uid["M-FILTER-B"]["project_owner_id"], self.user_b.pk)
        self.assertEqual(by_uid["M-FILTER-B"]["project_owner_name"], "Bob Lab")

    def test_create_page_renders_user_filter(self):
        client = Client()
        client.login(username="breedusera", password="x")
        response = client.get(reverse("breeding:breeding_create"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('id="id_mouse_owner_filter"', html)
        self.assertIn("Filter by user", html)
        self.assertIn("Alice Lab", html)
        self.assertIn("Bob Lab", html)
