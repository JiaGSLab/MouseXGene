from datetime import date

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from breeding.models import Breeding
from breeding.views import _breeder_mouse_choices_payload
from colony.models import Cage, Mouse, StrainLine
from core.models import Project, ProjectMembership
from users.models import UserProfile


class BreedingFormUserFilterTests(TestCase):
    def setUp(self):
        self.user_a = get_user_model().objects.create_user(username="breedusera", password="x")
        self.user_b = get_user_model().objects.create_user(username="breeduserb", password="x")
        UserProfile.objects.filter(user=self.user_a).update(
            display_name="Alice Lab",
            role=UserProfile.Role.MANAGER,
        )
        UserProfile.objects.filter(user=self.user_b).update(display_name="Bob Lab")
        self.project_a = Project.objects.create(name="Project A", owner=self.user_a)
        self.project_b = Project.objects.create(name="Project B", owner=self.user_b)
        ProjectMembership.objects.create(
            project=self.project_a,
            user=self.user_a,
            role=ProjectMembership.Role.MANAGER,
        )
        self.strain = StrainLine.objects.create(line_name="FilterStrain", name="FilterStrain")
        self.male_a = Mouse.objects.create(
            mouse_uid="M-FILTER-A",
            sex=Mouse.Sex.MALE,
            project=self.project_a,
            strain_line=self.strain,
        )
        self.female_a = Mouse.objects.create(
            mouse_uid="F-FILTER-A",
            sex=Mouse.Sex.FEMALE,
            project=self.project_a,
            strain_line=self.strain,
        )
        self.female_b = Mouse.objects.create(
            mouse_uid="M-FILTER-B",
            sex=Mouse.Sex.FEMALE,
            project=self.project_b,
            strain_line=self.strain,
        )
        self.cage = Cage.objects.create(cage_id="BREED-FILTER-CAGE")

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
        self.assertIn('id="id_breeding_cage_project_filter"', html)
        self.assertIn('id="id_breeding_cage_owner_filter"', html)
        self.assertIn('id="id_cage_lookup"', html)
        self.assertIn('id="id_mouse_strain_filter" class="filter-control" multiple', html)
        self.assertIn('data-empty-label="All strain lines"', html)
        self.assertIn('id="id_mouse_uid_filter"', html)
        self.assertIn('id="breeder-filter-apply"', html)
        self.assertIn("Auto from selected dams (recommended)", html)
        self.assertIn('id="breeding-type-auto-hint"', html)
        self.assertIn("Select at least one filter, then click Apply filters.", html)

    def test_create_page_includes_project_owner_without_mice(self):
        owner_only = get_user_model().objects.create_user(username="breedowneronly", password="x")
        UserProfile.objects.filter(user=owner_only).update(display_name="Owner Without Mice")
        Project.objects.create(name="Breeding Owner Project", owner=owner_only)
        client = Client()
        client.login(username="breedusera", password="x")
        response = client.get(reverse("breeding:breeding_create"))
        self.assertContains(response, "Owner Without Mice")
        self.assertContains(response, f'value="{owner_only.pk}"')

    def test_edit_page_autoloads_current_breeders(self):
        breeding = Breeding.objects.create(
            breeding_code="BR-FILTER-EDIT",
            cage=self.cage,
            male=self.male_a,
            female_1=self.female_a,
            breeding_type=Breeding.BreedingType.PAIR,
            start_date=date(2026, 1, 1),
            active=True,
        )
        client = Client()
        client.login(username="breedusera", password="x")

        response = client.get(reverse("breeding:breeding_edit", args=[breeding.pk]))

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn(f'<option value="{self.male_a.pk}" selected>', html)
        self.assertIn(f'<option value="{self.female_a.pk}" selected>', html)
        self.assertIn('filters.selected_only = "1";', html)
        self.assertIn("current breeder(s). Add filters to find replacement mice.", html)
