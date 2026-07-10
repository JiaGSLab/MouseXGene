from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from breeding.forms import BreedingForm, LitterForm, LitterPupFormSet
from breeding.models import Breeding, Litter, LitterPup
from colony.models import Cage, Mouse, StrainLine
from core.models import Project


class BreedingAndLitterIntegrityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="workflow-integrity")
        self.project = Project.objects.create(name="Workflow Integrity", owner=self.user)
        self.strain = StrainLine.objects.create(line_name="Workflow Integrity Line")
        self.cage = Cage.objects.create(cage_id="WF-BR-1", project=self.project)
        self.sire = Mouse.objects.create(
            mouse_uid="WF-SIRE",
            sex=Mouse.Sex.MALE,
            project=self.project,
            strain_line=self.strain,
            current_cage=self.cage,
        )
        self.dam = Mouse.objects.create(
            mouse_uid="WF-DAM",
            sex=Mouse.Sex.FEMALE,
            project=self.project,
            strain_line=self.strain,
            current_cage=self.cage,
        )
        self.breeding = Breeding.objects.create(
            breeding_code="WF-BR-001",
            cage=self.cage,
            male=self.sire,
            female_1=self.dam,
            start_date=date(2026, 1, 1),
        )

    def test_active_breeder_cannot_be_assigned_to_second_active_breeding(self):
        other_dam = Mouse.objects.create(
            mouse_uid="WF-DAM-2",
            sex=Mouse.Sex.FEMALE,
            project=self.project,
            strain_line=self.strain,
        )
        form = BreedingForm(
            data={
                "sire": self.sire.pk,
                "dams": [other_dam.pk],
                "cage_assignment_mode": BreedingForm.CageAssignmentMode.AUTO,
                "breeding_type": BreedingForm.AUTO_BREEDING_TYPE,
                "start_date": "2026-02-01",
                "status": Breeding.Status.SETUP,
                "active": "on",
            },
            user=self.user,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("already in active breeding", str(form.errors))

    def test_general_litter_edit_does_not_expose_workflow_state(self):
        litter = Litter.objects.create(
            breeding=self.breeding,
            birth_date=date(2026, 1, 22),
        )

        form = LitterForm(instance=litter)

        self.assertNotIn("wean_date", form.fields)
        self.assertNotIn("litter_status", form.fields)

    def test_linked_pup_row_cannot_be_deleted(self):
        litter = Litter.objects.create(
            breeding=self.breeding,
            birth_date=date(2026, 1, 22),
        )
        pup_mouse = Mouse.objects.create(
            mouse_uid="WF-PUP",
            sex=Mouse.Sex.FEMALE,
            project=self.project,
            strain_line=self.strain,
        )
        pup = LitterPup.objects.create(litter=litter, sort_order=1, mouse=pup_mouse)
        formset = LitterPupFormSet(
            data={
                "pups-TOTAL_FORMS": "1",
                "pups-INITIAL_FORMS": "1",
                "pups-MIN_NUM_FORMS": "0",
                "pups-MAX_NUM_FORMS": "1000",
                "pups-0-id": str(pup.pk),
                "pups-0-litter": str(litter.pk),
                "pups-0-sort_order": "1",
                "pups-0-sex": Mouse.Sex.FEMALE,
                "pups-0-DELETE": "on",
            },
            instance=litter,
            prefix="pups",
        )

        self.assertFalse(formset.is_valid())
        self.assertIn("cannot be deleted", str(formset.non_form_errors()))
