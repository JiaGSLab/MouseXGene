from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from colony.models import Cage
from core.models import Project
from core.owner_filters import project_owner_filter_options
from users.models import UserProfile


class ProjectOwnerFilterOptionsTests(TestCase):
    def test_includes_project_owner_without_mice(self):
        owner = get_user_model().objects.create_user(username="empty_project_owner", password="x")
        UserProfile.objects.filter(user=owner).update(display_name="New Lab Owner")
        Project.objects.create(name="Owner Only Project", owner=owner)
        option_ids = [item["pk"] for item in project_owner_filter_options()]
        self.assertIn(owner.pk, option_ids)

    def test_mouse_list_shows_new_project_owner_in_dropdown(self):
        viewer = get_user_model().objects.create_user(username="viewer", password="x")
        UserProfile.objects.filter(user=viewer).update(role=UserProfile.Role.ADMIN)
        new_owner = get_user_model().objects.create_user(username="fresh_owner", password="x")
        UserProfile.objects.filter(user=new_owner).update(display_name="Fresh Owner")
        Project.objects.create(name="Fresh Owner Project", owner=new_owner)
        client = Client()
        client.login(username="viewer", password="x")
        response = client.get(reverse("mice:mouse_list"))
        self.assertContains(response, "Fresh Owner")
        self.assertContains(response, f'value="{new_owner.pk}"')


class DashboardOwnerCageStatsTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_non_admin_home_excludes_unassigned_empty_cages_from_owner_stats(self):
        owner = get_user_model().objects.create_user(username="dashboard_owner", password="x")
        UserProfile.objects.filter(user=owner).update(role=UserProfile.Role.MANAGER)
        project = Project.objects.create(name="Dashboard Owner Project", owner=owner)
        Cage.objects.create(cage_id="DASH-OWNER-CAGE", project=project, status=Cage.Status.ACTIVE)
        Cage.objects.create(cage_id="DASH-LEGACY-CLOSED", status=Cage.Status.CLOSED)

        client = Client()
        client.login(username="dashboard_owner", password="x")
        response = client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["home_owner"], str(owner.pk))
        self.assertEqual(response.context["total_cages"], 1)
        self.assertEqual(response.context["active_cages"], 1)
        self.assertEqual(response.context["inactive_cages"], 0)
