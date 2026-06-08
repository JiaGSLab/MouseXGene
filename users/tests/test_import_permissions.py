from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from users.models import UserProfile
from users.permissions import can_import, can_manage_breeding


class ImportPermissionTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(username="labadmin", password="x")
        self.manager = get_user_model().objects.create_user(username="labmanager", password="x")
        UserProfile.objects.filter(user=self.admin).update(role=UserProfile.Role.ADMIN)
        UserProfile.objects.filter(user=self.manager).update(role=UserProfile.Role.MANAGER)
        self.admin = get_user_model().objects.select_related("profile").get(pk=self.admin.pk)
        self.manager = get_user_model().objects.select_related("profile").get(pk=self.manager.pk)

    def test_can_import_is_admin_only(self):
        self.assertTrue(can_import(self.admin))
        self.assertFalse(can_import(self.manager))

    def test_manager_can_still_manage_breeding(self):
        self.assertTrue(can_manage_breeding(self.manager))

    def test_manager_denied_mouse_import_page(self):
        client = Client()
        client.force_login(self.manager)
        response = client.get(reverse("mice:mouse_import"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))

    def test_admin_can_open_mouse_import_page(self):
        client = Client()
        client.login(username="labadmin", password="x")
        response = client.get(reverse("mice:mouse_import"))
        self.assertEqual(response.status_code, 200)
