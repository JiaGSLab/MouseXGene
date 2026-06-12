from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from breeding.models import Breeding
from colony.models import Cage, CageMembership, Mouse, StrainLine
from core.models import Project, ProjectMembership
from users.models import UserProfile


class BreedingEndWorkflowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="endbreeding", password="x")
        UserProfile.objects.filter(user=self.user).update(role=UserProfile.Role.MANAGER)
        self.client = Client()
        self.client.login(username="endbreeding", password="x")
        self.project = Project.objects.create(name="End Breeding Project", owner=self.user)
        ProjectMembership.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectMembership.Role.MANAGER,
        )
        self.strain = StrainLine.objects.create(line_name="EndBreedStrain", name="EndBreedStrain")
        self.breeding_cage = Cage.objects.create(
            cage_id="END-BR-CAGE",
            cage_type=Cage.CageType.BREEDING,
            purpose=Cage.Purpose.BREEDING,
        )
        self.male_cage = Cage.objects.create(cage_id="END-MALE-HOLD")
        self.female_cage = Cage.objects.create(cage_id="END-FEMALE-HOLD")
        self.sire = Mouse.objects.create(
            mouse_uid="END-SIRE",
            sex=Mouse.Sex.MALE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.breeding_cage,
        )
        self.dam = Mouse.objects.create(
            mouse_uid="END-DAM",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.breeding_cage,
        )
        for mouse in (self.sire, self.dam):
            CageMembership.objects.create(
                mouse=mouse,
                cage=self.breeding_cage,
                start_date="2026-01-01",
                is_current=True,
                reason="Breeding setup",
            )
        self.breeding = Breeding.objects.create(
            breeding_code="BR-END-WF",
            cage=self.breeding_cage,
            male=self.sire,
            female_1=self.dam,
            start_date="2026-01-01",
            active=True,
        )

    def test_end_page_requires_breeder_destinations(self):
        response = self.client.get(reverse("breeding:breeding_end", args=[self.breeding.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "End Breeding and Move Breeders")
        self.assertContains(response, "END-SIRE")
        self.assertContains(response, "END-DAM")
        self.assertContains(response, "Destination Cage Filter")
        self.assertContains(response, "Create New Cage")
        self.assertContains(response, f"select_field=destination_cage_{self.sire.pk}")
        self.assertContains(response, "purpose=holding")
        self.assertContains(response, "Exception: no current cage")
        self.assertNotContains(response, "<th>No Cage</th>", html=True)

    def test_end_breeding_moves_breeders_and_closes_breeding(self):
        response = self.client.post(
            reverse("breeding:breeding_end", args=[self.breeding.pk]),
            {
                "end_date": "2026-02-01",
                f"destination_cage_{self.sire.pk}": self.male_cage.pk,
                f"destination_cage_{self.dam.pk}": self.female_cage.pk,
                "notes": "Split after breeding.",
            },
        )
        self.assertRedirects(response, reverse("breeding:breeding_detail", args=[self.breeding.pk]))
        self.breeding.refresh_from_db()
        self.sire.refresh_from_db()
        self.dam.refresh_from_db()
        self.breeding_cage.refresh_from_db()
        self.assertFalse(self.breeding.active)
        self.assertEqual(self.breeding.status, Breeding.Status.CLOSED)
        self.assertEqual(self.sire.current_cage_id, self.male_cage.pk)
        self.assertEqual(self.dam.current_cage_id, self.female_cage.pk)
        self.assertFalse(
            CageMembership.objects.filter(mouse=self.sire, cage=self.breeding_cage, is_current=True).exists()
        )
        self.assertTrue(CageMembership.objects.filter(mouse=self.sire, cage=self.male_cage, is_current=True).exists())
        self.assertEqual(self.breeding_cage.purpose, Cage.Purpose.HOLDING)
        self.assertEqual(self.breeding_cage.cage_type, Cage.CageType.STANDARD)

    def test_end_breeding_rejects_mixed_sex_destination_cage(self):
        response = self.client.post(
            reverse("breeding:breeding_end", args=[self.breeding.pk]),
            {
                "end_date": "2026-02-01",
                f"destination_cage_{self.sire.pk}": self.male_cage.pk,
                f"destination_cage_{self.dam.pk}": self.male_cage.pk,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "would contain active male and female mice")
        self.breeding.refresh_from_db()
        self.sire.refresh_from_db()
        self.dam.refresh_from_db()
        self.assertTrue(self.breeding.active)
        self.assertEqual(self.sire.current_cage_id, self.breeding_cage.pk)
        self.assertEqual(self.dam.current_cage_id, self.breeding_cage.pk)
