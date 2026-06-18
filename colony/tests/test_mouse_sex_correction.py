from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from breeding.models import Breeding
from colony.models import Cage, Mouse, MouseGenotypeComponent, StrainLine
from core.models import Project, ProjectMembership
from users.models import UserProfile


class MouseSexCorrectionTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user(username="sex-manager", password="x")
        UserProfile.objects.filter(user=self.manager).update(role=UserProfile.Role.MEMBER)
        self.admin = get_user_model().objects.create_superuser(
            username="sex-admin",
            email="sex-admin@example.test",
            password="x",
        )
        UserProfile.objects.filter(user=self.admin).update(role=UserProfile.Role.ADMIN)
        self.project = Project.objects.create(name="Sex Correction Project", owner=self.admin)
        ProjectMembership.objects.create(
            project=self.project,
            user=self.manager,
            role=ProjectMembership.Role.MANAGER,
        )
        self.strain = StrainLine.objects.create(line_name="Sex Correction Line", name="Sex Correction Line")
        self.cage = Cage.objects.create(cage_id="SEX-CAGE-1", project=self.project)
        self.mouse = Mouse.objects.create(
            mouse_uid="SEX-M-1",
            sex=Mouse.Sex.MALE,
            status=Mouse.Status.ACTIVE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage,
        )
        self.client = Client()
        self.client.login(username="sex-manager", password="x")

    def _post_correction(self, target_sex):
        return self.client.post(
            reverse("mice:mouse_correct_sex", args=[self.mouse.pk]),
            {
                "sex": target_sex,
                "reason": "Physical recheck confirmed sex",
                "confirm": "on",
            },
        )

    def test_project_manager_can_correct_uncomplicated_mouse_sex(self):
        detail = self.client.get(reverse("mice:mouse_detail", args=[self.mouse.pk]))
        self.assertContains(detail, "Correct Sex")

        response = self._post_correction(Mouse.Sex.FEMALE)

        self.assertRedirects(response, reverse("mice:mouse_detail", args=[self.mouse.pk]))
        self.mouse.refresh_from_db()
        self.assertEqual(self.mouse.sex, Mouse.Sex.FEMALE)

    def test_correct_sex_blocks_mixed_holding_cage(self):
        Mouse.objects.create(
            mouse_uid="SEX-M-OTHER",
            sex=Mouse.Sex.MALE,
            status=Mouse.Status.ACTIVE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage,
        )

        response = self._post_correction(Mouse.Sex.FEMALE)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cannot be housed together")
        self.mouse.refresh_from_db()
        self.assertEqual(self.mouse.sex, Mouse.Sex.MALE)

    def test_correct_sex_blocks_conflicting_sire_role(self):
        dam = Mouse.objects.create(
            mouse_uid="SEX-DAM-1",
            sex=Mouse.Sex.FEMALE,
            status=Mouse.Status.ACTIVE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage,
        )
        Breeding.objects.create(
            breeding_code="SEX-BR-1",
            cage=self.cage,
            male=self.mouse,
            female_1=dam,
            start_date=timezone.localdate(),
        )

        response = self._post_correction(Mouse.Sex.FEMALE)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "recorded as sire")
        self.mouse.refresh_from_db()
        self.assertEqual(self.mouse.sex, Mouse.Sex.MALE)

    def test_correct_sex_blocks_y_linked_female(self):
        MouseGenotypeComponent.objects.create(
            mouse=self.mouse,
            strain_line=self.strain,
            locus_name="Sry",
            chromosome_type=MouseGenotypeComponent.ChromosomeType.Y_LINKED,
            allele_display_1="+",
            allele_display_2="Y",
        )

        response = self._post_correction(Mouse.Sex.FEMALE)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Y-linked genotype rows")
        self.mouse.refresh_from_db()
        self.assertEqual(self.mouse.sex, Mouse.Sex.MALE)

    def test_admin_cannot_change_sex_through_generic_mouse_edit(self):
        self.client.logout()
        self.client.login(username="sex-admin", password="x")

        response = self.client.post(
            reverse("mice:mouse_edit", args=[self.mouse.pk]),
            {
                "admin_correction_unlocked": "1",
                "admin_correction_reason": "Admin reviewed correction",
                "mouse_uid": self.mouse.mouse_uid,
                "sex": Mouse.Sex.FEMALE,
                "birth_date": "",
                "death_date": "",
                "euthanasia_date": "",
                "death_reason": "",
                "status": Mouse.Status.ACTIVE,
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

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Use Correct Sex")
        self.mouse.refresh_from_db()
        self.assertEqual(self.mouse.sex, Mouse.Sex.MALE)
