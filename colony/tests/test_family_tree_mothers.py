from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from breeding.models import Breeding, Litter, LitterPup
from colony.breeding_pedigree import mouse_family_pedigree
from colony.models import Cage, Mouse, StrainLine
from core.models import Project


class FamilyTreeMothersTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="family_tree_user", password="x")
        self.project = Project.objects.create(name="FT-P1", owner=self.user, is_active=True)
        self.strain = StrainLine.objects.create(line_name="FT-SL1", is_active=True)
        self.cage = Cage.objects.create(cage_id="FT-BR-CAGE", status=Cage.Status.ACTIVE)
        self.sire = Mouse.objects.create(
            mouse_uid="FT-SIRE",
            sex=Mouse.Sex.MALE,
            status=Mouse.Status.ACTIVE,
            strain_line=self.strain,
            project=self.project,
        )
        self.dam1 = Mouse.objects.create(
            mouse_uid="FT-DAM-1",
            sex=Mouse.Sex.FEMALE,
            status=Mouse.Status.ACTIVE,
            strain_line=self.strain,
            project=self.project,
        )
        self.dam2 = Mouse.objects.create(
            mouse_uid="FT-DAM-2",
            sex=Mouse.Sex.FEMALE,
            status=Mouse.Status.ACTIVE,
            strain_line=self.strain,
            project=self.project,
        )
        self.breeding = Breeding.objects.create(
            breeding_code="FT-BR-1",
            cage=self.cage,
            male=self.sire,
            female_1=self.dam1,
            female_2=self.dam2,
            start_date="2026-01-01",
            active=False,
        )
        self.litter = Litter.objects.create(breeding=self.breeding, birth_date="2026-02-01")
        self.pup = Mouse.objects.create(
            mouse_uid="FT-PUP-1",
            sex=Mouse.Sex.MALE,
            status=Mouse.Status.ACTIVE,
            strain_line=self.strain,
            project=self.project,
            sire=self.sire,
            dam=self.dam1,
            birth_date="2026-02-01",
        )
        LitterPup.objects.create(litter=self.litter, sort_order=1, sex=Mouse.Sex.MALE, mouse=self.pup)

    def test_pedigree_lists_both_dams_from_litter_breeding(self):
        pedigree = mouse_family_pedigree(self.pup)
        dam_uids = {d.mouse_uid for d in pedigree.dams}
        self.assertEqual(dam_uids, {"FT-DAM-1", "FT-DAM-2"})

    def test_family_tree_renders_linked_mothers(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("mice:family_tree"), {"q": "FT-PUP-1"})
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('href="/mice/', html)
        self.assertIn("FT-DAM-1", html)
        self.assertIn("FT-DAM-2", html)
