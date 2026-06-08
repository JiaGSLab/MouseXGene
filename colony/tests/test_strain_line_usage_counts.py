from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from breeding.models import Breeding
from colony.models import Cage, Mouse, StrainLine
from colony.strain_line_usage import compute_strain_line_usage_counts
from colony.views import _strain_line_usage_annotations
from core.models import Project


class StrainLineUsageCountTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="straincounts", password="x")
        self.project = Project.objects.create(name="CountProject", owner=self.user)
        self.strain = StrainLine.objects.create(line_name="CountStrain", name="CountStrain")
        self.cage = Cage.objects.create(cage_id="CNT-CAGE-1", purpose=Cage.Purpose.HOLDING)
        self.breeding_cage = Cage.objects.create(cage_id="BR-CAGE-1", purpose=Cage.Purpose.BREEDING)
        self.other_cage = Cage.objects.create(cage_id="CNT-CAGE-2", purpose=Cage.Purpose.HOLDING)
        self.sire = Mouse.objects.create(
            mouse_uid="M-CNT-S",
            sex=Mouse.Sex.MALE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage,
        )
        self.dam = Mouse.objects.create(
            mouse_uid="M-CNT-D",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage,
        )

    def _annotated_line(self):
        return StrainLine.objects.annotate(**_strain_line_usage_annotations()).get(pk=self.strain.pk)

    def _live_counts(self):
        return compute_strain_line_usage_counts(self.strain.pk)

    def test_same_strain_pair_counts_one_active_breeding(self):
        Breeding.objects.create(
            breeding_code="BR-CNT-1",
            cage=self.cage,
            male=self.sire,
            female_1=self.dam,
            start_date="2026-01-01",
            active=True,
        )
        live = self._live_counts()
        self.assertEqual(live["active_breedings_count"], 1)

    def test_breeding_cage_counts_even_when_breeders_still_listed_elsewhere(self):
        Breeding.objects.create(
            breeding_code="BR-CNT-CAGE",
            cage=self.breeding_cage,
            male=self.sire,
            female_1=self.dam,
            start_date="2026-01-01",
            active=True,
        )
        live = self._live_counts()
        self.assertIn(self.breeding_cage.pk, {self.cage.pk, self.breeding_cage.pk})
        self.assertGreaterEqual(live["active_cages_count"], 2)

    def test_detail_page_related_records_reflect_new_breeding(self):
        user = get_user_model().objects.create_user(username="strainview", password="x")
        self.client.login(username="strainview", password="x")
        Breeding.objects.create(
            breeding_code="BR-CNT-2",
            cage=self.cage,
            male=self.sire,
            female_1=self.dam,
            start_date="2026-01-01",
            active=True,
        )
        response = self.client.get(reverse("colony:strain_line_detail", args=[self.strain.pk]))
        self.assertEqual(response.status_code, 200)
        live = self._live_counts()
        self.assertEqual(live["active_breedings_count"], 1)
        html = response.content.decode()
        self.assertRegex(html, r">Breedings</dt>\s*<dd[^>]*>[\s\S]*?1 active")

    def test_breeding_list_strain_line_filter(self):
        Breeding.objects.create(
            breeding_code="BR-FILTER-ME",
            cage=self.breeding_cage,
            male=self.sire,
            female_1=self.dam,
            start_date="2026-01-01",
            active=True,
        )
        other_strain = StrainLine.objects.create(line_name="Other", name="Other")
        other_sire = Mouse.objects.create(
            mouse_uid="M-OTHER-S",
            sex=Mouse.Sex.MALE,
            strain_line=other_strain,
            project=self.project,
        )
        other_dam = Mouse.objects.create(
            mouse_uid="M-OTHER-D",
            sex=Mouse.Sex.FEMALE,
            strain_line=other_strain,
            project=self.project,
        )
        Breeding.objects.create(
            breeding_code="BR-FILTER-NOT",
            cage=self.other_cage,
            male=other_sire,
            female_1=other_dam,
            start_date="2026-01-01",
            active=True,
        )
        user = get_user_model().objects.create_user(username="breedfilter", password="x")
        self.client.login(username="breedfilter", password="x")
        response = self.client.get(reverse("breeding:breeding_list"), {"strain_line_id": self.strain.pk})
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("BR-FILTER-ME", html)
        self.assertNotIn("BR-FILTER-NOT", html)

    def test_breeding_list_keeps_strain_line_filter_after_apply(self):
        Breeding.objects.create(
            breeding_code="BR-KEEP-FILTER",
            cage=self.breeding_cage,
            male=self.sire,
            female_1=self.dam,
            start_date="2026-01-01",
            active=True,
        )
        other_owner = get_user_model().objects.create_user(username="otherowner2", password="x")
        other_project = Project.objects.create(name="OtherOwnerProject", owner=other_owner)
        other_strain = StrainLine.objects.create(line_name="OtherKeep", name="OtherKeep")
        other_sire = Mouse.objects.create(
            mouse_uid="M-KEEP-OTHER",
            sex=Mouse.Sex.MALE,
            strain_line=other_strain,
            project=other_project,
        )
        other_dam = Mouse.objects.create(
            mouse_uid="M-KEEP-OTHER-D",
            sex=Mouse.Sex.FEMALE,
            strain_line=other_strain,
            project=other_project,
        )
        Breeding.objects.create(
            breeding_code="BR-KEEP-OTHER",
            cage=self.other_cage,
            male=other_sire,
            female_1=other_dam,
            start_date="2026-01-01",
            active=True,
        )
        viewer = get_user_model().objects.create_user(username="breedkeep", password="x")
        self.client.login(username="breedkeep", password="x")
        response = self.client.get(
            reverse("breeding:breeding_list"),
            {
                "strain_line_id": self.strain.pk,
                "owner": str(viewer.pk),
            },
        )
        self.assertContains(response, "BR-KEEP-FILTER")
        self.assertNotContains(response, "BR-KEEP-OTHER")
        response = self.client.get(
            reverse("breeding:breeding_list"),
            {
                "strain_line_id": self.strain.pk,
                "q": "BR-KEEP-FILTER",
            },
        )
        self.assertContains(response, "BR-KEEP-FILTER")
        self.assertNotContains(response, "BR-KEEP-OTHER")

    def test_cage_list_strain_line_includes_breeding_cage(self):
        Breeding.objects.create(
            breeding_code="BR-CAGE-LINK",
            cage=self.breeding_cage,
            male=self.sire,
            female_1=self.dam,
            start_date="2026-01-01",
            active=True,
        )
        user = get_user_model().objects.create_user(username="cagefilter", password="x")
        self.client.login(username="cagefilter", password="x")
        response = self.client.get(reverse("colony:cage_list"), {"strain_line": self.strain.pk})
        self.assertEqual(response.status_code, 200)
        self.assertIn("BR-CAGE-1", response.content.decode())

    def test_euthanized_mouse_drops_active_counts(self):
        self.dam.status = Mouse.Status.EUTHANIZED
        self.dam.save(update_fields=["status", "updated_at"])
        live = self._live_counts()
        self.assertEqual(live["active_mice_count"], 1)
        self.assertEqual(live["total_mice_count"], 2)

    def test_closed_cage_drops_active_cage_count(self):
        self.cage.status = Cage.Status.CLOSED
        self.cage.save(update_fields=["status", "updated_at"])
        live = self._live_counts()
        self.assertEqual(live["active_cages_count"], 0)
        self.assertGreaterEqual(live["total_cages_count"], 1)

    def test_ended_breeding_drops_active_breeding_count(self):
        breeding = Breeding.objects.create(
            breeding_code="BR-CNT-END",
            cage=self.cage,
            male=self.sire,
            female_1=self.dam,
            start_date="2026-01-01",
            active=True,
        )
        breeding.active = False
        breeding.status = Breeding.Status.CLOSED
        breeding.save(update_fields=["active", "status", "updated_at"])
        live = self._live_counts()
        self.assertEqual(live["active_breedings_count"], 0)
        self.assertEqual(live["total_breedings_count"], 1)
