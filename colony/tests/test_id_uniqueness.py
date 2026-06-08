from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from colony.forms import CageForm, MouseForm
from colony.id_uniqueness import validate_cage_id_available, validate_mouse_uid_available
from colony.models import Cage, Mouse, StrainLine
from core.models import Project


class IdUniquenessTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="uidtest", password="x")
        self.strain = StrainLine.objects.create(line_name="UID-Strain", name="UID-Strain")
        self.project = Project.objects.create(name="UID-Project", owner=self.user)
        self.retired_cage = Cage.objects.create(
            cage_id="OLD-CAGE-1",
            status=Cage.Status.RETIRED,
        )
        self.dead_mouse = Mouse.objects.create(
            mouse_uid="OLD-MOUSE-1",
            strain_line=self.strain,
            project=self.project,
            status=Mouse.Status.DEAD,
        )

    def test_cannot_reuse_retired_cage_id(self):
        with self.assertRaises(ValidationError):
            validate_cage_id_available("OLD-CAGE-1")

    def test_cannot_reuse_retired_cage_id_case_insensitive(self):
        with self.assertRaises(ValidationError):
            validate_cage_id_available("old-cage-1")

    def test_cage_form_rejects_reused_id(self):
        form = CageForm(data={"cage_id": "OLD-CAGE-1", "purpose": Cage.Purpose.HOLDING, "status": Cage.Status.ACTIVE})
        self.assertFalse(form.is_valid())
        self.assertIn("already used", str(form.errors))

    def test_cannot_reuse_dead_mouse_uid(self):
        with self.assertRaises(ValidationError):
            validate_mouse_uid_available("OLD-MOUSE-1")

    def test_mouse_form_rejects_reused_uid(self):
        form = MouseForm(
            data={
                "mouse_uid": "OLD-MOUSE-1",
                "sex": Mouse.Sex.MALE,
                "status": Mouse.Status.ACTIVE,
                "strain_line": self.strain.pk,
                "project": self.project.pk,
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("already used", str(form.errors))

    def test_edit_allows_same_cage_id(self):
        form = CageForm(
            data={
                "cage_id": self.retired_cage.cage_id,
                "cage_type": self.retired_cage.cage_type,
                "purpose": self.retired_cage.purpose,
                "status": self.retired_cage.status,
            },
            instance=self.retired_cage,
        )
        self.assertTrue(form.is_valid(), form.errors)
