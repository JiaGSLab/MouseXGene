from django.contrib.auth import get_user_model
from django.test import TestCase

from breeding.forms import BreedingForm, resolve_cage_from_lookup
from colony.models import Cage, Mouse, StrainLine
from core.models import Project


class BreedingCageLookupTests(TestCase):
    def setUp(self):
        self.cage_a = Cage.objects.create(cage_id="BR-CAGE-A1", status=Cage.Status.ACTIVE)
        self.cage_b = Cage.objects.create(cage_id="BR-CAGE-A2", status=Cage.Status.ACTIVE)
        Cage.objects.create(cage_id="BR-CAGE-RET", status=Cage.Status.RETIRED)

    def test_resolve_exact_match(self):
        cage, err = resolve_cage_from_lookup("BR-CAGE-A1")
        self.assertIsNone(err)
        self.assertEqual(cage, self.cage_a)

    def test_resolve_partial_single_match(self):
        cage, err = resolve_cage_from_lookup("CAGE-A1")
        self.assertIsNone(err)
        self.assertEqual(cage, self.cage_a)

    def test_resolve_partial_multiple_matches(self):
        cage, err = resolve_cage_from_lookup("BR-CAGE-A")
        self.assertIsNone(cage)
        self.assertIn("Multiple cages match", err or "")

    def test_resolve_no_match(self):
        cage, err = resolve_cage_from_lookup("DOES-NOT-EXIST")
        self.assertIsNone(cage)
        self.assertIn("Create the cage first", err or "")

    def test_form_rejects_unknown_lookup(self):
        user = get_user_model().objects.create_user(username="cage_lookup_user", password="x")
        strain = StrainLine.objects.create(line_name="LookupStrain", name="LookupStrain")
        project = Project.objects.create(name="LookupProject", owner=user)
        sire = Mouse.objects.create(
            mouse_uid="M-LOOKUP-SIRE",
            sex=Mouse.Sex.MALE,
            project=project,
            strain_line=strain,
        )
        dam = Mouse.objects.create(
            mouse_uid="M-LOOKUP-DAM",
            sex=Mouse.Sex.FEMALE,
            project=project,
            strain_line=strain,
        )
        form = BreedingForm(
            data={
                "sire": sire.pk,
                "dams": [dam.pk],
                "cage_lookup": "UNKNOWN-CAGE",
                "breeding_type": "pair",
                "start_date": "2026-01-01",
                "status": "setup",
                "active": True,
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("cage_lookup", form.errors)

    def test_form_accepts_partial_lookup(self):
        user = get_user_model().objects.create_user(username="cage_lookup_user2", password="x")
        strain = StrainLine.objects.create(line_name="LookupStrain2", name="LookupStrain2")
        project = Project.objects.create(name="LookupProject2", owner=user)
        sire = Mouse.objects.create(
            mouse_uid="M-LOOKUP-SIRE-2",
            sex=Mouse.Sex.MALE,
            project=project,
            strain_line=strain,
        )
        dam = Mouse.objects.create(
            mouse_uid="M-LOOKUP-DAM-2",
            sex=Mouse.Sex.FEMALE,
            project=project,
            strain_line=strain,
        )
        form = BreedingForm(
            data={
                "sire": sire.pk,
                "dams": [dam.pk],
                "cage_lookup": "CAGE-A2",
                "breeding_type": "pair",
                "start_date": "2026-01-01",
                "status": "setup",
                "active": True,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["cage"], self.cage_b)
