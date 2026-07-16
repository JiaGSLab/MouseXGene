import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from colony.models import Mouse, MouseGenotypeComponent, StrainLine
from colony.strain_line_choices import CUSTOM_SELECT_VALUE
from core.models import Project, ProjectMembership
from users.models import UserProfile


class StrainLineEditViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="strainedit",
            email="strainedit@example.test",
            password="x",
        )
        UserProfile.objects.filter(user=self.user).update(role=UserProfile.Role.ADMIN)
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
                    "locus_type": "other_custom",
                    "chromosome_type": "autosomal",
                }
            ],
        )

    def _post_data(self, **overrides):
        config = overrides.pop(
            "expected_loci_config",
            [
                {"locus_name": "LocusA", "locus_type": "other_custom", "chromosome_type": "autosomal"},
                {"locus_name": "LocusB", "locus_type": "floxed_allele", "chromosome_type": "autosomal"},
            ],
        )
        data = {
            "name": "EditMe-Renamed",
            "species": "mouse",
            "source": "Vendor X",
            "category": StrainLine.Category.CRE_DRIVER,
            "background": StrainLine.BackgroundPreset.BALB_C,
            "projects": [str(self.project.pk)],
            "expected_loci_template": "\n".join(item["locus_name"] for item in config),
            "expected_loci_config": json.dumps(config),
            "is_active": "on",
            "notes": "updated notes",
            "admin_correction_unlocked": "1",
            "admin_correction_reason": "Admin reviewed correction",
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
        self.assertEqual(list(self.line.projects.values_list("pk", flat=True)), [self.project.pk])
        self.assertEqual(self.line.expected_loci_list(), ["LocusA", "LocusB"])

    def test_create_redirects_to_detail_with_custom_background_and_pdf_upload(self):
        url = reverse("colony:strain_line_create")
        response = self.client.post(
            url,
            {
                "name": "Create-Pdf-Flow",
                "owner": str(self.user.pk),
                "projects": [str(self.project.pk)],
                "species": "mouse",
                "source": "",
                "category": StrainLine.Category.COMPOUND_STRAIN,
                "background": CUSTOM_SELECT_VALUE,
                "background_custom": "C57BL/6JGpt",
                "expected_loci_template": "",
                "expected_loci_config": "[]",
                "is_active": "on",
                "notes": "",
            },
            follow=False,
        )
        line = StrainLine.objects.get(line_name="Create-Pdf-Flow")
        self.assertRedirects(response, reverse("colony:strain_line_detail", args=[line.pk]))
        self.assertEqual(line.background, "C57BL/6JGpt")

        detail_response = self.client.get(reverse("colony:strain_line_detail", args=[line.pk]))
        self.assertContains(detail_response, 'class="strain-pdf-upload-form"')

    def test_edit_view_requires_reason_for_admin_correction(self):
        url = reverse("colony:strain_line_edit", args=[self.line.pk])
        response = self.client.post(url, self._post_data(admin_correction_reason=""))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Correction reason is required")
        self.line.refresh_from_db()
        self.assertEqual(self.line.name, "EditMe")
        self.assertEqual(self.line.expected_loci_list(), ["LocusA"])

    def test_edit_view_uses_project_dropdown_multiselect(self):
        response = self.client.get(reverse("colony:strain_line_edit", args=[self.line.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="strain-project-dropdown"')
        self.assertContains(response, 'id="strain-project-summary"')
        self.assertNotContains(response, 'size="8"')

    def test_edit_view_displays_custom_background(self):
        self.line.background = "C57BL/6JGpt"
        self.line.save(update_fields=["background"])

        response = self.client.get(reverse("colony:strain_line_edit", args=[self.line.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="__custom__" selected')
        self.assertContains(response, 'value="C57BL/6JGpt"')

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

    def test_edit_view_shows_observed_loci_from_mice(self):
        mouse = Mouse.objects.create(
            mouse_uid="M-OBSERVED-LOCUS",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.line,
            project=self.project,
        )
        MouseGenotypeComponent.objects.create(
            mouse=mouse,
            strain_line=self.line,
            locus_name="ImportedExtra",
            zygosity="+/-",
            allele_display_1="+",
            allele_display_2="-",
        )
        url = reverse("colony:strain_line_edit", args=[self.line.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ImportedExtra")
        self.assertContains(response, "LocusA")

    def test_edit_view_allows_clearing_all_loci(self):
        url = reverse("colony:strain_line_edit", args=[self.line.pk])
        response = self.client.post(
            url,
            self._post_data(
                name="EditMe",
                expected_loci_config=[],
                expected_loci_template="",
            ),
        )
        self.assertRedirects(response, reverse("colony:strain_line_detail", args=[self.line.pk]))
        self.line.refresh_from_db()
        self.assertEqual(self.line.expected_loci_list(), [])

    def test_edit_view_clearing_all_loci_removes_mouse_genotype_rows(self):
        mouse = Mouse.objects.create(
            mouse_uid="M-CLEAR-LOCUS",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.line,
            project=self.project,
        )
        mouse.ensure_template_genotype_components(include_strain_template=True)
        url = reverse("colony:strain_line_edit", args=[self.line.pk])
        self.client.post(
            url,
            self._post_data(
                name="EditMe",
                expected_loci_config=[],
                expected_loci_template="",
            ),
        )
        self.assertFalse(MouseGenotypeComponent.objects.filter(mouse=mouse).exists())

    def test_edit_view_removing_observed_locus_propagates_to_mice(self):
        mouse = Mouse.objects.create(
            mouse_uid="M-REMOVE-OBSERVED",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.line,
            project=self.project,
        )
        mouse.ensure_template_genotype_components(include_strain_template=True)
        MouseGenotypeComponent.objects.create(
            mouse=mouse,
            strain_line=self.line,
            locus_name="ImportedExtra",
            zygosity="+/-",
            allele_display_1="+",
            allele_display_2="-",
        )
        config = [
            {"locus_name": "LocusA", "locus_type": "other_custom", "chromosome_type": "autosomal"},
        ]
        url = reverse("colony:strain_line_edit", args=[self.line.pk])
        self.client.post(
            url,
            self._post_data(
                name="EditMe",
                expected_loci_config=config,
                expected_loci_template="LocusA",
            ),
        )
        loci = set(
            MouseGenotypeComponent.objects.filter(mouse=mouse).values_list("locus_name", flat=True)
        )
        self.assertEqual(loci, {"LocusA"})

    def test_edit_view_notes_only_save_keeps_observed_locus_on_mice(self):
        mouse = Mouse.objects.create(
            mouse_uid="M-KEEP-OBSERVED",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.line,
            project=self.project,
        )
        mouse.ensure_template_genotype_components(include_strain_template=True)
        MouseGenotypeComponent.objects.create(
            mouse=mouse,
            strain_line=self.line,
            locus_name="ImportedExtra",
            zygosity="+/-",
            allele_display_1="+",
            allele_display_2="-",
        )
        config = [
            {"locus_name": "LocusA", "locus_type": "other_custom", "chromosome_type": "autosomal"},
            {"locus_name": "ImportedExtra", "locus_type": "other_custom", "chromosome_type": "autosomal"},
        ]
        url = reverse("colony:strain_line_edit", args=[self.line.pk])
        self.client.post(
            url,
            self._post_data(
                name="EditMe",
                notes="only notes changed",
                expected_loci_config=config,
                expected_loci_template="LocusA\nImportedExtra",
            ),
        )
        loci = set(
            MouseGenotypeComponent.objects.filter(mouse=mouse).values_list("locus_name", flat=True)
        )
        self.assertIn("ImportedExtra", loci)

    def test_edit_view_keeps_exact_locus_name_on_mouse_after_template_edit(self):
        self.line.expected_loci_config = [
            {
                "locus_name": "Foxp3 flox",
                "locus_type": "floxed_allele",
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
                "locus_type": "floxed_allele",
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
