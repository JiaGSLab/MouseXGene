from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from breeding.forms import BreedingForm
from breeding.models import Breeding
from colony.cage_lifecycle import ensure_breeding_for_cage
from colony.models import Cage, Mouse, StrainLine
from core.models import Project


class BreedingCodeRetryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="breeding_retry", password="x")
        self.project = Project.objects.create(name="Breeding Retry Project", owner=self.user)
        self.strain = StrainLine.objects.create(line_name="Breeding Retry Strain", is_active=True)
        self.cage = Cage.objects.create(cage_id="RETRY-CAGE", purpose=Cage.Purpose.BREEDING)
        self.other_cage = Cage.objects.create(cage_id="RETRY-OTHER", purpose=Cage.Purpose.BREEDING)
        self.sire = Mouse.objects.create(
            mouse_uid="RETRY-SIRE",
            sex=Mouse.Sex.MALE,
            project=self.project,
            strain_line=self.strain,
            current_cage=self.cage,
        )
        self.dam = Mouse.objects.create(
            mouse_uid="RETRY-DAM",
            sex=Mouse.Sex.FEMALE,
            project=self.project,
            strain_line=self.strain,
            current_cage=self.cage,
        )
        Breeding.objects.filter(cage=self.cage).delete()

    def test_cage_breeding_creation_retries_code_collision(self):
        Breeding.objects.create(
            breeding_code="BR-RETRY-001",
            cage=self.other_cage,
            breeding_type=Breeding.BreedingType.PAIR,
            male=self.sire,
            female_1=self.dam,
            start_date=timezone.localdate(),
        )
        with patch(
            "colony.cage_lifecycle._generate_breeding_code",
            side_effect=["BR-RETRY-001", "BR-RETRY-002"],
        ):
            breeding = ensure_breeding_for_cage(self.cage)
        self.assertIsNotNone(breeding)
        self.assertEqual(breeding.breeding_code, "BR-RETRY-002")

    def test_form_save_retries_auto_generated_code_collision(self):
        form = BreedingForm(
            data={
                "breeding_code": "",
                "cage": self.cage.pk,
                "breeding_type": Breeding.BreedingType.PAIR,
                "sire": self.sire.pk,
                "dams": [self.dam.pk],
                "start_date": timezone.localdate().isoformat(),
                "status": Breeding.Status.SETUP,
                "active": "on",
                "notes": "",
            }
        )
        with patch(
            "breeding.forms.BreedingForm._generate_breeding_code",
            side_effect=["BR-FORM-001", "BR-FORM-002"],
        ):
            self.assertTrue(form.is_valid(), form.errors)
            Breeding.objects.create(
                breeding_code="BR-FORM-001",
                cage=self.other_cage,
                breeding_type=Breeding.BreedingType.PAIR,
                male=self.sire,
                female_1=self.dam,
                start_date=timezone.localdate(),
            )
            breeding = form.save()
        self.assertEqual(breeding.breeding_code, "BR-FORM-002")
