from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from breeding.models import Breeding
from colony.cage_lifecycle import sync_breeding_member_cages
from colony.models import Cage, CageMembership, Mouse, StrainLine
from core.models import Project, ProjectMembership
from users.models import UserProfile


class BreedingCageSyncTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="breedsync", password="x")
        UserProfile.objects.filter(user=self.user).update(role=UserProfile.Role.MANAGER)
        self.client = Client()
        self.client.login(username="breedsync", password="x")
        self.project = Project.objects.create(name="BreedingSyncProject", owner=self.user)
        ProjectMembership.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectMembership.Role.MANAGER,
        )
        self.strain = StrainLine.objects.create(line_name="SyncStrain", name="SyncStrain")
        self.old_cage = Cage.objects.create(cage_id="OLD-CAGE")
        self.new_cage = Cage.objects.create(cage_id="NEW-CAGE", purpose=Cage.Purpose.BREEDING)
        self.sire = Mouse.objects.create(
            mouse_uid="M-SYNC-S",
            sex=Mouse.Sex.MALE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.old_cage,
        )
        self.dam = Mouse.objects.create(
            mouse_uid="M-SYNC-D",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.old_cage,
        )

    def test_sync_breeding_member_cages_moves_breeders(self):
        CageMembership.objects.create(
            mouse=self.sire,
            cage=self.old_cage,
            start_date=date(2025, 12, 20),
            is_current=True,
        )
        CageMembership.objects.create(
            mouse=self.dam,
            cage=self.old_cage,
            start_date=date(2025, 12, 20),
            is_current=True,
        )
        breeding = Breeding.objects.create(
            breeding_code="BR-SYNC-1",
            cage=self.new_cage,
            male=self.sire,
            female_1=self.dam,
            start_date=date(2026, 1, 1),
            active=True,
        )
        moved = sync_breeding_member_cages(breeding)
        self.assertEqual(moved, 2)
        self.sire.refresh_from_db()
        self.dam.refresh_from_db()
        self.assertEqual(self.sire.current_cage_id, self.new_cage.pk)
        self.assertEqual(self.dam.current_cage_id, self.new_cage.pk)
        self.assertFalse(CageMembership.objects.get(mouse=self.sire, cage=self.old_cage).is_current)
        self.assertFalse(CageMembership.objects.get(mouse=self.dam, cage=self.old_cage).is_current)
        self.assertTrue(CageMembership.objects.get(mouse=self.sire, cage=self.new_cage).is_current)
        self.assertTrue(CageMembership.objects.get(mouse=self.dam, cage=self.new_cage).is_current)

    def test_breeding_edit_moves_breeders_to_selected_cage(self):
        breeding = Breeding.objects.create(
            breeding_code="BR-SYNC-2",
            cage=self.old_cage,
            male=self.sire,
            female_1=self.dam,
            start_date="2026-01-01",
            active=True,
        )
        response = self.client.post(
            reverse("breeding:breeding_edit", args=[breeding.pk]),
            {
                "breeding_code": breeding.breeding_code,
                "cage": self.new_cage.pk,
                "breeding_type": Breeding.BreedingType.PAIR,
                "sire": self.sire.pk,
                "dams": [self.dam.pk],
                "male": self.sire.pk,
                "female_1": self.dam.pk,
                "start_date": "2026-01-01",
                "status": Breeding.Status.SETUP,
                "active": "on",
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("breeding:breeding_detail", args=[breeding.pk]))
        breeding.refresh_from_db()
        self.assertEqual(breeding.cage_id, self.new_cage.pk)
        self.sire.refresh_from_db()
        self.dam.refresh_from_db()
        self.assertEqual(self.sire.current_cage_id, self.new_cage.pk)
        self.assertEqual(self.dam.current_cage_id, self.new_cage.pk)

    def test_breeding_detail_warns_when_breeders_are_not_in_breeding_cage(self):
        breeding = Breeding.objects.create(
            breeding_code="BR-SYNC-MISMATCH",
            cage=self.new_cage,
            male=self.sire,
            female_1=self.dam,
            start_date="2026-01-01",
            active=True,
        )

        response = self.client.get(reverse("breeding:breeding_detail", args=[breeding.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Breeding Cage Mismatch")
        self.assertContains(response, self.sire.mouse_uid)
        self.assertContains(response, self.old_cage.cage_id)
        self.assertContains(response, self.new_cage.cage_id)

    def test_dashboard_warns_when_active_breeding_cage_mismatch_exists(self):
        Breeding.objects.create(
            breeding_code="BR-SYNC-DASH-MISMATCH",
            cage=self.new_cage,
            male=self.sire,
            female_1=self.dam,
            start_date="2026-01-01",
            active=True,
        )

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Breeding Cage Mismatch")
        self.assertContains(response, "BR-SYNC-DASH-MISMATCH")

    def test_breeding_list_displays_breeder_age_as_weeks_and_days(self):
        birth_date = timezone.localdate() - timedelta(days=59)
        self.sire.birth_date = birth_date
        self.sire.save(update_fields=["birth_date", "updated_at"])
        self.dam.birth_date = birth_date
        self.dam.save(update_fields=["birth_date", "updated_at"])
        Breeding.objects.create(
            breeding_code="BR-SYNC-AGE",
            cage=self.new_cage,
            male=self.sire,
            female_1=self.dam,
            start_date=timezone.localdate(),
            active=True,
        )

        response = self.client.get(reverse("breeding:breeding_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Age: 8w 3d")
        self.assertContains(response, "<td>8w 3d</td>", html=True)
