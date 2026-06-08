from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

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
