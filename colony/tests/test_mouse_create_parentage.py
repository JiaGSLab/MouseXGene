from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from breeding.models import Breeding
from colony.forms import MouseForm, MouseParentageMode
from colony.models import Cage, Mouse, StrainLine
from core.models import Project, ProjectMembership
from users.models import UserProfile


class MouseCreateParentageTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="mouse_parentage_user", password="x")
        UserProfile.objects.filter(user=self.user).update(role=UserProfile.Role.MANAGER)
        self.project = Project.objects.create(name="Mouse Parentage Project", owner=self.user, is_active=True)
        ProjectMembership.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectMembership.Role.MANAGER,
        )
        self.strain = StrainLine.objects.create(line_name="ParentageLine", name="ParentageLine")
        self.cage = Cage.objects.create(
            cage_id="PARENTAGE-BR-CAGE",
            status=Cage.Status.ACTIVE,
            purpose=Cage.Purpose.BREEDING,
        )
        self.pup_cage = Cage.objects.create(
            cage_id="PARENTAGE-PUP-CAGE",
            status=Cage.Status.ACTIVE,
            purpose=Cage.Purpose.HOLDING,
        )
        self.sire = Mouse.objects.create(
            mouse_uid="PARENTAGE-SIRE",
            sex=Mouse.Sex.MALE,
            status=Mouse.Status.ACTIVE,
            project=self.project,
            strain_line=self.strain,
        )
        self.dam1 = Mouse.objects.create(
            mouse_uid="PARENTAGE-DAM-1",
            sex=Mouse.Sex.FEMALE,
            status=Mouse.Status.ACTIVE,
            project=self.project,
            strain_line=self.strain,
        )
        self.dam2 = Mouse.objects.create(
            mouse_uid="PARENTAGE-DAM-2",
            sex=Mouse.Sex.FEMALE,
            status=Mouse.Status.ACTIVE,
            project=self.project,
            strain_line=self.strain,
        )
        self.breeding = Breeding.objects.create(
            breeding_code="PARENTAGE-BR-1",
            cage=self.cage,
            breeding_type=Breeding.BreedingType.TRIO,
            male=self.sire,
            female_1=self.dam1,
            female_2=self.dam2,
            start_date="2026-01-01",
            active=True,
        )
        self.breeding.sync_members_from_legacy_fields()

    def _mouse_form_payload(self, **overrides):
        data = {
            "mouse_uid": "PARENTAGE-PUP",
            "sex": Mouse.Sex.MALE,
            "status": Mouse.Status.ACTIVE,
            "strain_line": self.strain.pk,
            "project": self.project.pk,
            "current_cage": self.pup_cage.pk,
            "parentage_mode": MouseParentageMode.NONE,
        }
        data.update(overrides)
        return data

    def test_mouse_form_breeding_cage_parentage_sets_possible_dams(self):
        form = MouseForm(
            data=self._mouse_form_payload(
                parentage_mode=MouseParentageMode.BREEDING_CAGE,
                source_breeding=self.breeding.pk,
            ),
            user=self.user,
        )

        self.assertTrue(form.is_valid(), form.errors)
        mouse = form.save()

        self.assertEqual(mouse.sire_id, self.sire.pk)
        self.assertIsNone(mouse.dam_id)
        self.assertEqual(mouse.source_breeding_id, self.breeding.pk)
        self.assertEqual(set(mouse.possible_dams.values_list("mouse_uid", flat=True)), {"PARENTAGE-DAM-1", "PARENTAGE-DAM-2"})

    def test_mouse_form_manual_multiple_possible_dams(self):
        form = MouseForm(
            data=self._mouse_form_payload(
                parentage_mode=MouseParentageMode.SELECT_PARENTS,
                sire=self.sire.pk,
                possible_dams=[self.dam1.pk, self.dam2.pk],
            ),
            user=self.user,
        )

        self.assertTrue(form.is_valid(), form.errors)
        mouse = form.save()

        self.assertEqual(mouse.sire_id, self.sire.pk)
        self.assertIsNone(mouse.dam_id)
        self.assertIsNone(mouse.source_breeding_id)
        self.assertEqual(set(mouse.possible_dams.values_list("mouse_uid", flat=True)), {"PARENTAGE-DAM-1", "PARENTAGE-DAM-2"})

    def test_mouse_form_no_parentage_clears_hidden_submitted_parent_values(self):
        form = MouseForm(
            data=self._mouse_form_payload(
                parentage_mode=MouseParentageMode.NONE,
                source_breeding=self.breeding.pk,
                sire=self.sire.pk,
                possible_dams=[self.dam1.pk, self.dam2.pk],
            ),
            user=self.user,
        )

        self.assertTrue(form.is_valid(), form.errors)
        mouse = form.save()

        self.assertIsNone(mouse.sire_id)
        self.assertIsNone(mouse.dam_id)
        self.assertIsNone(mouse.source_breeding_id)
        self.assertFalse(mouse.possible_dams.exists())

    def test_batch_create_uses_breeding_cage_parentage(self):
        client = Client()
        client.login(username="mouse_parentage_user", password="x")
        response = client.post(
            reverse("mice:mouse_create"),
            {
                "birth_date": "2026-03-01",
                "status": Mouse.Status.ACTIVE,
                "strain_line": str(self.strain.pk),
                "project": str(self.project.pk),
                "current_cage": str(self.pup_cage.pk),
                "parentage_mode": MouseParentageMode.BREEDING_CAGE,
                "source_breeding": str(self.breeding.pk),
                "batch_row_count": "1",
                "batch_mouse_uid_0": "PARENTAGE-BATCH-PUP",
                "batch_sex_0": Mouse.Sex.FEMALE,
                "batch_ear_tag_0": "",
                "batch_toe_tag_0": "",
                "genotype_row_count": "0",
                "form_action": "create",
            },
        )

        mouse = Mouse.objects.get(mouse_uid="PARENTAGE-BATCH-PUP")
        self.assertRedirects(response, reverse("mice:mouse_detail", args=[mouse.pk]))
        self.assertEqual(mouse.sire_id, self.sire.pk)
        self.assertIsNone(mouse.dam_id)
        self.assertEqual(mouse.source_breeding_id, self.breeding.pk)
        self.assertEqual(set(mouse.possible_dams.values_list("mouse_uid", flat=True)), {"PARENTAGE-DAM-1", "PARENTAGE-DAM-2"})
