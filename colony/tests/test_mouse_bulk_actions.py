from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from breeding.models import Breeding
from colony.models import Cage, CageMembership, Mouse, MouseExperimentAssignment, StrainLine
from core.models import Project
from users.models import UserProfile


class MouseBulkActionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="bulk-admin",
            email="bulk-admin@example.test",
            password="x",
        )
        UserProfile.objects.filter(user=self.user).update(role=UserProfile.Role.ADMIN)
        self.client.force_login(self.user)
        self.project = Project.objects.create(name="Bulk Mouse Project", owner=self.user)
        self.strain = StrainLine.objects.create(line_name="Bulk Mouse Line", name="Bulk Mouse Line")
        self.source_cage = Cage.objects.create(
            cage_id="BULK-SRC",
            status=Cage.Status.ACTIVE,
            purpose=Cage.Purpose.HOLDING,
        )
        self.dest_cage = Cage.objects.create(
            cage_id="BULK-DEST",
            status=Cage.Status.ACTIVE,
            purpose=Cage.Purpose.HOLDING,
        )
        self.male = self._mouse("BULK-M1", Mouse.Sex.MALE, self.source_cage)
        self.female = self._mouse("BULK-F1", Mouse.Sex.FEMALE, self.source_cage)
        self.extra_female = self._mouse("BULK-F2", Mouse.Sex.FEMALE, self.source_cage)

    def _mouse(self, uid: str, sex: str, cage: Cage) -> Mouse:
        mouse = Mouse.objects.create(
            mouse_uid=uid,
            sex=sex,
            status=Mouse.Status.ACTIVE,
            birth_date=date(2026, 1, 1),
            project=self.project,
            strain_line=self.strain,
            current_cage=cage,
        )
        CageMembership.objects.create(
            mouse=mouse,
            cage=cage,
            start_date=timezone.localdate() - timedelta(days=7),
            is_current=True,
        )
        return mouse

    def test_mouse_list_renders_bulk_controls_and_state_rows(self):
        MouseExperimentAssignment.objects.create(mouse=self.extra_female, created_by=self.user, updated_by=self.user)
        Breeding.objects.create(
            breeding_code="BULK-BR-ROW",
            cage=self.source_cage,
            male=self.male,
            female_1=self.female,
            start_date=date(2026, 1, 10),
            active=True,
        )

        response = self.client.get(reverse("mice:mouse_list"), {"q": "BULK-"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="mouse-bulk-form"')
        self.assertContains(response, 'name="mouse_ids"')
        self.assertContains(response, "row-breeding-active")
        self.assertContains(response, "row-experiment-active")
        self.assertContains(response, "Mark in experiment")
        self.assertContains(response, "End selected mice")

    def test_mark_in_experiment_creates_active_assignment(self):
        response = self.client.post(
            reverse("mice:mouse_bulk_action"),
            {
                "bulk_action": "mark_experiment",
                "confirm_bulk_action": "1",
                "mouse_ids": [self.extra_female.pk],
                "note": "behavior assay",
                "confirm": "on",
                "next": reverse("mice:mouse_list"),
            },
        )

        self.assertRedirects(response, reverse("mice:mouse_list"))
        assignment = MouseExperimentAssignment.objects.get(mouse=self.extra_female)
        self.assertIsNone(assignment.ended_at)
        self.assertEqual(assignment.note, "behavior assay")

    def test_bulk_action_rejects_more_than_selection_limit(self):
        many_ids = []
        for index in range(201):
            mouse = Mouse.objects.create(
                mouse_uid=f"BULK-LIMIT-{index:03d}",
                sex=Mouse.Sex.FEMALE,
                status=Mouse.Status.ACTIVE,
                birth_date=date(2026, 1, 1),
                project=self.project,
                strain_line=self.strain,
            )
            many_ids.append(mouse.pk)

        response = self.client.post(
            reverse("mice:mouse_bulk_action"),
            {
                "bulk_action": "mark_experiment",
                "confirm_bulk_action": "1",
                "mouse_ids": many_ids,
                "note": "too many",
                "confirm": "on",
                "next": reverse("mice:mouse_list"),
            },
        )

        self.assertRedirects(response, reverse("mice:mouse_list"))
        self.assertFalse(MouseExperimentAssignment.objects.filter(note="too many").exists())

    def test_clear_experiment_ends_active_assignment(self):
        assignment = MouseExperimentAssignment.objects.create(
            mouse=self.extra_female,
            created_by=self.user,
            updated_by=self.user,
        )

        response = self.client.post(
            reverse("mice:mouse_bulk_action"),
            {
                "bulk_action": "clear_experiment",
                "confirm_bulk_action": "1",
                "mouse_ids": [self.extra_female.pk],
                "confirm": "on",
                "next": reverse("mice:mouse_list"),
            },
        )

        self.assertRedirects(response, reverse("mice:mouse_list"))
        assignment.refresh_from_db()
        self.assertIsNotNone(assignment.ended_at)
        self.assertEqual(assignment.ended_by, self.user)

    def test_create_breeding_redirects_with_prefilled_breeders(self):
        response = self.client.post(
            reverse("mice:mouse_bulk_action"),
            {
                "bulk_action": "create_breeding",
                "mouse_ids": [self.male.pk, self.female.pk],
                "next": reverse("mice:mouse_list"),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("breeding:breeding_create"), response["Location"])
        self.assertIn(f"sire={self.male.pk}", response["Location"])
        self.assertIn(f"dams={self.female.pk}", response["Location"])

        page = self.client.get(response["Location"])
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, f'<option value="{self.male.pk}" selected')
        self.assertContains(page, f'<option value="{self.female.pk}" selected')

    def test_create_breeding_invalid_selection_shows_blocking_dialog(self):
        response = self.client.post(
            reverse("mice:mouse_bulk_action"),
            {
                "bulk_action": "create_breeding",
                "mouse_ids": [self.female.pk, self.extra_female.pk],
                "next": reverse("mice:mouse_list"),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create breeding blocked")
        self.assertContains(response, "Create breeding requires exactly one selected male.")
        self.assertContains(response, "bulk-blocking-rule-dialog")
        self.assertNotContains(response, "This bulk action is not available yet.")

    def test_bulk_end_blocks_active_breeding_mouse(self):
        Breeding.objects.create(
            breeding_code="BULK-BR-END",
            cage=self.source_cage,
            male=self.male,
            female_1=self.female,
            start_date=date(2026, 1, 10),
            active=True,
        )

        response = self.client.post(
            reverse("mice:mouse_bulk_action"),
            {
                "bulk_action": "end",
                "confirm_bulk_action": "1",
                "mouse_ids": [self.male.pk],
                "terminal_status": Mouse.Status.EUTHANIZED,
                "end_date": timezone.localdate().isoformat(),
                "reason": "Scheduled endpoint",
                "confirm": "on",
                "next": reverse("mice:mouse_list"),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already in active breeding")
        self.male.refresh_from_db()
        self.assertEqual(self.male.status, Mouse.Status.ACTIVE)

    def test_bulk_move_blocks_mixed_sex_holding_destination(self):
        response = self.client.post(
            reverse("mice:mouse_bulk_action"),
            {
                "bulk_action": "move_cage",
                "confirm_bulk_action": "1",
                "mouse_ids": [self.male.pk, self.female.pk],
                "destination_cage": self.dest_cage.pk,
                "move_date": timezone.localdate().isoformat(),
                "reason": "Bulk move",
                "confirm": "on",
                "next": reverse("mice:mouse_list"),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Active male and female mice cannot be housed together")
        self.male.refresh_from_db()
        self.assertEqual(self.male.current_cage_id, self.source_cage.pk)

    def test_bulk_move_confirm_warns_mixed_sex_requires_breeding_cage(self):
        response = self.client.post(
            reverse("mice:mouse_bulk_action"),
            {
                "bulk_action": "move_cage",
                "mouse_ids": [self.male.pk, self.female.pk],
                "next": reverse("mice:mouse_list"),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mixed-sex move selected.")
        self.assertContains(response, "Choose a Breeding cage, or move males and females separately")
        self.assertContains(response, "bulk-move-cage-meta")
        self.assertContains(response, "bulk-move-rule-dialog")
        self.assertContains(response, "Move blocked")

    def test_bulk_move_allows_same_sex_holding_destination(self):
        response = self.client.post(
            reverse("mice:mouse_bulk_action"),
            {
                "bulk_action": "move_cage",
                "confirm_bulk_action": "1",
                "mouse_ids": [self.female.pk, self.extra_female.pk],
                "destination_cage": self.dest_cage.pk,
                "move_date": timezone.localdate().isoformat(),
                "reason": "Bulk move",
                "confirm": "on",
                "next": reverse("mice:mouse_list"),
            },
        )

        self.assertRedirects(response, reverse("mice:mouse_list"))
        self.female.refresh_from_db()
        self.extra_female.refresh_from_db()
        self.assertEqual(self.female.current_cage_id, self.dest_cage.pk)
        self.assertEqual(self.extra_female.current_cage_id, self.dest_cage.pk)

    def test_bulk_move_allows_active_breeder_back_to_own_breeding_cage(self):
        self.dest_cage.purpose = Cage.Purpose.BREEDING
        self.dest_cage.cage_type = Cage.CageType.BREEDING
        self.dest_cage.save(update_fields=["purpose", "cage_type", "updated_at"])
        Breeding.objects.create(
            breeding_code="BULK-BR-MOVE-BACK",
            cage=self.dest_cage,
            male=self.male,
            female_1=self.female,
            start_date=date(2026, 1, 10),
            active=True,
        )

        response = self.client.post(
            reverse("mice:mouse_bulk_action"),
            {
                "bulk_action": "move_cage",
                "confirm_bulk_action": "1",
                "mouse_ids": [self.female.pk],
                "destination_cage": self.dest_cage.pk,
                "move_date": timezone.localdate().isoformat(),
                "reason": "Repair breeding cage",
                "confirm": "on",
                "next": reverse("mice:mouse_list"),
            },
        )

        self.assertRedirects(response, reverse("mice:mouse_list"))
        self.female.refresh_from_db()
        self.assertEqual(self.female.current_cage_id, self.dest_cage.pk)

    def test_bulk_move_blocks_nonmember_into_active_breeding_cage(self):
        self.dest_cage.purpose = Cage.Purpose.BREEDING
        self.dest_cage.cage_type = Cage.CageType.BREEDING
        self.dest_cage.save(update_fields=["purpose", "cage_type", "updated_at"])
        breeding = Breeding.objects.create(
            breeding_code="BULK-BR-BLOCK-INTRUDER",
            cage=self.dest_cage,
            male=self.male,
            female_1=self.female,
            start_date=date(2026, 1, 10),
            active=True,
        )

        response = self.client.post(
            reverse("mice:mouse_bulk_action"),
            {
                "bulk_action": "move_cage",
                "confirm_bulk_action": "1",
                "mouse_ids": [self.extra_female.pk],
                "destination_cage": self.dest_cage.pk,
                "move_date": timezone.localdate().isoformat(),
                "reason": "Bulk move",
                "confirm": "on",
                "next": reverse("mice:mouse_list"),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already has active breeding")
        self.assertContains(response, breeding.breeding_code)
        self.extra_female.refresh_from_db()
        self.assertEqual(self.extra_female.current_cage_id, self.source_cage.pk)
        self.assertFalse(breeding.extra_female_links.filter(mouse=self.extra_female).exists())

    def test_bulk_move_allows_mouse_without_current_cage(self):
        no_cage_mouse = Mouse.objects.create(
            mouse_uid="BULK-NO-CAGE",
            sex=Mouse.Sex.FEMALE,
            status=Mouse.Status.ACTIVE,
            birth_date=date(2026, 1, 1),
            project=self.project,
            strain_line=self.strain,
            current_cage=None,
        )

        response = self.client.post(
            reverse("mice:mouse_bulk_action"),
            {
                "bulk_action": "move_cage",
                "confirm_bulk_action": "1",
                "mouse_ids": [no_cage_mouse.pk],
                "destination_cage": self.dest_cage.pk,
                "move_date": timezone.localdate().isoformat(),
                "reason": "Bulk move",
                "confirm": "on",
                "next": reverse("mice:mouse_list"),
            },
        )

        self.assertRedirects(response, reverse("mice:mouse_list"))
        no_cage_mouse.refresh_from_db()
        self.assertEqual(no_cage_mouse.current_cage_id, self.dest_cage.pk)
        self.assertTrue(CageMembership.objects.filter(mouse=no_cage_mouse, cage=self.dest_cage, is_current=True).exists())

    def test_bulk_end_removes_cage_occupancy_and_clears_experiment(self):
        assignment = MouseExperimentAssignment.objects.create(
            mouse=self.extra_female,
            created_by=self.user,
            updated_by=self.user,
        )

        response = self.client.post(
            reverse("mice:mouse_bulk_action"),
            {
                "bulk_action": "end",
                "confirm_bulk_action": "1",
                "mouse_ids": [self.extra_female.pk],
                "terminal_status": Mouse.Status.EUTHANIZED,
                "end_date": timezone.localdate().isoformat(),
                "reason": "Scheduled endpoint",
                "confirm": "on",
                "next": reverse("mice:mouse_list"),
            },
        )

        self.assertRedirects(response, reverse("mice:mouse_list"))
        self.extra_female.refresh_from_db()
        assignment.refresh_from_db()
        self.assertEqual(self.extra_female.status, Mouse.Status.EUTHANIZED)
        self.assertIsNone(self.extra_female.current_cage_id)
        self.assertIsNotNone(assignment.ended_at)
        self.assertFalse(
            CageMembership.objects.filter(mouse=self.extra_female, is_current=True).exists()
        )
