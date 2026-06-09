from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from colony.models import Mouse, StrainLine
from core.models import Project


class MouseListSearchTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="mouse_search_user", password="x")
        self.client.force_login(self.user)
        self.project = Project.objects.create(name="Search Project", owner=self.user, is_active=True)
        self.strain = StrainLine.objects.create(line_name="Search Strain", is_active=True)

    def test_mouse_list_search_includes_coat_color_and_notes(self):
        Mouse.objects.create(
            mouse_uid="SEARCH-COAT",
            sex=Mouse.Sex.MALE,
            status=Mouse.Status.ACTIVE,
            project=self.project,
            strain_line=self.strain,
            coat_color="agouti",
        )
        Mouse.objects.create(
            mouse_uid="SEARCH-NOTES",
            sex=Mouse.Sex.FEMALE,
            status=Mouse.Status.ACTIVE,
            project=self.project,
            strain_line=self.strain,
            notes="rare phenotype observation",
        )

        coat_response = self.client.get(reverse("mice:mouse_list"), {"q": "agouti"})
        notes_response = self.client.get(reverse("mice:mouse_list"), {"q": "rare"})

        self.assertContains(coat_response, "SEARCH-COAT")
        self.assertContains(notes_response, "SEARCH-NOTES")
