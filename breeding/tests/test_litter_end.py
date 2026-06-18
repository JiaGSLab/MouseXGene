from datetime import date

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from breeding.models import Breeding, Litter
from colony.models import Cage, Mouse, StrainLine
from core.models import Project, ProjectMembership
from users.models import UserProfile


class LitterEndWorkflowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="litterend", password="x")
        UserProfile.objects.filter(user=self.user).update(role=UserProfile.Role.MANAGER)
        self.client = Client()
        self.client.login(username="litterend", password="x")
        self.project = Project.objects.create(name="Litter End Project", owner=self.user)
        ProjectMembership.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectMembership.Role.MANAGER,
        )
        self.strain = StrainLine.objects.create(line_name="LitterEndStrain", name="LitterEndStrain")
        self.cage = Cage.objects.create(cage_id="LITTER-END-CAGE")
        self.sire = Mouse.objects.create(
            mouse_uid="LE-SIRE",
            sex=Mouse.Sex.MALE,
            project=self.project,
            strain_line=self.strain,
            current_cage=self.cage,
        )
        self.dam = Mouse.objects.create(
            mouse_uid="LE-DAM",
            sex=Mouse.Sex.FEMALE,
            project=self.project,
            strain_line=self.strain,
            current_cage=self.cage,
        )
        self.breeding = Breeding.objects.create(
            breeding_code="BR-LITTER-END",
            cage=self.cage,
            male=self.sire,
            female_1=self.dam,
            start_date=date(2026, 1, 1),
        )
        self.litter = Litter.objects.create(
            breeding=self.breeding,
            litter_code="LT-END-1",
            birth_date=date(2026, 1, 22),
            total_born=6,
            alive_count=6,
            male_count=3,
            female_count=3,
        )

    def test_end_page_warns_when_litter_is_not_weaned(self):
        response = self.client.get(reverse("litters:litter_end", args=[self.litter.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This litter has not been weaned.")
        self.assertContains(response, "Wean litter first")
        self.assertContains(response, "no pups need to be converted into mouse records")

    def test_unweaned_litter_cannot_be_ended_without_explicit_unweaned_confirmation(self):
        response = self.client.post(
            reverse("litters:litter_end", args=[self.litter.pk]),
            {"confirm_end": "on"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirm that no pups from this unweaned litter need mouse records")
        self.litter.refresh_from_db()
        self.assertEqual(self.litter.litter_status, Litter.LitterStatus.ACTIVE)
        self.assertFalse(self.litter.is_archived)

    def test_unweaned_litter_can_be_ended_after_explicit_confirmation(self):
        response = self.client.post(
            reverse("litters:litter_end", args=[self.litter.pk]),
            {
                "confirm_end": "on",
                "confirm_unweaned": "on",
            },
        )

        self.assertRedirects(response, reverse("litters:litter_detail", args=[self.litter.pk]))
        self.litter.refresh_from_db()
        self.assertEqual(self.litter.litter_status, Litter.LitterStatus.ENDED)
        self.assertTrue(self.litter.is_archived)

    def test_weaned_litter_can_be_ended_without_unweaned_confirmation(self):
        self.litter.wean_date = date(2026, 2, 12)
        self.litter.litter_status = Litter.LitterStatus.WEANED
        self.litter.save(update_fields=["wean_date", "litter_status"])

        response = self.client.post(
            reverse("litters:litter_end", args=[self.litter.pk]),
            {"confirm_end": "on"},
        )

        self.assertRedirects(response, reverse("litters:litter_detail", args=[self.litter.pk]))
        self.litter.refresh_from_db()
        self.assertEqual(self.litter.litter_status, Litter.LitterStatus.ENDED)
        self.assertTrue(self.litter.is_archived)
