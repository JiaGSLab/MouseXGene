from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from breeding.models import Breeding, Litter
from colony.models import Cage, Mouse, StrainLine
from core.models import Project, ProjectMembership
from users.models import UserProfile


class LitterCreateFromBreedingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="litterpicker", password="x")
        UserProfile.objects.filter(user=self.user).update(role=UserProfile.Role.MANAGER)
        self.client = Client()
        self.client.login(username="litterpicker", password="x")
        self.project = Project.objects.create(name="Litter Picker Project", owner=self.user)
        ProjectMembership.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectMembership.Role.MANAGER,
        )
        self.strain = StrainLine.objects.create(line_name="LitterPickerStrain", name="LitterPickerStrain")
        self.cage_a = Cage.objects.create(cage_id="LITTER-PICK-A")
        self.cage_b = Cage.objects.create(cage_id="LITTER-PICK-B")
        self.sire_a = Mouse.objects.create(
            mouse_uid="LP-SIRE-A",
            sex=Mouse.Sex.MALE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage_a,
        )
        self.dam_a = Mouse.objects.create(
            mouse_uid="LP-DAM-A",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage_a,
        )
        self.sire_b = Mouse.objects.create(
            mouse_uid="LP-SIRE-B",
            sex=Mouse.Sex.MALE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage_b,
        )
        self.dam_b = Mouse.objects.create(
            mouse_uid="LP-DAM-B",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage_b,
        )
        self.active_breeding = Breeding.objects.create(
            breeding_code="BR-LITTER-A",
            cage=self.cage_a,
            male=self.sire_a,
            female_1=self.dam_a,
            start_date="2026-01-01",
            status=Breeding.Status.SETUP,
            active=True,
        )
        self.closed_breeding = Breeding.objects.create(
            breeding_code="BR-LITTER-B",
            cage=self.cage_b,
            male=self.sire_b,
            female_1=self.dam_b,
            start_date="2026-01-02",
            status=Breeding.Status.CLOSED,
            active=False,
        )

    def test_create_litter_page_filters_by_search(self):
        response = self.client.get(reverse("litters:litter_create_from_breeding"), {"q": "BR-LITTER-A"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "BR-LITTER-A")
        self.assertNotContains(response, "BR-LITTER-B")
        self.assertContains(response, "Create Litter")

    def test_create_litter_page_hides_closed_by_default(self):
        response = self.client.get(reverse("litters:litter_create_from_breeding"))
        self.assertContains(response, "BR-LITTER-A")
        self.assertNotContains(response, "BR-LITTER-B")
        response = self.client.get(reverse("litters:litter_create_from_breeding"), {"include_closed": "yes"})
        self.assertContains(response, "BR-LITTER-A")
        self.assertContains(response, "BR-LITTER-B")

    def test_record_litter_form_does_not_wean_litter(self):
        url = reverse("breeding:litter_create", args=[self.active_breeding.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Record birth only.")
        self.assertContains(response, "Litter code (optional)")
        self.assertContains(response, "Optional: L2026-06-001")
        self.assertContains(response, "Total born (number of pups)")
        self.assertContains(response, "e.g. 8")
        self.assertContains(response, "Do not enter the pup count here.")
        self.assertNotContains(response, "Wean date")
        self.assertNotContains(response, "Litter status")

        response = self.client.post(
            url,
            {
                "litter_code": "LP-LITTER-1",
                "birth_date": "2026-01-20",
                "total_born": "6",
                "alive_count": "6",
                "dead_count": "0",
                "male_count": "3",
                "female_count": "3",
                "wean_date": "2026-02-10",
                "litter_status": Litter.LitterStatus.WEANED,
                "tail_tag_date": "",
                "notes": "",
            },
        )
        litter = Litter.objects.get(litter_code="LP-LITTER-1")
        self.assertRedirects(response, reverse("litters:litter_detail", args=[litter.pk]))
        self.assertIsNone(litter.wean_date)
        self.assertEqual(litter.litter_status, Litter.LitterStatus.ACTIVE)
