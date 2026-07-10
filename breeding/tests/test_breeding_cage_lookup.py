from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from breeding.cage_autocreate import generate_auto_cage_id
from breeding.forms import BreedingForm, resolve_cage_from_lookup
from breeding.models import Breeding
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

    def test_auto_cage_id_uses_short_date_and_sequence(self):
        self.assertEqual(generate_auto_cage_id("CAGE-BR", when=date(2026, 6, 22)), "CAGE-BR-260622-001")
        Cage.objects.create(cage_id="CAGE-BR-260622-001")

        self.assertEqual(generate_auto_cage_id("CAGE-BR", when=date(2026, 6, 22)), "CAGE-BR-260622-002")

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

    def test_form_auto_creates_breeding_cage(self):
        user = get_user_model().objects.create_user(username="cage_auto_user", password="x")
        strain = StrainLine.objects.create(line_name="AutoCageStrain", name="AutoCageStrain")
        project = Project.objects.create(name="AutoCageProject", owner=user)
        sire = Mouse.objects.create(
            mouse_uid="M-AUTO-CAGE-SIRE",
            sex=Mouse.Sex.MALE,
            project=project,
            strain_line=strain,
        )
        dam = Mouse.objects.create(
            mouse_uid="M-AUTO-CAGE-DAM",
            sex=Mouse.Sex.FEMALE,
            project=project,
            strain_line=strain,
        )
        form = BreedingForm(
            data={
                "sire": sire.pk,
                "dams": [dam.pk],
                "cage_assignment_mode": BreedingForm.CageAssignmentMode.AUTO,
                "auto_cage_id": "AUTO-BR-CAGE-1",
                "breeding_type": "pair",
                "start_date": "2026-01-01",
                "status": "setup",
                "active": True,
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        breeding = form.save()

        self.assertEqual(breeding.cage.cage_id, "AUTO-BR-CAGE-1")
        self.assertEqual(breeding.cage.cage_type, Cage.CageType.BREEDING)
        self.assertEqual(breeding.cage.purpose, Cage.Purpose.BREEDING)
        self.assertEqual(breeding.cage.project_id, project.pk)
        self.assertEqual(form.created_auto_cage, breeding.cage)

    def test_form_warns_but_allows_cross_project_breeders(self):
        user_a = get_user_model().objects.create_user(username="cross_project_a", first_name="Alice")
        user_b = get_user_model().objects.create_user(username="cross_project_b", first_name="Bob")
        strain = StrainLine.objects.create(line_name="CrossProjectStrain", name="CrossProjectStrain")
        project_a = Project.objects.create(name="Cross Project A", owner=user_a)
        project_b = Project.objects.create(name="Cross Project B", owner=user_b)
        sire = Mouse.objects.create(
            mouse_uid="M-CROSS-SIRE",
            sex=Mouse.Sex.MALE,
            project=project_a,
            strain_line=strain,
        )
        dam = Mouse.objects.create(
            mouse_uid="M-CROSS-DAM",
            sex=Mouse.Sex.FEMALE,
            project=project_b,
            strain_line=strain,
        )

        form = BreedingForm(
            data={
                "sire": sire.pk,
                "dams": [dam.pk],
                "cage": self.cage_a.pk,
                "breeding_type": "pair",
                "start_date": "2026-01-01",
                "status": "setup",
                "active": True,
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(any("multiple projects" in msg for msg in form.warning_messages))
        self.assertTrue(any("multiple users" in msg for msg in form.warning_messages))


class BreedingTypeInferenceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="breeding_type_user", password="x")
        self.strain = StrainLine.objects.create(line_name="BreedingTypeStrain", name="BreedingTypeStrain")
        self.project = Project.objects.create(name="BreedingTypeProject", owner=self.user)
        self.sire = Mouse.objects.create(
            mouse_uid="BT-SIRE",
            sex=Mouse.Sex.MALE,
            project=self.project,
            strain_line=self.strain,
        )
        self.dam_1 = Mouse.objects.create(
            mouse_uid="BT-DAM-1",
            sex=Mouse.Sex.FEMALE,
            project=self.project,
            strain_line=self.strain,
        )
        self.dam_2 = Mouse.objects.create(
            mouse_uid="BT-DAM-2",
            sex=Mouse.Sex.FEMALE,
            project=self.project,
            strain_line=self.strain,
        )
        self.dam_3 = Mouse.objects.create(
            mouse_uid="BT-DAM-3",
            sex=Mouse.Sex.FEMALE,
            project=self.project,
            strain_line=self.strain,
        )

    def _form(self, dams, breeding_type=BreedingForm.AUTO_BREEDING_TYPE):
        return BreedingForm(
            data={
                "sire": self.sire.pk,
                "dams": [dam.pk for dam in dams],
                "cage_assignment_mode": BreedingForm.CageAssignmentMode.AUTO,
                "breeding_type": breeding_type,
                "start_date": "2026-01-01",
                "status": Breeding.Status.SETUP,
                "active": "on",
            }
        )

    def test_auto_breeding_type_uses_pair_for_one_dam(self):
        form = self._form([self.dam_1])

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["breeding_type"], Breeding.BreedingType.PAIR)

    def test_auto_breeding_type_uses_trio_for_two_dams(self):
        form = self._form([self.dam_1, self.dam_2])

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["breeding_type"], Breeding.BreedingType.TRIO)
        self.assertEqual(form.cleaned_data["female_2"], self.dam_2)

    def test_auto_breeding_type_uses_custom_for_three_dams(self):
        form = self._form([self.dam_1, self.dam_2, self.dam_3])

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["breeding_type"], Breeding.BreedingType.CUSTOM)
        self.assertEqual(form.cleaned_data["extra_females"], [self.dam_3])

    def test_omitted_breeding_type_defaults_to_auto(self):
        form = self._form([self.dam_1, self.dam_2], breeding_type="")

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["breeding_type"], Breeding.BreedingType.TRIO)

    def test_manual_custom_can_override_recommended_type(self):
        form = self._form([self.dam_1], breeding_type=Breeding.BreedingType.CUSTOM)

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["breeding_type"], Breeding.BreedingType.CUSTOM)

    def test_manual_pair_rejects_multiple_dams(self):
        form = self._form([self.dam_1, self.dam_2], breeding_type=Breeding.BreedingType.PAIR)

        self.assertFalse(form.is_valid())
        self.assertIn("Pair breeding requires exactly 1 dam", str(form.errors))

    def test_manual_trio_error_points_to_breeder_selection(self):
        form = self._form([self.dam_1], breeding_type=Breeding.BreedingType.TRIO)

        self.assertFalse(form.is_valid())
        self.assertIn("Select one more female in Breeder Selection", str(form.errors))
