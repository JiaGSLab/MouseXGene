from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from breeding.models import Breeding, Litter
from colony.models import Cage, Mouse, StrainLine
from core.models import Project, ProjectMembership
from users.models import UserProfile


class LitterListWeanGroupingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="litter_list_user", password="x")
        UserProfile.objects.filter(user=self.user).update(role=UserProfile.Role.MANAGER)
        self.client.force_login(self.user)
        self.project = Project.objects.create(name="Litter List Project", owner=self.user, is_active=True)
        ProjectMembership.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectMembership.Role.MANAGER,
        )
        self.strain = StrainLine.objects.create(line_name="LitterListStrain", is_active=True)
        self.cage = Cage.objects.create(cage_id="LL-CAGE", status=Cage.Status.ACTIVE)
        self.sire = Mouse.objects.create(
            mouse_uid="LL-SIRE",
            sex=Mouse.Sex.MALE,
            status=Mouse.Status.ACTIVE,
            project=self.project,
            strain_line=self.strain,
            current_cage=self.cage,
        )
        self.dam = Mouse.objects.create(
            mouse_uid="LL-DAM",
            sex=Mouse.Sex.FEMALE,
            status=Mouse.Status.ACTIVE,
            project=self.project,
            strain_line=self.strain,
            current_cage=self.cage,
        )
        self.breeding = Breeding.objects.create(
            breeding_code="LL-BR",
            cage=self.cage,
            male=self.sire,
            female_1=self.dam,
            start_date=date(2026, 1, 1),
        )

    def test_litter_list_groups_not_weaned_before_weaned(self):
        active_litter = Litter.objects.create(
            breeding=self.breeding,
            litter_code="LL-ACTIVE",
            birth_date=date(2026, 2, 1),
        )
        weaned_litter = Litter.objects.create(
            breeding=self.breeding,
            litter_code="LL-WEANED",
            birth_date=date(2026, 1, 1),
            wean_date=date(2026, 1, 22),
            litter_status=Litter.LitterStatus.WEANED,
        )

        response = self.client.get(reverse("litters:litter_list"))

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("litter-section-row--not-weaned", html)
        self.assertIn("litter-section-row--weaned", html)
        self.assertIn("Weaned / closed", html)
        self.assertLess(html.find(active_litter.litter_code), html.find(weaned_litter.litter_code))
