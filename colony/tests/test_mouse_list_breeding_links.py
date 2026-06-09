from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from breeding.models import Breeding
from colony.models import Cage, Mouse, StrainLine
from core.models import Project, ProjectMembership
from users.models import UserProfile


class MouseListBreedingLinksTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="mouse_breeding_link_user", password="x")
        UserProfile.objects.filter(user=self.user).update(role=UserProfile.Role.MANAGER)
        self.client.force_login(self.user)
        self.project = Project.objects.create(name="Mouse Breeding Link Project", owner=self.user, is_active=True)
        ProjectMembership.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectMembership.Role.MANAGER,
        )
        self.strain = StrainLine.objects.create(line_name="MouseBreedingLinkStrain", is_active=True)
        self.cage = Cage.objects.create(cage_id="MBL-CAGE", status=Cage.Status.ACTIVE)
        self.sire = Mouse.objects.create(
            mouse_uid="MBL-SIRE",
            sex=Mouse.Sex.MALE,
            status=Mouse.Status.ACTIVE,
            project=self.project,
            strain_line=self.strain,
            current_cage=self.cage,
        )
        self.dam = Mouse.objects.create(
            mouse_uid="MBL-DAM",
            sex=Mouse.Sex.FEMALE,
            status=Mouse.Status.ACTIVE,
            project=self.project,
            strain_line=self.strain,
            current_cage=self.cage,
        )
        self.breeding = Breeding.objects.create(
            breeding_code="MBL-BR",
            cage=self.cage,
            male=self.sire,
            female_1=self.dam,
            start_date=date(2026, 1, 1),
            active=True,
        )

    def test_mouse_list_breeding_badge_links_to_breeding_detail(self):
        response = self.client.get(reverse("mice:mouse_list"), {"q": self.sire.mouse_uid})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.breeding.breeding_code)
        self.assertContains(response, f'href="{reverse("breeding:breeding_detail", args=[self.breeding.pk])}"')
