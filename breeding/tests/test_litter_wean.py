from datetime import date

from django.contrib.messages import get_messages
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from breeding.models import Breeding, Litter, LitterPup
from breeding.views import (
    _litter_wean_initial_pup_count,
    _litter_wean_initial_sex_counts,
    _litter_wean_prefill_rows,
    _litter_wean_pup_initial_rows,
    _litter_wean_rows_from_sex_counts,
)
from colony.breeding_pedigree import mouse_family_pedigree
from colony.models import Cage, Mouse, MouseGenotypeComponent, StrainLine
from core.models import Project, ProjectMembership
from users.models import UserProfile


class LitterWeanPageTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="weanuser", password="x")
        UserProfile.objects.filter(user=self.user).update(role=UserProfile.Role.MANAGER)
        self.client = Client()
        self.client.login(username="weanuser", password="x")
        self.project = Project.objects.create(name="WeanProject", owner=self.user)
        ProjectMembership.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectMembership.Role.MANAGER,
        )
        self.strain = StrainLine.objects.create(line_name="WeanStrain", name="WeanStrain")
        self.cage = Cage.objects.create(cage_id="WEAN-CAGE-1", purpose=Cage.Purpose.HOLDING)
        self.male_cage = Cage.objects.create(cage_id="WEAN-M-CAGE", purpose=Cage.Purpose.HOLDING)
        self.female_cage = Cage.objects.create(cage_id="WEAN-F-CAGE", purpose=Cage.Purpose.HOLDING)
        self.sire = Mouse.objects.create(
            mouse_uid="M-WEAN-S",
            sex=Mouse.Sex.MALE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage,
        )
        self.dam = Mouse.objects.create(
            mouse_uid="M-WEAN-D",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage,
        )
        self.breeding = Breeding.objects.create(
            breeding_code="BR-WEAN-1",
            cage=self.cage,
            male=self.sire,
            female_1=self.dam,
            start_date=date(2026, 1, 1),
        )
        self.litter = Litter.objects.create(
            breeding=self.breeding,
            litter_code="LT-WEAN-1",
            birth_date=date(2026, 1, 22),
            total_born=5,
            alive_count=3,
        )

    def _wean_post(self, **fields):
        base = {
            "wean_date": "2026-02-12",
            "project_assignment_mode": "sire",
            "strain_assignment_mode": "dam",
        }
        base.update(fields)
        return self.client.post(reverse("litters:litter_wean", args=[self.litter.pk]), base)

    def test_initial_pup_count_matches_total_born(self):
        self.assertEqual(_litter_wean_initial_pup_count(self.litter), 5)

    def test_initial_sex_counts_manual_when_litter_has_no_split(self):
        male, female, source = _litter_wean_initial_sex_counts(self.litter)
        self.assertEqual(male, 0)
        self.assertEqual(female, 0)
        self.assertEqual(source, "manual")

    def test_prefills_pup_rows_from_litter_pups(self):
        LitterPup.objects.create(litter=self.litter, sort_order=1, sex=Mouse.Sex.MALE, ear_tag="E1")
        LitterPup.objects.create(litter=self.litter, sort_order=2, sex=Mouse.Sex.FEMALE, coat_color="black")
        rows = _litter_wean_pup_initial_rows(self.litter)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["sex"], Mouse.Sex.MALE)
        self.assertEqual(rows[0]["ear_tag"], "E1")
        self.assertEqual(rows[1]["sex"], Mouse.Sex.FEMALE)
        self.assertEqual(rows[1]["coat_color"], "black")

    def test_prefill_rows_from_sex_counts_when_no_pup_rows(self):
        self.litter.male_count = 2
        self.litter.female_count = 1
        self.litter.save(update_fields=["male_count", "female_count"])
        rows = _litter_wean_rows_from_sex_counts(self.litter)
        self.assertEqual(len(rows), 3)
        self.assertEqual(sum(1 for r in rows if r["sex"] == Mouse.Sex.MALE), 2)
        self.assertEqual(sum(1 for r in rows if r["sex"] == Mouse.Sex.FEMALE), 1)
        prefill = _litter_wean_prefill_rows(self.litter)
        self.assertEqual(len(prefill), 3)

    def test_sex_count_prefill_renders_on_get(self):
        self.litter.male_count = 1
        self.litter.female_count = 1
        self.litter.save(update_fields=["male_count", "female_count"])
        response = self.client.get(reverse("litters:litter_wean", args=[self.litter.pk]))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertEqual(html.count('class="card pup-card"'), 2)

    def test_wean_page_renders_sex_split_cages(self):
        response = self.client.get(reverse("litters:litter_wean", args=[self.litter.pk]))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("Parentage", html)
        self.assertIn("Use breeding cage parents", html)
        self.assertIn("Select sire and possible dam(s)", html)
        self.assertIn("Use existing strain line", html)
        self.assertIn('id="id_existing_strain_line"', html)
        self.assertIn('id="id_male_cage"', html)
        self.assertIn('id="id_female_cage"', html)
        self.assertIn('id="wean-sex-summary"', html)
        self.assertIn("Weaning Cage Setup", html)
        self.assertIn("Male pups -> cage", html)
        self.assertIn("Female pups -> cage", html)
        self.assertIn("Add male cage", html)
        self.assertIn("Add female cage", html)
        self.assertIn("Weaning cage", html)
        self.assertLess(html.index("Weaning Cage Setup"), html.index("Pup Entries"))
        self.assertIn('id="wean-submit-btn"', html)

    def test_wean_all_male_single_cage(self):
        response = self._wean_post(
            male_pup_count="2",
            female_pup_count="0",
            male_cage_lookup=self.male_cage.cage_id,
            **{
                "pups-0-mouse_uid": "M-WEAN-ALL-M-1",
                "pups-0-sex": "M",
                "pups-0-ear_tag": "",
                "pups-0-coat_color": "",
                "pups-0-notes": "",
                "pups-1-mouse_uid": "M-WEAN-ALL-M-2",
                "pups-1-sex": "M",
                "pups-1-ear_tag": "",
                "pups-1-coat_color": "",
                "pups-1-notes": "",
            },
        )
        self.assertRedirects(response, reverse("litters:litter_detail", args=[self.litter.pk]))
        for uid in ("M-WEAN-ALL-M-1", "M-WEAN-ALL-M-2"):
            pup = Mouse.objects.get(mouse_uid=uid)
            self.assertEqual(pup.current_cage_id, self.male_cage.pk)
            self.assertEqual(pup.dam_id, self.dam.pk)
        self.litter.refresh_from_db()
        self.assertEqual(self.litter.litter_status, Litter.LitterStatus.WEANED)

    def test_wean_all_female_single_cage(self):
        response = self._wean_post(
            male_pup_count="0",
            female_pup_count="2",
            female_cage_lookup=self.female_cage.cage_id,
            **{
                "pups-0-mouse_uid": "M-WEAN-ALL-F-1",
                "pups-0-sex": "F",
                "pups-0-ear_tag": "",
                "pups-0-coat_color": "",
                "pups-0-notes": "",
                "pups-1-mouse_uid": "M-WEAN-ALL-F-2",
                "pups-1-sex": "F",
                "pups-1-ear_tag": "",
                "pups-1-coat_color": "",
                "pups-1-notes": "",
            },
        )
        self.assertRedirects(response, reverse("litters:litter_detail", args=[self.litter.pk]))
        for uid in ("M-WEAN-ALL-F-1", "M-WEAN-ALL-F-2"):
            pup = Mouse.objects.get(mouse_uid=uid)
            self.assertEqual(pup.current_cage_id, self.female_cage.pk)

    def test_wean_follows_sire_strain_line(self):
        sire_strain = StrainLine.objects.create(line_name="SireOnlyStrain", name="SireOnlyStrain")
        dam_strain = StrainLine.objects.create(line_name="DamOnlyStrain", name="DamOnlyStrain")
        self.sire.strain_line = sire_strain
        self.sire.save(update_fields=["strain_line", "updated_at"])
        self.dam.strain_line = dam_strain
        self.dam.save(update_fields=["strain_line", "updated_at"])
        response = self._wean_post(
            male_pup_count="1",
            female_pup_count="0",
            strain_assignment_mode="sire",
            male_cage_lookup=self.male_cage.cage_id,
            **{
                "pups-0-mouse_uid": "M-WEAN-PUP-SIRE-STRAIN",
                "pups-0-sex": "M",
                "pups-0-ear_tag": "",
                "pups-0-coat_color": "",
                "pups-0-notes": "",
            },
        )
        self.assertRedirects(response, reverse("litters:litter_detail", args=[self.litter.pk]))
        pup = Mouse.objects.get(mouse_uid="M-WEAN-PUP-SIRE-STRAIN")
        self.assertEqual(pup.strain_line_id, sire_strain.pk)
        self.assertEqual(pup.current_cage_id, self.male_cage.pk)

    def test_wean_follows_dam_strain_line_by_default(self):
        sire_strain = StrainLine.objects.create(line_name="SireLineDefault", name="SireLineDefault")
        dam_strain = StrainLine.objects.create(line_name="DamLineDefault", name="DamLineDefault")
        self.sire.strain_line = sire_strain
        self.sire.save(update_fields=["strain_line", "updated_at"])
        self.dam.strain_line = dam_strain
        self.dam.save(update_fields=["strain_line", "updated_at"])
        response = self._wean_post(
            male_pup_count="0",
            female_pup_count="1",
            female_cage_lookup=self.female_cage.cage_id,
            **{
                "pups-0-mouse_uid": "M-WEAN-PUP-DAM-DEFAULT",
                "pups-0-sex": "F",
                "pups-0-ear_tag": "",
                "pups-0-coat_color": "",
                "pups-0-notes": "",
            },
        )
        self.assertRedirects(response, reverse("litters:litter_detail", args=[self.litter.pk]))
        pup = Mouse.objects.get(mouse_uid="M-WEAN-PUP-DAM-DEFAULT")
        self.assertEqual(pup.strain_line_id, dam_strain.pk)

    def test_wean_creates_new_strain_line(self):
        sire_strain = StrainLine.objects.create(
            line_name="SireNewLineTemplate",
            name="SireNewLineTemplate",
            expected_loci_template="GeneA",
        )
        dam_strain = StrainLine.objects.create(
            line_name="DamNewLineTemplate",
            name="DamNewLineTemplate",
            expected_loci_template="GeneB",
        )
        self.sire.strain_line = sire_strain
        self.sire.save(update_fields=["strain_line", "updated_at"])
        self.dam.strain_line = dam_strain
        self.dam.save(update_fields=["strain_line", "updated_at"])
        response = self._wean_post(
            male_pup_count="0",
            female_pup_count="1",
            strain_assignment_mode="new",
            new_strain_line_name="OffspringStrain2026",
            female_cage_lookup=self.female_cage.cage_id,
            **{
                "pups-0-mouse_uid": "M-WEAN-PUP-NEW-STRAIN",
                "pups-0-sex": "F",
                "pups-0-ear_tag": "",
                "pups-0-coat_color": "",
                "pups-0-notes": "",
            },
        )
        self.assertRedirects(response, reverse("litters:litter_detail", args=[self.litter.pk]))
        new_line = StrainLine.objects.get(line_name="OffspringStrain2026")
        pup = Mouse.objects.get(mouse_uid="M-WEAN-PUP-NEW-STRAIN")
        self.assertEqual(pup.strain_line_id, new_line.pk)
        self.assertIn(self.project.pk, new_line.projects.values_list("pk", flat=True))
        self.assertEqual(pup.current_cage_id, self.female_cage.pk)
        self.assertEqual(new_line.expected_loci_list(), ["GeneA", "GeneB"])
        self.assertEqual(
            [entry["locus_name"] for entry in new_line.expected_loci_entries()],
            ["GeneA", "GeneB"],
        )

    def test_wean_can_use_existing_non_parent_strain_line(self):
        hybrid_line = StrainLine.objects.create(
            line_name="Cas9-TdT-gRNAs-M2; DARLIN-barcode",
            name="Cas9-TdT-gRNAs-M2; DARLIN-barcode",
            expected_loci_template="Cas9\nDARLIN",
        )
        response = self._wean_post(
            male_pup_count="0",
            female_pup_count="1",
            strain_assignment_mode="existing",
            existing_strain_line=hybrid_line.pk,
            female_cage_lookup=self.female_cage.cage_id,
            **{
                "pups-0-mouse_uid": "M-WEAN-PUP-EXISTING-STRAIN",
                "pups-0-sex": "F",
                "pups-0-ear_tag": "",
                "pups-0-coat_color": "",
                "pups-0-notes": "",
            },
        )
        self.assertRedirects(response, reverse("litters:litter_detail", args=[self.litter.pk]))
        pup = Mouse.objects.get(mouse_uid="M-WEAN-PUP-EXISTING-STRAIN")
        self.assertEqual(pup.strain_line_id, hybrid_line.pk)
        self.assertIn(self.project.pk, hybrid_line.projects.values_list("pk", flat=True))

    def test_wean_rejects_duplicate_new_strain_line_name(self):
        StrainLine.objects.create(line_name="ExistingStrain", name="ExistingStrain")
        response = self._wean_post(
            male_pup_count="0",
            female_pup_count="1",
            strain_assignment_mode="new",
            new_strain_line_name="ExistingStrain",
            female_cage_lookup=self.female_cage.cage_id,
            **{
                "pups-0-mouse_uid": "M-WEAN-DUP-STRAIN",
                "pups-0-sex": "F",
                "pups-0-ear_tag": "",
                "pups-0-coat_color": "",
                "pups-0-notes": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("already exists", response.content.decode())
        self.assertFalse(Mouse.objects.filter(mouse_uid="M-WEAN-DUP-STRAIN").exists())

    def test_wean_splits_mixed_sex_into_two_cages(self):
        response = self._wean_post(
            male_pup_count="1",
            female_pup_count="1",
            male_cage_lookup=self.male_cage.cage_id,
            female_cage_lookup=self.female_cage.cage_id,
            **{
                "pups-0-mouse_uid": "M-WEAN-MIX-M",
                "pups-0-sex": "M",
                "pups-0-ear_tag": "",
                "pups-0-coat_color": "",
                "pups-0-notes": "",
                "pups-1-mouse_uid": "M-WEAN-MIX-F",
                "pups-1-sex": "F",
                "pups-1-ear_tag": "",
                "pups-1-coat_color": "",
                "pups-1-notes": "",
            },
        )
        self.assertRedirects(response, reverse("litters:litter_detail", args=[self.litter.pk]))
        male_pup = Mouse.objects.get(mouse_uid="M-WEAN-MIX-M")
        female_pup = Mouse.objects.get(mouse_uid="M-WEAN-MIX-F")
        self.assertEqual(male_pup.current_cage_id, self.male_cage.pk)
        self.assertEqual(female_pup.current_cage_id, self.female_cage.pk)

    def test_wean_auto_creates_separate_sex_cages(self):
        response = self._wean_post(
            male_pup_count="1",
            female_pup_count="1",
            male_cage_assignment_mode="auto",
            male_auto_cage_id="AUTO-WEAN-M",
            female_cage_assignment_mode="auto",
            female_auto_cage_id="AUTO-WEAN-F",
            **{
                "pups-0-mouse_uid": "M-WEAN-AUTO-M",
                "pups-0-sex": "M",
                "pups-0-ear_tag": "",
                "pups-0-coat_color": "",
                "pups-0-notes": "",
                "pups-1-mouse_uid": "M-WEAN-AUTO-F",
                "pups-1-sex": "F",
                "pups-1-ear_tag": "",
                "pups-1-coat_color": "",
                "pups-1-notes": "",
            },
        )

        self.assertRedirects(response, reverse("litters:litter_detail", args=[self.litter.pk]))
        male_pup = Mouse.objects.get(mouse_uid="M-WEAN-AUTO-M")
        female_pup = Mouse.objects.get(mouse_uid="M-WEAN-AUTO-F")
        male_cage = Cage.objects.get(cage_id="AUTO-WEAN-M")
        female_cage = Cage.objects.get(cage_id="AUTO-WEAN-F")
        self.assertEqual(male_pup.current_cage_id, male_cage.pk)
        self.assertEqual(female_pup.current_cage_id, female_cage.pk)
        self.assertNotEqual(male_cage.pk, female_cage.pk)
        self.assertEqual(male_cage.cage_type, Cage.CageType.WEANING)
        self.assertEqual(female_cage.cage_type, Cage.CageType.WEANING)
        self.assertEqual(male_cage.project_id, self.project.pk)
        self.assertEqual(female_cage.project_id, self.project.pk)
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertIn(
            "Weaned 2 pups into cage(s) AUTO-WEAN-F, AUTO-WEAN-M: M-WEAN-AUTO-M, M-WEAN-AUTO-F.",
            messages,
        )
        self.assertIn("Created weaning cage(s): AUTO-WEAN-M, AUTO-WEAN-F.", messages)

    def test_wean_can_split_each_sex_across_multiple_auto_cages(self):
        response = self._wean_post(
            male_pup_count="2",
            female_pup_count="2",
            male_cage_assignment_mode="auto",
            male_auto_cage_id="AUTO-WEAN-M-DEFAULT",
            male_extra_cage_count="1",
            male_extra_cage_id_1="AUTO-WEAN-M-EXTRA",
            female_cage_assignment_mode="auto",
            female_auto_cage_id="AUTO-WEAN-F-DEFAULT",
            female_extra_cage_count="1",
            female_extra_cage_id_1="AUTO-WEAN-F-EXTRA",
            **{
                "pups-0-mouse_uid": "M-WEAN-MULTI-M-1",
                "pups-0-sex": "M",
                "pups-0-cage_slot": "male-default",
                "pups-0-ear_tag": "",
                "pups-0-coat_color": "",
                "pups-0-notes": "",
                "pups-1-mouse_uid": "M-WEAN-MULTI-M-2",
                "pups-1-sex": "M",
                "pups-1-cage_slot": "male-extra-1",
                "pups-1-ear_tag": "",
                "pups-1-coat_color": "",
                "pups-1-notes": "",
                "pups-2-mouse_uid": "M-WEAN-MULTI-F-1",
                "pups-2-sex": "F",
                "pups-2-cage_slot": "female-default",
                "pups-2-ear_tag": "",
                "pups-2-coat_color": "",
                "pups-2-notes": "",
                "pups-3-mouse_uid": "M-WEAN-MULTI-F-2",
                "pups-3-sex": "F",
                "pups-3-cage_slot": "female-extra-1",
                "pups-3-ear_tag": "",
                "pups-3-coat_color": "",
                "pups-3-notes": "",
            },
        )

        self.assertRedirects(response, reverse("litters:litter_detail", args=[self.litter.pk]))
        expected = {
            "M-WEAN-MULTI-M-1": "AUTO-WEAN-M-DEFAULT",
            "M-WEAN-MULTI-M-2": "AUTO-WEAN-M-EXTRA",
            "M-WEAN-MULTI-F-1": "AUTO-WEAN-F-DEFAULT",
            "M-WEAN-MULTI-F-2": "AUTO-WEAN-F-EXTRA",
        }
        for uid, cage_id in expected.items():
            pup = Mouse.objects.get(mouse_uid=uid)
            cage = Cage.objects.get(cage_id=cage_id)
            self.assertEqual(pup.current_cage_id, cage.pk)
            self.assertEqual(cage.cage_type, Cage.CageType.WEANING)
            self.assertEqual(cage.project_id, self.project.pk)

    def test_wean_extra_cage_can_use_existing_active_cage(self):
        male_extra_cage = Cage.objects.create(cage_id="WEAN-M-EXTRA-EXISTING", purpose=Cage.Purpose.HOLDING)
        response = self._wean_post(
            male_pup_count="2",
            female_pup_count="0",
            male_cage_lookup=self.male_cage.cage_id,
            male_extra_cage_count="1",
            male_extra_cage_mode_1="existing",
            male_extra_cage_lookup_1=male_extra_cage.cage_id,
            **{
                "pups-0-mouse_uid": "M-WEAN-EXISTING-DEFAULT",
                "pups-0-sex": "M",
                "pups-0-cage_slot": "male-default",
                "pups-0-ear_tag": "",
                "pups-0-coat_color": "",
                "pups-0-notes": "",
                "pups-1-mouse_uid": "M-WEAN-EXISTING-EXTRA",
                "pups-1-sex": "M",
                "pups-1-cage_slot": "male-extra-1",
                "pups-1-ear_tag": "",
                "pups-1-coat_color": "",
                "pups-1-notes": "",
            },
        )

        self.assertRedirects(response, reverse("litters:litter_detail", args=[self.litter.pk]))
        default_pup = Mouse.objects.get(mouse_uid="M-WEAN-EXISTING-DEFAULT")
        extra_pup = Mouse.objects.get(mouse_uid="M-WEAN-EXISTING-EXTRA")
        self.assertEqual(default_pup.current_cage_id, self.male_cage.pk)
        self.assertEqual(extra_pup.current_cage_id, male_extra_cage.pk)

    def test_wean_trio_uses_breeding_cage_possible_dams_by_default(self):
        dam2 = Mouse.objects.create(
            mouse_uid="M-WEAN-D2",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage,
        )
        self.breeding.female_2 = dam2
        self.breeding.save(update_fields=["female_2"])

        response = self._wean_post(
            male_pup_count="1",
            female_pup_count="0",
            male_cage_lookup=self.male_cage.cage_id,
            **{
                "pups-0-mouse_uid": "M-WEAN-TRIO-PUP",
                "pups-0-sex": "M",
                "pups-0-ear_tag": "",
                "pups-0-coat_color": "",
                "pups-0-notes": "",
            },
        )

        self.assertRedirects(response, reverse("litters:litter_detail", args=[self.litter.pk]))
        pup = Mouse.objects.get(mouse_uid="M-WEAN-TRIO-PUP")
        self.assertEqual(pup.sire_id, self.sire.pk)
        self.assertIsNone(pup.dam_id)
        self.assertEqual(pup.source_breeding_id, self.breeding.pk)
        self.assertEqual(set(pup.possible_dams.values_list("mouse_uid", flat=True)), {"M-WEAN-D", "M-WEAN-D2"})
        pedigree = mouse_family_pedigree(pup)
        self.assertEqual({dam.mouse_uid for dam in pedigree.dams}, {"M-WEAN-D", "M-WEAN-D2"})

    def test_wean_manual_single_dam_sets_known_dam(self):
        dam2 = Mouse.objects.create(
            mouse_uid="M-WEAN-D3",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage,
        )
        self.breeding.female_2 = dam2
        self.breeding.save(update_fields=["female_2"])

        response = self._wean_post(
            parentage_mode="select_parents",
            parent_breeding=str(self.breeding.pk),
            wean_sire=str(self.sire.pk),
            wean_possible_dams=[str(dam2.pk)],
            male_pup_count="0",
            female_pup_count="1",
            female_cage_lookup=self.female_cage.cage_id,
            **{
                "pups-0-mouse_uid": "M-WEAN-KNOWN-DAM",
                "pups-0-sex": "F",
                "pups-0-ear_tag": "",
                "pups-0-coat_color": "",
                "pups-0-notes": "",
            },
        )

        self.assertRedirects(response, reverse("litters:litter_detail", args=[self.litter.pk]))
        pup = Mouse.objects.get(mouse_uid="M-WEAN-KNOWN-DAM")
        self.assertEqual(pup.sire_id, self.sire.pk)
        self.assertEqual(pup.dam_id, dam2.pk)
        self.assertEqual(pup.source_breeding_id, self.breeding.pk)
        self.assertFalse(pup.possible_dams.exists())

    def test_wean_rejects_same_cage_for_mixed_sex(self):
        response = self._wean_post(
            male_pup_count="1",
            female_pup_count="1",
            male_cage_lookup=self.male_cage.cage_id,
            female_cage_lookup=self.male_cage.cage_id,
            **{
                "pups-0-mouse_uid": "M-WEAN-SAME-1",
                "pups-0-sex": "M",
                "pups-0-ear_tag": "",
                "pups-0-coat_color": "",
                "pups-0-notes": "",
                "pups-1-mouse_uid": "M-WEAN-SAME-2",
                "pups-1-sex": "F",
                "pups-1-ear_tag": "",
                "pups-1-coat_color": "",
                "pups-1-notes": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("different cages", response.content.decode())

    def test_wean_blocks_invalid_sex_and_creates_no_mice(self):
        response = self._wean_post(
            male_pup_count="1",
            female_pup_count="0",
            male_cage_lookup=self.male_cage.cage_id,
            **{
                "pups-0-mouse_uid": "M-WEAN-BAD-SEX",
                "pups-0-sex": "U",
                "pups-0-ear_tag": "",
                "pups-0-coat_color": "",
                "pups-0-notes": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Mouse.objects.filter(mouse_uid="M-WEAN-BAD-SEX").exists())

    def test_missing_pup_uid_is_shown_in_top_error_summary(self):
        response = self._wean_post(
            male_pup_count="1",
            female_pup_count="0",
            male_cage_lookup=self.male_cage.cage_id,
            **{
                "pups-0-mouse_uid": "",
                "pups-0-sex": "M",
                "pups-0-ear_tag": "",
                "pups-0-coat_color": "",
                "pups-0-notes": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please fix the highlighted fields")
        self.assertContains(response, "Nothing was saved yet.")
        self.assertContains(response, "Pup 1 - Mouse UID: Enter a Mouse UID for this pup.")
        self.assertContains(response, 'href="#id_pups-0-mouse_uid"')
        self.assertFalse(Mouse.objects.filter(birth_date=self.litter.birth_date, mouse_uid="").exists())

    def test_genotype_loci_are_sire_dam_union_not_pup_strain_only(self):
        sire_strain = StrainLine.objects.create(
            line_name="SireLociStrain",
            name="SireLociStrain",
            expected_loci_template="LocusA\nLocusB",
        )
        dam_strain = StrainLine.objects.create(
            line_name="DamLociStrain",
            name="DamLociStrain",
            expected_loci_template="LocusB\nLocusC",
        )
        self.sire.strain_line = sire_strain
        self.sire.save(update_fields=["strain_line", "updated_at"])
        self.dam.strain_line = dam_strain
        self.dam.save(update_fields=["strain_line", "updated_at"])
        response = self._wean_post(
            male_pup_count="0",
            female_pup_count="1",
            strain_assignment_mode="dam",
            female_cage_lookup=self.female_cage.cage_id,
            **{
                "pups-0-mouse_uid": "M-WEAN-GT-UNION",
                "pups-0-sex": "F",
                "pups-0-ear_tag": "",
                "pups-0-coat_color": "",
                "pups-0-notes": "",
            },
        )
        self.assertRedirects(response, reverse("litters:litter_detail", args=[self.litter.pk]))
        pup = Mouse.objects.get(mouse_uid="M-WEAN-GT-UNION")
        self.assertEqual(pup.strain_line_id, dam_strain.pk)
        loci = set(pup.genotype_components.values_list("locus_name", flat=True))
        self.assertEqual(loci, {"LocusA", "LocusB", "LocusC"})
        for comp in pup.genotype_components.all():
            self.assertEqual(comp.zygosity_class, MouseGenotypeComponent.ZygosityClass.UNKNOWN)

    def test_refresh_forms_updates_pup_count(self):
        response = self._wean_post(
            male_pup_count="2",
            female_pup_count="0",
            refresh_forms="1",
            **{
                "pups-0-mouse_uid": "keep-me",
                "pups-0-sex": "M",
            },
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertEqual(html.count('class="card pup-card"'), 2)
        self.assertIn('value="keep-me"', html)

    def test_prefilled_litter_pups_render_on_get(self):
        LitterPup.objects.create(litter=self.litter, sort_order=1, sex=Mouse.Sex.MALE, ear_tag="TAG-1")
        LitterPup.objects.create(litter=self.litter, sort_order=2, sex=Mouse.Sex.FEMALE)
        response = self.client.get(reverse("litters:litter_wean", args=[self.litter.pk]))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertEqual(html.count('class="card pup-card"'), 2)
        self.assertIn('value="TAG-1"', html)
