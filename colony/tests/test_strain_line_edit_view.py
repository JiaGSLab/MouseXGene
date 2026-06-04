import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from colony.models import Mouse, MouseGenotypeComponent, StrainLine
from core.models import Project, ProjectMembership
from users.models import UserProfile


class StrainLineEditViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="strainedit", password="x")
        UserProfile.objects.filter(user=self.user).update(role=UserProfile.Role.MANAGER)
        self.client = Client()
        self.client.login(username="strainedit", password="x")
        self.project = Project.objects.create(name="Edit Default Project", owner=self.user)
        ProjectMembership.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectMembership.Role.MANAGER,
        )
        self.line = StrainLine.objects.create(
            line_name="EditMe",
            name="EditMe",
            notes="old notes",
            source="",
            expected_loci_template="LocusA",
            expected_loci_config=[
                {
                    "locus_name": "LocusA",
                    "locus_type": "custom",
                    "chromosome_type": "autosomal",
                }
            ],
        )

    def _post_data(self, **overrides):
        config = overrides.pop(
            "expected_loci_config",
            [
                {"locus_name": "LocusA", "locus_type": "custom", "chromosome_type": "autosomal"},
                {"locus_name": "LocusB", "locus_type": "flox", "chromosome_type": "autosomal"},
            ],
        )
        data = {
            "name": "EditMe-Renamed",
            "species": "mouse",
            "source": "Vendor X",
            "category": StrainLine.Category.CRE_DRIVER,
            "background": StrainLine.BackgroundPreset.BALB_C,
            "default_project": str(self.project.pk),
            "expected_loci_template": "\n".join(item["locus_name"] for item in config),
            "expected_loci_config": json.dumps(config),
            "is_active": "on",
            "notes": "updated notes",
        }
        data.update(overrides)
        return data

    def test_edit_view_persists_field_changes(self):
        url = reverse("colony:strain_line_edit", args=[self.line.pk])
        response = self.client.post(url, self._post_data(), follow=False)
        self.assertRedirects(response, reverse("colony:strain_line_detail", args=[self.line.pk]))
        self.line.refresh_from_db()
        self.assertEqual(self.line.name, "EditMe-Renamed")
        self.assertEqual(self.line.line_name, "EditMe-Renamed")
        self.assertEqual(self.line.notes, "updated notes")
        self.assertEqual(self.line.source, "Vendor X")
        self.assertEqual(self.line.category, StrainLine.Category.CRE_DRIVER)
        self.assertEqual(self.line.background, StrainLine.BackgroundPreset.BALB_C)
        self.assertEqual(self.line.default_project_id, self.project.pk)
        self.assertEqual(self.line.expected_loci_list(), ["LocusA", "LocusB"])

    def test_edit_view_detail_shows_saved_changes(self):
        url = reverse("colony:strain_line_edit", args=[self.line.pk])
        self.client.post(url, self._post_data())
        detail = self.client.get(reverse("colony:strain_line_detail", args=[self.line.pk]))
        self.assertContains(detail, "EditMe-Renamed")
        self.assertContains(detail, "updated notes")
        self.assertContains(detail, "Vendor X")
        self.assertContains(detail, "Edit Default Project")
        self.assertContains(detail, "LocusA, LocusB")

    def test_edit_view_propagates_new_template_locus_to_mice(self):
        mouse = Mouse.objects.create(
            mouse_uid="M-EDIT-STRAIN",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.line,
            project=self.project,
        )
        mouse.ensure_template_genotype_components(include_strain_template=True)
        url = reverse("colony:strain_line_edit", args=[self.line.pk])
        self.client.post(url, self._post_data())
        loci = set(
            MouseGenotypeComponent.objects.filter(mouse=mouse).values_list("locus_name", flat=True)
        )
        self.assertIn("LocusA", loci)
        self.assertIn("LocusB", loci)

    def test_edit_view_keeps_exact_locus_name_on_mouse_after_template_edit(self):
        self.line.expected_loci_config = [
            {
                "locus_name": "Foxp3 flox",
                "locus_type": "flox",
                "chromosome_type": "autosomal",
            }
        ]
        self.line.expected_loci_template = "Foxp3 flox"
        self.line.save()
        mouse = Mouse.objects.create(
            mouse_uid="M-FOXP3-FLOX",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.line,
            project=self.project,
        )
        mouse.ensure_template_genotype_components(include_strain_template=True)
        MouseGenotypeComponent.objects.filter(mouse=mouse, locus_name="Foxp3 flox").update(zygosity="+/+")
        config = [
            {
                "locus_name": "Foxp3 flox",
                "locus_type": "flox",
                "chromosome_type": "autosomal",
            }
        ]
        url = reverse("colony:strain_line_edit", args=[self.line.pk])
        self.client.post(
            url,
            self._post_data(
                name="EditMe",
                expected_loci_config=config,
                expected_loci_template="Foxp3 flox",
                notes="still there",
            ),
        )
        comp = MouseGenotypeComponent.objects.get(mouse=mouse, locus_name="Foxp3 flox")
        self.assertEqual(comp.zygosity, "+/+")
