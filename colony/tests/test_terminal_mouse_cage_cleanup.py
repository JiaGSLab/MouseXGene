from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from breeding.models import Breeding
from colony.models import Cage, CageMembership, Mouse, StrainLine
from core.models import Project, ProjectMembership
from users.models import UserProfile


class TerminalMouseCageCleanupTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="terminal-admin",
            email="terminal-admin@example.test",
            password="x",
        )
        UserProfile.objects.filter(user=self.user).update(role=UserProfile.Role.ADMIN)
        self.client = Client()
        self.client.login(username="terminal-admin", password="x")

        self.strain = StrainLine.objects.create(line_name="Terminal Line", name="Terminal Line")
        self.project = Project.objects.create(name="Terminal Project", owner=self.user)
        self.cage = Cage.objects.create(cage_id="TERM-CAGE-1", status=Cage.Status.ACTIVE)
        self.start_date = timezone.localdate() - timedelta(days=7)
        self.mouse = Mouse.objects.create(
            mouse_uid="TERM-M-1",
            sex=Mouse.Sex.MALE,
            status=Mouse.Status.ACTIVE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage,
        )
        self.membership = CageMembership.objects.create(
            mouse=self.mouse,
            cage=self.cage,
            start_date=self.start_date,
            is_current=True,
        )

    def test_end_mouse_removes_current_cage_and_closes_membership(self):
        response = self.client.post(
            reverse("mice:mouse_end", args=[self.mouse.pk]),
            {
                "terminal_status": Mouse.Status.EUTHANIZED,
                "end_date": timezone.localdate().isoformat(),
                "reason": "Scheduled endpoint",
                "confirm": "on",
            },
        )

        self.assertRedirects(response, reverse("mice:mouse_detail", args=[self.mouse.pk]))
        self.mouse.refresh_from_db()
        self.membership.refresh_from_db()
        self.cage.refresh_from_db()

        self.assertEqual(self.mouse.status, Mouse.Status.EUTHANIZED)
        self.assertIsNone(self.mouse.current_cage_id)
        self.assertFalse(self.membership.is_current)
        self.assertEqual(self.membership.end_date, timezone.localdate())
        self.assertIn("End Mouse", self.membership.reason)
        self.assertEqual(self.cage.status, Cage.Status.CLOSED)

    def test_mouse_detail_links_to_end_mouse_workflow(self):
        response = self.client.get(reverse("mice:mouse_detail", args=[self.mouse.pk]))
        self.assertContains(response, "End / Euthanize Mouse")
        self.assertContains(response, reverse("mice:mouse_end", args=[self.mouse.pk]))

    def test_admin_can_restore_terminal_mouse_to_previous_cage(self):
        end_date = timezone.localdate() - timedelta(days=1)
        self.mouse.status = Mouse.Status.EUTHANIZED
        self.mouse.death_date = end_date
        self.mouse.euthanasia_date = end_date
        self.mouse.death_reason = "Mistaken endpoint"
        self.mouse.current_cage = None
        self.mouse.save(update_fields=["status", "death_date", "euthanasia_date", "death_reason", "current_cage"])
        self.membership.end_date = end_date
        self.membership.is_current = False
        self.membership.reason = "Mouse marked as Euthanized via End Mouse workflow."
        self.membership.save(update_fields=["end_date", "is_current", "reason"])
        self.cage.status = Cage.Status.CLOSED
        self.cage.save(update_fields=["status"])

        detail = self.client.get(reverse("mice:mouse_detail", args=[self.mouse.pk]))
        self.assertContains(detail, "Restore Mouse")
        self.assertContains(detail, reverse("mice:mouse_restore", args=[self.mouse.pk]))

        response = self.client.post(
            reverse("mice:mouse_restore", args=[self.mouse.pk]),
            {
                "destination_cage": self.cage.pk,
                "restore_date": timezone.localdate().isoformat(),
                "reason": "Wrong mouse was ended.",
                "confirm": "on",
            },
        )

        self.assertRedirects(response, reverse("mice:mouse_detail", args=[self.mouse.pk]))
        self.mouse.refresh_from_db()
        self.membership.refresh_from_db()
        self.cage.refresh_from_db()

        self.assertEqual(self.mouse.status, Mouse.Status.ACTIVE)
        self.assertEqual(self.mouse.current_cage_id, self.cage.pk)
        self.assertIsNone(self.mouse.death_date)
        self.assertIsNone(self.mouse.euthanasia_date)
        self.assertEqual(self.mouse.death_reason, "")
        self.assertTrue(self.membership.is_current)
        self.assertIsNone(self.membership.end_date)
        self.assertEqual(self.membership.reason, "Wrong mouse was ended.")
        self.assertEqual(self.cage.status, Cage.Status.ACTIVE)

    def test_project_manager_can_open_end_mouse_form(self):
        manager = get_user_model().objects.create_user(username="terminal-manager", password="x")
        UserProfile.objects.filter(user=manager).update(role=UserProfile.Role.MEMBER)
        ProjectMembership.objects.create(
            project=self.project,
            user=manager,
            role=ProjectMembership.Role.MANAGER,
        )
        self.client.logout()
        self.client.login(username="terminal-manager", password="x")

        detail = self.client.get(reverse("mice:mouse_detail", args=[self.mouse.pk]))
        self.assertContains(detail, "End / Euthanize Mouse")
        response = self.client.get(reverse("mice:mouse_end", args=[self.mouse.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirm End Mouse")

    def test_project_manager_does_not_get_restore_mouse_action(self):
        self.mouse.status = Mouse.Status.EUTHANIZED
        self.mouse.current_cage = None
        self.mouse.save(update_fields=["status", "current_cage"])
        manager = get_user_model().objects.create_user(username="terminal-manager-restore", password="x")
        UserProfile.objects.filter(user=manager).update(role=UserProfile.Role.MEMBER)
        ProjectMembership.objects.create(
            project=self.project,
            user=manager,
            role=ProjectMembership.Role.MANAGER,
        )
        self.client.logout()
        self.client.login(username="terminal-manager-restore", password="x")

        detail = self.client.get(reverse("mice:mouse_detail", args=[self.mouse.pk]))
        self.assertNotContains(detail, "Restore Mouse")

    def test_end_mouse_requires_confirmation(self):
        response = self.client.post(
            reverse("mice:mouse_end", args=[self.mouse.pk]),
            {
                "terminal_status": Mouse.Status.EUTHANIZED,
                "end_date": timezone.localdate().isoformat(),
                "reason": "Scheduled endpoint",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required")
        self.mouse.refresh_from_db()
        self.assertEqual(self.mouse.status, Mouse.Status.ACTIVE)

    def test_editing_mouse_to_terminal_status_removes_current_cage(self):
        death_date = timezone.localdate()
        response = self.client.post(
            reverse("mice:mouse_edit", args=[self.mouse.pk]),
            {
                "admin_correction_unlocked": "1",
                "admin_correction_reason": "record historical terminal status",
                "mouse_uid": self.mouse.mouse_uid,
                "sex": self.mouse.sex,
                "birth_date": "",
                "death_date": death_date.isoformat(),
                "euthanasia_date": "",
                "death_reason": "Found dead.",
                "status": Mouse.Status.DEAD,
                "strain_line": self.strain.pk,
                "current_cage": self.cage.pk,
                "current_cage_lookup": "",
                "sire": "",
                "dam": "",
                "project": self.project.pk,
                "ear_tag": "",
                "toe_tag": "",
                "origin": "",
                "coat_color": "",
                "notes": "",
            },
        )

        self.assertRedirects(response, reverse("mice:mouse_detail", args=[self.mouse.pk]))
        self.mouse.refresh_from_db()
        self.membership.refresh_from_db()
        self.cage.refresh_from_db()

        self.assertEqual(self.mouse.status, Mouse.Status.DEAD)
        self.assertIsNone(self.mouse.current_cage_id)
        self.assertFalse(self.membership.is_current)
        self.assertEqual(self.membership.end_date, death_date)
        self.assertIn("Edit Mouse", self.membership.reason)
        self.assertEqual(self.cage.status, Cage.Status.CLOSED)

    def test_terminal_cleanup_backfills_membership_when_current_cage_has_no_history(self):
        cage = Cage.objects.create(cage_id="TERM-CAGE-2", status=Cage.Status.ACTIVE)
        mouse = Mouse.objects.create(
            mouse_uid="TERM-M-2",
            sex=Mouse.Sex.FEMALE,
            status=Mouse.Status.ACTIVE,
            strain_line=self.strain,
            project=self.project,
            current_cage=cage,
        )

        response = self.client.post(
            reverse("mice:mouse_end", args=[mouse.pk]),
            {
                "terminal_status": Mouse.Status.EUTHANIZED,
                "end_date": timezone.localdate().isoformat(),
                "reason": "Scheduled endpoint",
                "confirm": "on",
            },
        )

        self.assertRedirects(response, reverse("mice:mouse_detail", args=[mouse.pk]))
        mouse.refresh_from_db()
        cage.refresh_from_db()
        membership = CageMembership.objects.get(mouse=mouse, cage=cage)

        self.assertIsNone(mouse.current_cage_id)
        self.assertFalse(membership.is_current)
        self.assertEqual(membership.start_date, timezone.localdate())
        self.assertEqual(membership.end_date, timezone.localdate())
        self.assertEqual(cage.status, Cage.Status.CLOSED)

    def test_end_mouse_closes_active_breeding_for_terminal_breeder(self):
        dam = Mouse.objects.create(
            mouse_uid="TERM-DAM-1",
            sex=Mouse.Sex.FEMALE,
            status=Mouse.Status.ACTIVE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage,
        )
        CageMembership.objects.create(
            mouse=dam,
            cage=self.cage,
            start_date=self.start_date,
            is_current=True,
        )
        breeding = Breeding.objects.create(
            breeding_code="TERM-BR-1",
            cage=self.cage,
            male=self.mouse,
            female_1=dam,
            start_date=self.start_date,
            active=True,
        )

        response = self.client.post(
            reverse("mice:mouse_end", args=[self.mouse.pk]),
            {
                "terminal_status": Mouse.Status.EUTHANIZED,
                "end_date": timezone.localdate().isoformat(),
                "reason": "Scheduled endpoint",
                "confirm": "on",
            },
            follow=True,
        )

        self.assertContains(response, "Closed active breeding")
        breeding.refresh_from_db()
        self.mouse.refresh_from_db()
        self.cage.refresh_from_db()
        self.assertFalse(breeding.active)
        self.assertEqual(breeding.status, Breeding.Status.CLOSED)
        self.assertIsNone(self.mouse.current_cage_id)
        self.assertEqual(self.cage.status, Cage.Status.ACTIVE)
