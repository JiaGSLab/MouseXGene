from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from colony.models import StrainLine


class StrainLineListFilterTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="strainfilter", password="x")
        self.active_line = StrainLine.objects.create(line_name="ActiveStrain", name="ActiveStrain", is_active=True)
        self.inactive_line = StrainLine.objects.create(line_name="InactiveStrain", name="InactiveStrain", is_active=False)
        self.client.login(username="strainfilter", password="x")
        self.url = reverse("colony:strain_line_list")

    def test_default_shows_active_only(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ActiveStrain")
        self.assertNotContains(response, "InactiveStrain")

    def test_inactive_filter_shows_inactive_only(self):
        response = self.client.get(self.url, {"active": "no"})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "ActiveStrain")
        self.assertContains(response, "InactiveStrain")

    def test_all_status_shows_active_and_inactive(self):
        response = self.client.get(self.url, {"active": ""})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ActiveStrain")
        self.assertContains(response, "InactiveStrain")
