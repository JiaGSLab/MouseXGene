from datetime import date

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from breeding.models import Breeding, Litter
from breeding.views import _litter_wean_initial_pup_count
from colony.models import Cage, Mouse, StrainLine
from core.models import Project, ProjectMembership
from users.models import UserProfile


class LitterWeanPageTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="weanuser", password="x")
        UserProfile.objects.filter(user=self.user).update(role=UserProfile.Role.MANAGER)
        self.client = Client()
        self.client.login(username="weanuser", password="x")
        self.project = Project.objects.create(name="WeanProject", owner=self.user)
        ProjectMembership.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectMembership.Role.MANAGER,
        )
        self.strain = StrainLine.objects.create(line_name="WeanStrain", name="WeanStrain")
        self.cage = Cage.objects.create(cage_id="WEAN-CAGE-1", purpose=Cage.Purpose.HOLDING)
        self.other_cage = Cage.objects.create(cage_id="WEAN-CAGE-2", purpose=Cage.Purpose.HOLDING)
        self.sire = Mouse.objects.create(
            mouse_uid="M-WEAN-S",
            sex=Mouse.Sex.MALE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage,
        )
        self.dam = Mouse.objects.create(
            mouse_uid="M-WEAN-D",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage,
        )
        self.breeding = Breeding.objects.create(
            breeding_code="BR-WEAN-1",
            cage=self.cage,
            male=self.sire,
            female_1=self.dam,
            start_date=date(2026, 1, 1),
        )
        self.litter = Litter.objects.create(
            breeding=self.breeding,
            litter_code="LT-WEAN-1",
            birth_date=date(2026, 1, 22),
            total_born=5,
            alive_count=3,
        )

    def test_initial_pup_count_matches_total_born(self):
        self.assertEqual(_litter_wean_initial_pup_count(self.litter), 5)

    def test_wean_page_renders_filters_and_pup_rows(self):
        response = self.client.get(reverse("litters:litter_wean", args=[self.litter.pk]))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('id="id_wean_project_filter"', html)
        self.assertIn('id="id_target_cage_lookup"', html)
        self.assertIn("Create cage", html)
        self.assertIn('value="5"', html)
        self.assertEqual(html.count('class="card pup-card"'), 5)

    def test_refresh_forms_updates_pup_count(self):
        url = reverse("litters:litter_wean", args=[self.litter.pk])
        response = self.client.post(
            url,
            {
                "wean_date": "2026-02-12",
                "number_of_pups": "2",
                "project_assignment_mode": "sire",
                "refresh_forms": "1",
                "pups-0-mouse_uid": "keep-me",
                "pups-0-sex": "M",
            },
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertEqual(html.count('class="card pup-card"'), 2)
        self.assertIn('value="keep-me"', html)

    def test_refresh_increases_pup_rows(self):
        url = reverse("litters:litter_wean", args=[self.litter.pk])
        response = self.client.post(
            url,
            {
                "wean_date": "2026-02-12",
                "number_of_pups": "4",
                "project_assignment_mode": "sire",
                "refresh_forms": "1",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode().count('class="card pup-card"'), 4)

    def test_apply_cage_filter_controls_persist_on_refresh(self):
        url = reverse("litters:litter_wean", args=[self.litter.pk])
        response = self.client.post(
            url,
            {
                "wean_date": "2026-02-12",
                "number_of_pups": "5",
                "project_assignment_mode": "sire",
                "wean_cage_project_filter": str(self.project.pk),
                "wean_cage_owner_filter": str(self.user.pk),
                "refresh_forms": "1",
            },
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("Apply cage filter", html)
        self.assertIn(f'value="{self.project.pk}" selected', html)

    def test_target_cage_lookup_resolves_cage(self):
        url = reverse("litters:litter_wean", args=[self.litter.pk])
        response = self.client.post(
            url,
            {
                "wean_date": "2026-02-12",
                "number_of_pups": "1",
                "project_assignment_mode": "sire",
                "target_cage_lookup": self.other_cage.cage_id,
                "pups-0-mouse_uid": "M-WEAN-PUP-1",
                "pups-0-sex": "M",
                "pups-0-ear_tag": "",
                "pups-0-coat_color": "",
                "pups-0-notes": "",
            },
        )
        self.assertRedirects(response, reverse("litters:litter_detail", args=[self.litter.pk]))
        self.assertTrue(Mouse.objects.filter(mouse_uid="M-WEAN-PUP-1", current_cage=self.other_cage).exists())
