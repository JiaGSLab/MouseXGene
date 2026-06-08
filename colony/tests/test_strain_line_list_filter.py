from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from colony.models import StrainLine


class StrainLineListFilterTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="strainfilter", password="x")
        self.active_line = StrainLine.objects.create(line_name="AlphaStrain", name="AlphaStrain", is_active=True)
        self.inactive_line = StrainLine.objects.create(line_name="BetaStrain", name="BetaStrain", is_active=False)
        self.client.login(username="strainfilter", password="x")
        self.url = reverse("colony:strain_line_list")

    def test_lists_active_and_archived_in_separate_sections(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Active strain lines")
        self.assertContains(response, "Archived strain lines")
        self.assertContains(response, "AlphaStrain")
        self.assertContains(response, "BetaStrain")
        self.assertContains(response, "Status")
        active_pos = response.content.index(b"Active strain lines")
        archived_pos = response.content.index(b"Archived strain lines")
        alpha_pos = response.content.index(b"AlphaStrain")
        beta_pos = response.content.index(b"BetaStrain")
        self.assertLess(active_pos, archived_pos)
        self.assertLess(active_pos, alpha_pos)
        self.assertLess(archived_pos, beta_pos)

    def test_search_filters_both_sections(self):
        response = self.client.get(self.url, {"q": "Beta"})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "AlphaStrain")
        self.assertContains(response, "BetaStrain")
        self.assertContains(response, "Archived strain lines")

        response = self.client.get(self.url, {"q": "Alpha"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AlphaStrain")
        self.assertNotContains(response, "BetaStrain")
        self.assertNotContains(response, "Archived strain lines")
