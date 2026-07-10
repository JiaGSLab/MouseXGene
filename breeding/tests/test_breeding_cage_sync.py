from datetime import date, timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from breeding.models import Breeding
from breeding.consistency import active_breeding_cage_mismatches
from colony.cage_lifecycle import ensure_breeding_for_cage, sync_breeding_member_cages
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

    def test_existing_active_breeding_cage_sync_does_not_add_intruder_mice(self):
        breeding = Breeding.objects.create(
            breeding_code="BR-SYNC-NO-INTRUDER",
            cage=self.new_cage,
            male=self.sire,
            female_1=self.dam,
            start_date="2026-01-01",
            active=True,
        )
        intruder_male = Mouse.objects.create(
            mouse_uid="M-SYNC-INTRUDER-M",
            sex=Mouse.Sex.MALE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.new_cage,
        )
        intruder_female = Mouse.objects.create(
            mouse_uid="M-SYNC-INTRUDER-F",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.new_cage,
        )

        synced = ensure_breeding_for_cage(self.new_cage)

        self.assertEqual(synced, breeding)
        breeding.refresh_from_db()
        self.assertEqual(breeding.male_id, self.sire.pk)
        self.assertEqual(breeding.female_1_id, self.dam.pk)
        self.assertIsNone(breeding.female_2_id)
        self.assertFalse(breeding.extra_female_links.filter(mouse=intruder_female).exists())
        self.assertFalse(breeding.breeding_members.filter(mouse__in=[intruder_male, intruder_female]).exists())

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

    def test_sync_active_breeding_cages_command_repairs_mismatches(self):
        Breeding.objects.create(
            breeding_code="BR-SYNC-COMMAND",
            cage=self.new_cage,
            male=self.sire,
            female_1=self.dam,
            start_date="2026-01-01",
            active=True,
        )

        out = StringIO()
        call_command("sync_active_breeding_cages", stdout=out)

        self.sire.refresh_from_db()
        self.dam.refresh_from_db()
        self.assertEqual(self.sire.current_cage_id, self.new_cage.pk)
        self.assertEqual(self.dam.current_cage_id, self.new_cage.pk)
        self.assertTrue(CageMembership.objects.filter(mouse=self.sire, cage=self.new_cage, is_current=True).exists())
        self.assertTrue(CageMembership.objects.filter(mouse=self.dam, cage=self.new_cage, is_current=True).exists())
        self.assertIn("Repaired 2 breeder cage assignment(s).", out.getvalue())

    def test_sync_active_breeding_cages_does_not_reoccupy_terminal_breeders(self):
        self.sire.status = Mouse.Status.EUTHANIZED
        self.sire.current_cage = None
        self.sire.save(update_fields=["status", "current_cage", "updated_at"])
        CageMembership.objects.create(
            mouse=self.sire,
            cage=self.old_cage,
            start_date=date(2026, 1, 1),
            is_current=True,
            reason="Historical stale current membership",
        )
        Breeding.objects.create(
            breeding_code="BR-SYNC-TERMINAL",
            cage=self.new_cage,
            male=self.sire,
            female_1=self.dam,
            start_date="2026-01-01",
            active=True,
        )

        out = StringIO()
        call_command("sync_active_breeding_cages", stdout=out)

        self.sire.refresh_from_db()
        self.dam.refresh_from_db()
        self.assertIsNone(self.sire.current_cage_id)
        self.assertEqual(self.dam.current_cage_id, self.new_cage.pk)
        self.assertFalse(CageMembership.objects.get(mouse=self.sire, cage=self.old_cage).is_current)
        self.assertFalse(CageMembership.objects.filter(mouse=self.sire, cage=self.new_cage, is_current=True).exists())
        self.assertTrue(CageMembership.objects.filter(mouse=self.dam, cage=self.new_cage, is_current=True).exists())
        self.assertIn("Repaired 1 breeder cage assignment(s).", out.getvalue())

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

    def test_dashboard_warns_when_active_breeder_has_no_current_cage(self):
        self.sire.current_cage = None
        self.sire.save(update_fields=["current_cage", "updated_at"])
        self.dam.current_cage = self.new_cage
        self.dam.save(update_fields=["current_cage", "updated_at"])
        Breeding.objects.create(
            breeding_code="BR-SYNC-NO-CAGE",
            cage=self.new_cage,
            male=self.sire,
            female_1=self.dam,
            start_date="2026-01-01",
            active=True,
        )

        response = self.client.get(reverse("home"))

        self.assertContains(response, "Breeding Cage Mismatch")
        self.assertContains(response, "BR-SYNC-NO-CAGE")

    def test_active_breeding_cage_mismatch_uses_prefetched_members(self):
        for idx in range(5):
            cage = Cage.objects.create(cage_id=f"NEW-CAGE-PREFETCH-{idx}", purpose=Cage.Purpose.BREEDING)
            sire = Mouse.objects.create(
                mouse_uid=f"M-SYNC-PREFETCH-S-{idx}",
                sex=Mouse.Sex.MALE,
                strain_line=self.strain,
                project=self.project,
                current_cage=self.old_cage,
            )
            dam = Mouse.objects.create(
                mouse_uid=f"M-SYNC-PREFETCH-D-{idx}",
                sex=Mouse.Sex.FEMALE,
                strain_line=self.strain,
                project=self.project,
                current_cage=self.old_cage,
            )
            breeding = Breeding.objects.create(
                breeding_code=f"BR-SYNC-PREFETCH-{idx}",
                cage=cage,
                male=sire,
                female_1=dam,
                start_date="2026-01-01",
                active=True,
            )
            breeding.sync_members_from_legacy_fields()

        queryset = Breeding.objects.filter(breeding_code__startswith="BR-SYNC-PREFETCH-")
        with CaptureQueriesContext(connection) as captured:
            mismatches = active_breeding_cage_mismatches(queryset)

        self.assertEqual(len(mismatches), 5)
        self.assertLessEqual(len(captured), 5)

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
        self.assertContains(response, '<span class="muted">8w 3d</span>', html=True)
