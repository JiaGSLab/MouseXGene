from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from colony.models import Cage, CageMembership, Mouse, StrainLine
from core.models import Project
from users.models import UserProfile


class TerminalMouseCageCleanupTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="terminal-admin", password="x")
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
        response = self.client.post(reverse("mice:mouse_end", args=[self.mouse.pk]))

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

    def test_editing_mouse_to_terminal_status_removes_current_cage(self):
        death_date = timezone.localdate()
        response = self.client.post(
            reverse("mice:mouse_edit", args=[self.mouse.pk]),
            {
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

        response = self.client.post(reverse("mice:mouse_end", args=[mouse.pk]))

        self.assertRedirects(response, reverse("mice:mouse_detail", args=[mouse.pk]))
        mouse.refresh_from_db()
        cage.refresh_from_db()
        membership = CageMembership.objects.get(mouse=mouse, cage=cage)

        self.assertIsNone(mouse.current_cage_id)
        self.assertFalse(membership.is_current)
        self.assertEqual(membership.start_date, timezone.localdate())
        self.assertEqual(membership.end_date, timezone.localdate())
        self.assertEqual(cage.status, Cage.Status.CLOSED)
