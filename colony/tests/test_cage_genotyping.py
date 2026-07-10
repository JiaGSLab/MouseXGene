from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import Client, TestCase
from django.urls import reverse

from colony.models import Cage, Mouse, MouseGenotypeComponent, StrainLine
from core.models import Project, ProjectMembership
from users.models import UserProfile


class CageGenotypingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="cagegt", password="x")
        UserProfile.objects.filter(user=self.user).update(role=UserProfile.Role.MANAGER)
        self.client = Client()
        self.client.login(username="cagegt", password="x")
        self.project = Project.objects.create(name="Cage GT Project", owner=self.user)
        ProjectMembership.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectMembership.Role.MANAGER,
        )
        self.strain = StrainLine.objects.create(
            line_name="Cage GT Line",
            name="Cage GT Line",
            expected_loci_template="Lyz2-Cre\nGpnmb flox",
        )
        self.cage = Cage.objects.create(cage_id="CAGE-GT-1", project=self.project)
        self.mouse_a = Mouse.objects.create(
            mouse_uid="GT-M001",
            sex=Mouse.Sex.MALE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage,
        )
        self.mouse_b = Mouse.objects.create(
            mouse_uid="GT-M002",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage,
        )

    def test_cage_detail_links_to_cage_genotyping(self):
        response = self.client.get(reverse("colony:cage_detail", args=[self.cage.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Edit Genotyping Results")
        self.assertContains(response, reverse("colony:cage_genotyping_edit", args=[self.cage.pk]))

    def test_cage_genotyping_page_lists_current_mice_and_loci(self):
        response = self.client.get(reverse("colony:cage_genotyping_edit", args=[self.cage.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Edit Genotyping Results: Cage CAGE-GT-1")
        self.assertContains(response, "GT-M001")
        self.assertContains(response, "GT-M002")
        self.assertContains(response, "Lyz2-Cre")
        self.assertContains(response, "Gpnmb flox")
        self.assertContains(response, "data-genotype-select")
        self.assertContains(response, "data-locus-type")
        self.assertContains(response, "Custom genotype (e.g. Cre/+ or fl/fl)")
        self.assertContains(response, "CreERT2 het (CreERT2/+)")
        self.assertContains(response, f'name="mouse_{self.mouse_a.pk}_genotype_display_0"')
        self.assertContains(response, f'name="mouse_{self.mouse_b.pk}_genotype_display_1"')
        self.assertEqual(MouseGenotypeComponent.objects.count(), 0)

    def test_cage_genotyping_post_updates_each_mouse(self):
        response = self.client.post(
            reverse("colony:cage_genotyping_edit", args=[self.cage.pk]),
            {
                f"mouse_{self.mouse_a.pk}_genotype_display_0": "Cre/+",
                f"mouse_{self.mouse_a.pk}_genotype_display_1": "fl/fl",
                f"mouse_{self.mouse_b.pk}_genotype_display_0": "+/+",
                f"mouse_{self.mouse_b.pk}_genotype_display_1": "fl/+",
            },
        )

        self.assertRedirects(response, reverse("colony:cage_detail", args=[self.cage.pk]))
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertIn(
            "Updated genotyping for 2 mouse(s) in cage CAGE-GT-1: GT-M001, GT-M002.",
            messages,
        )
        self.assertEqual(
            MouseGenotypeComponent.objects.get(mouse=self.mouse_a, locus_name="Lyz2-Cre").zygosity,
            "Cre/+",
        )
        self.assertEqual(
            MouseGenotypeComponent.objects.get(mouse=self.mouse_a, locus_name="Gpnmb flox").zygosity,
            "fl/fl",
        )
        self.assertEqual(
            MouseGenotypeComponent.objects.get(mouse=self.mouse_b, locus_name="Lyz2-Cre").zygosity,
            "+/+",
        )
        self.assertEqual(
            MouseGenotypeComponent.objects.get(mouse=self.mouse_b, locus_name="Gpnmb flox").zygosity,
            "fl/+",
        )
