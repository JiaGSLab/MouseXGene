from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from breeding.models import Breeding
from colony.cage_lifecycle import ensure_breeding_for_cage, sync_cage_status_from_mice
from colony.models import Cage, Mouse, StrainLine
from core.models import Project, ProjectMembership
from users.models import UserProfile


class CageLifecycleTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="lifecycle", password="x")
        UserProfile.objects.filter(user=self.user).update(role=UserProfile.Role.MANAGER)
        self.strain = StrainLine.objects.create(line_name="LS", name="LS")
        self.project = Project.objects.create(name="P1", owner=self.user)
        ProjectMembership.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectMembership.Role.MANAGER,
        )
        self.cage = Cage.objects.create(cage_id="BR-CAGE-1", purpose=Cage.Purpose.BREEDING)
        self.male = Mouse.objects.create(
            mouse_uid="M-SIRE",
            sex=Mouse.Sex.MALE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage,
        )
        self.female = Mouse.objects.create(
            mouse_uid="M-DAM",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage,
        )

    def test_breeding_purpose_creates_active_breeding(self):
        breeding = ensure_breeding_for_cage(self.cage)
        self.assertIsNotNone(breeding)
        self.cage.refresh_from_db()
        self.assertEqual(self.cage.cage_type, Cage.CageType.BREEDING)
        self.assertTrue(
            Breeding.objects.filter(cage=self.cage, active=True, male=self.male, female_1=self.female).exists()
        )

    def test_all_inactive_mice_closes_active_cage(self):
        active_cage = Cage.objects.create(cage_id="CLOSE-ME")
        Mouse.objects.create(
            mouse_uid="M-DEAD",
            sex=Mouse.Sex.FEMALE,
            status=Mouse.Status.EUTHANIZED,
            strain_line=self.strain,
            project=self.project,
            current_cage=active_cage,
        )
        changed = sync_cage_status_from_mice(active_cage)
        active_cage.refresh_from_db()
        self.assertTrue(changed)
        self.assertEqual(active_cage.status, Cage.Status.CLOSED)

    def test_empty_cage_is_not_auto_closed(self):
        empty_cage = Cage.objects.create(cage_id="EMPTY-1")
        changed = sync_cage_status_from_mice(empty_cage)
        empty_cage.refresh_from_db()
        self.assertFalse(changed)
        self.assertEqual(empty_cage.status, Cage.Status.ACTIVE)

    def test_cage_edit_with_breeding_purpose_shows_on_breeding_list(self):
        client = Client()
        client.login(username="lifecycle", password="x")
        holding_cage = Cage.objects.create(cage_id="HOLD-TO-BR")
        Mouse.objects.create(
            mouse_uid="M-SIRE-2",
            sex=Mouse.Sex.MALE,
            strain_line=self.strain,
            project=self.project,
            current_cage=holding_cage,
        )
        Mouse.objects.create(
            mouse_uid="M-DAM-2",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.strain,
            project=self.project,
            current_cage=holding_cage,
        )
        response = client.post(
            reverse("colony:cage_edit", kwargs={"pk": holding_cage.pk}),
            {
                "cage_id": holding_cage.cage_id,
                "cage_type": Cage.CageType.STANDARD,
                "purpose": Cage.Purpose.BREEDING,
                "status": Cage.Status.ACTIVE,
                "room": "",
                "rack": "",
                "position": "",
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Breeding.objects.filter(cage=holding_cage, active=True).exists())
        list_response = client.get(reverse("breeding:breeding_list"))
        self.assertContains(list_response, holding_cage.cage_id)

    def test_breeding_purpose_cage_without_sire_shows_as_pending(self):
        client = Client()
        client.login(username="lifecycle", password="x")
        only_dams = Cage.objects.create(cage_id="PEND-CAGE", purpose=Cage.Purpose.BREEDING)
        Mouse.objects.create(
            mouse_uid="M-DAM-A",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.strain,
            project=self.project,
            current_cage=only_dams,
        )
        response = client.get(reverse("breeding:breeding_list"))
        self.assertContains(response, "PEND-CAGE")
        self.assertContains(response, "Pending setup")
        self.assertContains(response, "Need sire")


class DashboardAlertTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="dashuser", password="x")
        UserProfile.objects.filter(user=self.user).update(role=UserProfile.Role.MEMBER)
        self.project = Project.objects.create(name="DashP", owner=self.user)
        ProjectMembership.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectMembership.Role.MEMBER,
        )
        self.client = Client()
        self.client.login(username="dashuser", password="x")

    def test_empty_cage_alert_counts_without_mice_in_project(self):
        Cage.objects.create(cage_id="EMPTY-DASH", created_at=timezone.now() - timedelta(days=20))
        response = self.client.get(reverse("home"))
        self.assertContains(response, "Cages With No Current Mice")
        self.assertContains(response, "EMPTY-DASH")

    def test_recent_lists_show_created_dates(self):
        Cage.objects.create(cage_id="REC-CAGE", created_at=timezone.now() - timedelta(days=1))
        response = self.client.get(reverse("home"))
        self.assertContains(response, "Recently Created Cages")
        self.assertContains(response, "mini-list__date")
        self.assertContains(response, "REC-CAGE")
