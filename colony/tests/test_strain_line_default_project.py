import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from colony.forms import StrainLineForm
from colony.models import Cage, Mouse, StrainLine
from core.models import Project, ProjectMembership
from users.models import UserProfile


class StrainLineProjectsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="strainproj",
            email="strainproj@example.test",
            password="x",
        )
        UserProfile.objects.filter(user=self.user).update(role=UserProfile.Role.ADMIN)
        self.client = Client()
        self.client.login(username="strainproj", password="x")
        self.strain = StrainLine.objects.create(line_name="DP-Line", name="DP-Line")
        self.project_a = Project.objects.create(name="Alpha Project", owner=self.user)
        self.project_b = Project.objects.create(name="Beta Project", owner=self.user)
        ProjectMembership.objects.create(
            project=self.project_a,
            user=self.user,
            role=ProjectMembership.Role.MANAGER,
        )
        ProjectMembership.objects.create(
            project=self.project_b,
            user=self.user,
            role=ProjectMembership.Role.MANAGER,
        )
        self.cage = Cage.objects.create(cage_id="DP-C1")

    def test_strain_line_form_saves_projects(self):
        data = {
            "name": "DP-Line",
            "projects": [str(self.project_a.pk)],
            "species": "mouse",
            "source": "",
            "category": StrainLine.Category.COMPOUND_STRAIN,
            "background": StrainLine.BackgroundPreset.C57BL_6J,
            "expected_loci_template": "LocusA",
            "expected_loci_config": json.dumps(
                [
                    {
                        "locus_name": "LocusA",
                        "locus_type": "other_custom",
                        "chromosome_type": "autosomal",
                    }
                ]
            ),
            "is_active": "on",
            "notes": "",
        }
        form = StrainLineForm(data, instance=self.strain, user=self.user, admin_correction_unlocked=True)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(list(saved.projects.values_list("pk", flat=True)), [self.project_a.pk])

    def test_strain_line_detail_lists_related_projects(self):
        other_user = get_user_model().objects.create_user(username="strainproj_other", password="x")
        project_c = Project.objects.create(name="Gamma Project", owner=other_user)
        Mouse.objects.create(
            mouse_uid="M-A1",
            sex=Mouse.Sex.MALE,
            strain_line=self.strain,
            project=self.project_a,
            current_cage=self.cage,
        )
        Mouse.objects.create(
            mouse_uid="M-B1",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.strain,
            project=self.project_b,
            current_cage=self.cage,
        )
        Mouse.objects.create(
            mouse_uid="M-C1",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.strain,
            project=project_c,
            current_cage=self.cage,
        )
        response = self.client.get(reverse("colony:strain_line_detail", args=[self.strain.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alpha Project")
        self.assertContains(response, "Beta Project")
        self.assertContains(response, "Gamma Project")
        self.assertContains(response, "Owners using this line")
        self.assertContains(response, "strainproj")
        self.assertContains(response, "strainproj_other")
        self.assertNotContains(response, "Default project")

    def test_project_detail_lists_linked_strain_line_without_mice(self):
        self.strain.projects.add(self.project_a)
        response = self.client.get(reverse("project_detail", args=[self.project_a.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "DP-Line")
        self.assertContains(response, "0")

    def test_mouse_create_infers_project_when_strain_has_single_project(self):
        Mouse.objects.create(
            mouse_uid="M-FOXP3",
            sex=Mouse.Sex.MALE,
            strain_line=self.strain,
            project=self.project_a,
            current_cage=self.cage,
        )
        url = reverse("mice:mouse_create") + f"?strain_line_id={self.strain.pk}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'value="{self.project_a.pk}" selected')
        self.assertContains(response, f'"{self.strain.pk}": "{self.project_a.pk}"')

    def test_mouse_create_prefills_project_from_single_strain_project(self):
        self.strain.projects.set([self.project_b])
        url = reverse("mice:mouse_create") + f"?strain_line_id={self.strain.pk}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'value="{self.strain.pk}" selected')
        self.assertContains(response, f'value="{self.project_b.pk}" selected')
        self.assertContains(response, "strain-project-map")
        self.assertContains(response, f'"{self.strain.pk}": "{self.project_b.pk}"')
