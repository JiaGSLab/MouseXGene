from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from colony.models import Mouse, StrainLine
from core.models import Project
from users.models import UserProfile


class MouseListOwnerFilterTests(TestCase):
    def setUp(self):
        self.user_a = get_user_model().objects.create_user(username="mouse_owner_a", password="x")
        self.user_b = get_user_model().objects.create_user(username="mouse_owner_b", password="x")
        self.client.login(username="mouse_owner_a", password="x")
        self.strain = StrainLine.objects.create(line_name="OwnerStrain", name="OwnerStrain")
        self.project_a = Project.objects.create(name="Mouse Project A", owner=self.user_a)
        self.project_b = Project.objects.create(name="Mouse Project B", owner=self.user_b)
        Mouse.objects.create(
            mouse_uid="M-OWNER-A",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.strain,
            project=self.project_a,
        )
        Mouse.objects.create(
            mouse_uid="M-OWNER-B",
            sex=Mouse.Sex.MALE,
            strain_line=self.strain,
            project=self.project_b,
        )
        self.url = reverse("mice:mouse_list")

    def test_defaults_to_current_owner(self):
        response = self.client.get(self.url)
        self.assertContains(response, "M-OWNER-A")
        self.assertNotContains(response, "M-OWNER-B")

    def test_all_owners_shows_every_mouse(self):
        response = self.client.get(self.url, {"owner": "all"})
        self.assertContains(response, "M-OWNER-A")
        self.assertContains(response, "M-OWNER-B")

    def test_explicit_owner_filter_applies_with_strain_line_filter(self):
        response = self.client.get(
            self.url,
            {
                "strain_line_id": self.strain.pk,
                "owner": self.user_a.pk,
            },
        )

        self.assertContains(response, "M-OWNER-A")
        self.assertNotContains(response, "M-OWNER-B")

    def test_strain_line_filter_without_owner_shows_all_owners(self):
        response = self.client.get(self.url, {"strain_line_id": self.strain.pk})

        self.assertContains(response, "M-OWNER-A")
        self.assertContains(response, "M-OWNER-B")

    def test_admin_defaults_to_all_owners(self):
        admin = get_user_model().objects.create_user(username="mouseadmin", password="x")
        UserProfile.objects.filter(user=admin).update(role=UserProfile.Role.ADMIN)
        self.client.login(username="mouseadmin", password="x")
        response = self.client.get(self.url)
        self.assertContains(response, "M-OWNER-A")
        self.assertContains(response, "M-OWNER-B")


class HomeOwnerScopeTests(TestCase):
    def setUp(self):
        self.user_a = get_user_model().objects.create_user(username="home_owner_a", password="x")
        self.user_b = get_user_model().objects.create_user(username="home_owner_b", password="x")
        self.strain = StrainLine.objects.create(line_name="HomeStrain", name="HomeStrain")
        self.project_a = Project.objects.create(name="Home Project A", owner=self.user_a)
        self.project_b = Project.objects.create(name="Home Project B", owner=self.user_b)
        Mouse.objects.create(
            mouse_uid="M-HOME-A",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.strain,
            project=self.project_a,
        )
        Mouse.objects.create(
            mouse_uid="M-HOME-B",
            sex=Mouse.Sex.MALE,
            strain_line=self.strain,
            project=self.project_b,
        )

    def test_home_shows_only_current_users_mice(self):
        self.client.login(username="home_owner_a", password="x")
        response = self.client.get(reverse("home"))
        self.assertContains(response, "M-HOME-A")
        self.assertNotContains(response, "M-HOME-B")

    def test_home_all_owners_shows_every_mouse(self):
        self.client.login(username="home_owner_a", password="x")
        response = self.client.get(reverse("home"), {"owner": "all"})
        self.assertContains(response, "M-HOME-A")
        self.assertContains(response, "M-HOME-B")

    def test_home_admin_sees_all_mice(self):
        admin = get_user_model().objects.create_user(username="homeadmin", password="x")
        UserProfile.objects.filter(user=admin).update(role=UserProfile.Role.ADMIN)
        self.client.login(username="homeadmin", password="x")
        response = self.client.get(reverse("home"))
        self.assertContains(response, "M-HOME-A")
        self.assertContains(response, "M-HOME-B")
        self.assertNotContains(response, 'id="home-owner-filter"')
