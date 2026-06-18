from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from colony.models import Cage, CageMembership, Mouse, StrainLine
from core.models import Project


class ReconcileCageMembershipCommandTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="reconcile-owner", password="x")
        self.project = Project.objects.create(name="Reconcile Project", owner=self.user)
        self.strain = StrainLine.objects.create(line_name="Reconcile Line", name="Reconcile Line")
        self.cage_a = Cage.objects.create(cage_id="REC-A", status=Cage.Status.ACTIVE)
        self.cage_b = Cage.objects.create(cage_id="REC-B", status=Cage.Status.ACTIVE)

    def _run(self, *args):
        out = StringIO()
        call_command("reconcile_cage_memberships", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_reports_missing_current_membership_without_writing(self):
        mouse = Mouse.objects.create(
            mouse_uid="REC-MISSING",
            sex=Mouse.Sex.MALE,
            status=Mouse.Status.ACTIVE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage_a,
        )

        output = self._run()

        self.assertIn("Would repair REC-MISSING", output)
        self.assertIn("create matching current membership", output)
        self.assertFalse(CageMembership.objects.filter(mouse=mouse).exists())

    def test_apply_creates_missing_current_membership(self):
        mouse = Mouse.objects.create(
            mouse_uid="REC-CREATE",
            sex=Mouse.Sex.MALE,
            status=Mouse.Status.ACTIVE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage_a,
        )

        output = self._run("--apply")

        self.assertIn("Repaired REC-CREATE", output)
        membership = CageMembership.objects.get(mouse=mouse)
        self.assertEqual(membership.cage_id, self.cage_a.pk)
        self.assertTrue(membership.is_current)
        self.assertIsNone(membership.end_date)

        second = self._run()
        self.assertIn("No cage membership reconciliation issues found.", second)

    def test_apply_closes_stale_current_membership_and_creates_matching_one(self):
        mouse = Mouse.objects.create(
            mouse_uid="REC-STALE",
            sex=Mouse.Sex.FEMALE,
            status=Mouse.Status.ACTIVE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage_b,
        )
        stale = CageMembership.objects.create(
            mouse=mouse,
            cage=self.cage_a,
            start_date=timezone.localdate(),
            is_current=True,
        )

        output = self._run("--apply")

        self.assertIn("close current membership(s): REC-A", output)
        stale.refresh_from_db()
        self.assertFalse(stale.is_current)
        self.assertEqual(stale.end_date, timezone.localdate())
        self.assertTrue(CageMembership.objects.filter(mouse=mouse, cage=self.cage_b, is_current=True).exists())

    def test_apply_clears_terminal_mouse_current_cage_and_closes_membership(self):
        mouse = Mouse.objects.create(
            mouse_uid="REC-TERM",
            sex=Mouse.Sex.MALE,
            status=Mouse.Status.EUTHANIZED,
            death_date=timezone.localdate(),
            euthanasia_date=timezone.localdate(),
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage_a,
        )
        membership = CageMembership.objects.create(
            mouse=mouse,
            cage=self.cage_a,
            start_date=timezone.localdate(),
            is_current=True,
        )

        output = self._run("--apply")

        self.assertIn("clear terminal current cage", output)
        mouse.refresh_from_db()
        membership.refresh_from_db()
        self.assertIsNone(mouse.current_cage_id)
        self.assertFalse(membership.is_current)
        self.assertEqual(membership.end_date, timezone.localdate())

    def test_apply_closes_current_membership_when_mouse_has_no_current_cage(self):
        mouse = Mouse.objects.create(
            mouse_uid="REC-NONE",
            sex=Mouse.Sex.FEMALE,
            status=Mouse.Status.ACTIVE,
            strain_line=self.strain,
            project=self.project,
            current_cage=None,
        )
        membership = CageMembership.objects.create(
            mouse=mouse,
            cage=self.cage_a,
            start_date=timezone.localdate(),
            is_current=True,
        )

        output = self._run("--apply")

        self.assertIn("target=no current cage", output)
        membership.refresh_from_db()
        self.assertFalse(membership.is_current)
        self.assertEqual(membership.end_date, timezone.localdate())

    def test_can_reopen_closed_cages_that_still_have_active_current_mice(self):
        closed_cage = Cage.objects.create(cage_id="REC-CLOSED", status=Cage.Status.CLOSED)
        mouse = Mouse.objects.create(
            mouse_uid="REC-CLOSED-MOUSE",
            sex=Mouse.Sex.MALE,
            status=Mouse.Status.ACTIVE,
            strain_line=self.strain,
            project=self.project,
            current_cage=closed_cage,
        )
        CageMembership.objects.create(
            mouse=mouse,
            cage=closed_cage,
            start_date=timezone.localdate(),
            is_current=True,
        )

        dry_run = self._run()
        self.assertIn("closed cage(s) still contain active current mice", dry_run)
        closed_cage.refresh_from_db()
        self.assertEqual(closed_cage.status, Cage.Status.CLOSED)

        output = self._run("--apply", "--reopen-closed-active-cages")
        self.assertIn("Reopened 1 closed cage", output)
        closed_cage.refresh_from_db()
        self.assertEqual(closed_cage.status, Cage.Status.ACTIVE)
