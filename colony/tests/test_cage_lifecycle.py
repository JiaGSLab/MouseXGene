from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from breeding.models import Breeding
from colony.cage_lifecycle import ensure_breeding_for_cage, sync_cage_status_from_mice
from colony.models import Cage, Mouse, StrainLine
from core.models import Project, ProjectMembership
from users.models import UserProfile


class CageLifecycleTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="lifecycle", password="x")
        UserProfile.objects.filter(user=self.user).update(role=UserProfile.Role.MANAGER)
        self.strain = StrainLine.objects.create(line_name="LS", name="LS")
        self.project = Project.objects.create(name="P1", owner=self.user)
        ProjectMembership.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectMembership.Role.MANAGER,
        )
        self.cage = Cage.objects.create(cage_id="BR-CAGE-1", purpose=Cage.Purpose.BREEDING)
        self.male = Mouse.objects.create(
            mouse_uid="M-SIRE",
            sex=Mouse.Sex.MALE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage,
        )
        self.female = Mouse.objects.create(
            mouse_uid="M-DAM",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage,
        )

    def test_breeding_purpose_creates_active_breeding(self):
        breeding = ensure_breeding_for_cage(self.cage)
        self.assertIsNotNone(breeding)
        self.cage.refresh_from_db()
        self.assertEqual(self.cage.cage_type, Cage.CageType.BREEDING)
        self.assertTrue(
            Breeding.objects.filter(cage=self.cage, active=True, male=self.male, female_1=self.female).exists()
        )

    def test_all_inactive_mice_closes_active_cage(self):
        active_cage = Cage.objects.create(cage_id="CLOSE-ME")
        Mouse.objects.create(
            mouse_uid="M-DEAD",
            sex=Mouse.Sex.FEMALE,
            status=Mouse.Status.EUTHANIZED,
            strain_line=self.strain,
            project=self.project,
            current_cage=active_cage,
        )
        changed = sync_cage_status_from_mice(active_cage)
        active_cage.refresh_from_db()
        self.assertTrue(changed)
        self.assertEqual(active_cage.status, Cage.Status.CLOSED)

    def test_empty_cage_is_not_auto_closed(self):
        empty_cage = Cage.objects.create(cage_id="EMPTY-1")
        changed = sync_cage_status_from_mice(empty_cage)
        empty_cage.refresh_from_db()
        self.assertFalse(changed)
        self.assertEqual(empty_cage.status, Cage.Status.ACTIVE)

    def test_cage_edit_with_breeding_purpose_shows_on_breeding_list(self):
        client = Client()
        client.login(username="lifecycle", password="x")
        holding_cage = Cage.objects.create(cage_id="HOLD-TO-BR")
        Mouse.objects.create(
            mouse_uid="M-SIRE-2",
            sex=Mouse.Sex.MALE,
            strain_line=self.strain,
            project=self.project,
            current_cage=holding_cage,
        )
        Mouse.objects.create(
            mouse_uid="M-DAM-2",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.strain,
            project=self.project,
            current_cage=holding_cage,
        )
        response = client.post(
            reverse("colony:cage_edit", kwargs={"pk": holding_cage.pk}),
            {
                "cage_id": holding_cage.cage_id,
                "created_date": "",
                "cage_use": Cage.CageUse.BREEDING,
                "status": Cage.Status.ACTIVE,
                "room": "",
                "rack": "",
                "position": "",
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        holding_cage.refresh_from_db()
        self.assertEqual(holding_cage.cage_type, Cage.CageType.BREEDING)
        self.assertEqual(holding_cage.purpose, Cage.Purpose.BREEDING)
        self.assertTrue(Breeding.objects.filter(cage=holding_cage, active=True).exists())
        list_response = client.get(reverse("breeding:breeding_list"))
        self.assertContains(list_response, holding_cage.cage_id)

    def test_breeding_purpose_cage_without_sire_shows_as_pending(self):
        client = Client()
        client.login(username="lifecycle", password="x")
        only_dams = Cage.objects.create(cage_id="PEND-CAGE", purpose=Cage.Purpose.BREEDING)
        Mouse.objects.create(
            mouse_uid="M-DAM-A",
            sex=Mouse.Sex.FEMALE,
            strain_line=self.strain,
            project=self.project,
            current_cage=only_dams,
        )
        response = client.get(reverse("breeding:breeding_list"))
        self.assertContains(response, "PEND-CAGE")
        self.assertContains(response, "Pending setup")
        self.assertContains(response, "Need sire")

    def test_breeding_cage_detail_has_warm_visual_treatment(self):
        client = Client()
        client.login(username="lifecycle", password="x")

        response = client.get(reverse("colony:cage_detail", args=[self.cage.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cage-detail--breeding")
        self.assertContains(response, "Breeding cage")
        self.assertContains(response, "cage-purpose-pill--breeding")

    def test_project_manager_can_retire_empty_cage(self):
        client = Client()
        client.login(username="lifecycle", password="x")
        empty_cage = Cage.objects.create(cage_id="RETIRE-EMPTY", project=self.project)
        detail = client.get(reverse("colony:cage_detail", args=[empty_cage.pk]))
        self.assertContains(detail, "Retire Cage")

        response = client.post(
            reverse("colony:cage_retire", args=[empty_cage.pk]),
            {
                "retire_date": timezone.localdate().isoformat(),
                "reason": "Empty cage no longer used",
                "confirm": "on",
            },
        )

        self.assertRedirects(response, reverse("colony:cage_detail", args=[empty_cage.pk]))
        empty_cage.refresh_from_db()
        self.assertEqual(empty_cage.status, Cage.Status.RETIRED)
        self.assertEqual(empty_cage.purpose, Cage.Purpose.RETIRED)

    def test_project_manager_can_restore_retired_cage(self):
        client = Client()
        client.login(username="lifecycle", password="x")
        empty_cage = Cage.objects.create(
            cage_id="RESTORE-EMPTY",
            project=self.project,
            status=Cage.Status.RETIRED,
            purpose=Cage.Purpose.RETIRED,
            cage_type=Cage.CageType.STANDARD,
            archived_at=timezone.now(),
        )

        detail = client.get(reverse("colony:cage_detail", args=[empty_cage.pk]))
        self.assertContains(detail, "Restore Cage")

        response = client.post(
            reverse("colony:cage_restore", args=[empty_cage.pk]),
            {
                "restore_date": timezone.localdate().isoformat(),
                "cage_use": Cage.CageUse.HOLDING,
                "reason": "Project manager reviewed correction",
                "confirm": "on",
            },
        )

        self.assertRedirects(response, reverse("colony:cage_detail", args=[empty_cage.pk]))
        empty_cage.refresh_from_db()
        self.assertEqual(empty_cage.status, Cage.Status.ACTIVE)
        self.assertEqual(empty_cage.purpose, Cage.Purpose.HOLDING)
        self.assertIsNone(empty_cage.archived_at)

    def test_retire_cage_blocks_current_mice(self):
        client = Client()
        client.login(username="lifecycle", password="x")
        response = client.post(
            reverse("colony:cage_retire", args=[self.cage.pk]),
            {
                "retire_date": timezone.localdate().isoformat(),
                "reason": "Trying to close active cage.",
                "confirm": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Move or end all current mice")
        self.cage.refresh_from_db()
        self.assertEqual(self.cage.status, Cage.Status.ACTIVE)


class DashboardAlertTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="dashuser", password="x")
        UserProfile.objects.filter(user=self.user).update(role=UserProfile.Role.MEMBER)
        self.project = Project.objects.create(name="DashP", owner=self.user)
        ProjectMembership.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectMembership.Role.MEMBER,
        )
        self.client = Client()
        self.client.login(username="dashuser", password="x")

    def test_empty_cage_alert_counts_only_long_empty_cages(self):
        cage = Cage.objects.create(cage_id="EMPTY-DASH")
        Cage.objects.filter(pk=cage.pk).update(created_at=timezone.now() - timedelta(days=20))
        response = self.client.get(reverse("home"))
        self.assertContains(response, "Active Cages Empty &gt;14 Days")
        self.assertContains(response, "EMPTY-DASH")
        self.assertNotContains(response, "Cages With No Current Mice")

    def test_dashboard_genotype_alert_skips_plain_wt_mice(self):
        wt_line = StrainLine.objects.create(
            line_name="Dashboard WT",
            name="Dashboard WT",
            category=StrainLine.Category.WILD_TYPE,
        )
        legacy_wt_line = StrainLine.objects.create(
            line_name="WT",
            name="WT",
            category=StrainLine.Category.COMPOUND_STRAIN,
        )
        required_line = StrainLine.objects.create(
            line_name="Dashboard Required",
            name="Dashboard Required",
            category=StrainLine.Category.KNOCKOUT,
            expected_loci_template="GeneX",
        )
        Mouse.objects.create(
            mouse_uid="WT-CLEAR",
            sex=Mouse.Sex.FEMALE,
            strain_line=wt_line,
            project=self.project,
        )
        Mouse.objects.create(
            mouse_uid="LEGACY-WT-CLEAR",
            sex=Mouse.Sex.MALE,
            strain_line=legacy_wt_line,
            project=self.project,
        )
        Mouse.objects.create(
            mouse_uid="MUT-MISSING",
            sex=Mouse.Sex.MALE,
            strain_line=required_line,
            project=self.project,
        )

        response = self.client.get(reverse("home"))
        alerts = {alert["kind"]: alert for alert in response.context["dashboard_alerts"]}

        self.assertContains(response, "Mice Missing Required Genotype")
        self.assertEqual(alerts["mice_no_genotype"]["count"], 1)
        self.assertEqual([mouse.mouse_uid for mouse in alerts["mice_no_genotype"]["items"]], ["MUT-MISSING"])
        self.assertContains(
            response,
            f'href="/mice/?status=active&amp;needs_genotype=yes&amp;owner={self.user.pk}"',
        )
        self.assertNotContains(response, "Mice Without Genotype Records")

        list_response = self.client.get(
            reverse("mice:mouse_list"),
            {"status": Mouse.Status.ACTIVE, "needs_genotype": "yes", "owner": str(self.user.pk)},
        )
        self.assertContains(list_response, "MUT-MISSING")
        self.assertNotContains(list_response, "WT-CLEAR")
        self.assertNotContains(list_response, "LEGACY-WT-CLEAR")

    def test_dashboard_uses_overdue_breeding_not_plain_no_litter_alert(self):
        strain = StrainLine.objects.create(line_name="Dash Breeding Strain", name="Dash Breeding Strain")
        cage = Cage.objects.create(cage_id="DASH-BR-CAGE", purpose=Cage.Purpose.BREEDING)
        male = Mouse.objects.create(
            mouse_uid="DASH-SIRE",
            sex=Mouse.Sex.MALE,
            strain_line=strain,
            project=self.project,
            current_cage=cage,
        )
        female = Mouse.objects.create(
            mouse_uid="DASH-DAM",
            sex=Mouse.Sex.FEMALE,
            strain_line=strain,
            project=self.project,
            current_cage=cage,
        )
        Breeding.objects.create(
            breeding_code="DASH-BR-OLD",
            cage=cage,
            male=male,
            female_1=female,
            start_date=timezone.localdate() - timedelta(days=30),
            active=True,
        )

        response = self.client.get(reverse("home"))

        self.assertContains(response, "Breeding Overdue / Review Pair")
        self.assertContains(response, "DASH-BR-OLD")
        self.assertContains(response, f'href="/breedings/?alert=overdue&amp;owner={self.user.pk}"')
        self.assertNotContains(response, "Active/Plugged Breedings Without Litters")
        self.assertNotContains(response, "Tail-tagged Pups Missing Genotype")

    def test_dashboard_alert_links_use_actionable_filters(self):
        cage = Cage.objects.create(cage_id="EMPTY-LINK")
        Cage.objects.filter(pk=cage.pk).update(created_at=timezone.now() - timedelta(days=20))
        strain = StrainLine.objects.create(line_name="Dash Link Strain", name="Dash Link Strain")
        Mouse.objects.create(
            mouse_uid="MISSING-CAGE-LINK",
            sex=Mouse.Sex.MALE,
            strain_line=strain,
            project=self.project,
        )

        response = self.client.get(reverse("home"))

        self.assertContains(response, f'href="/cages/?status=active&amp;empty_long=yes&amp;owner={self.user.pk}"')
        self.assertContains(response, f'href="/mice/?status=active&amp;missing_cage=yes&amp;owner={self.user.pk}"')
        self.assertContains(response, f'href="/litters/?weaning_due=soon&amp;owner={self.user.pk}"')
        self.assertContains(response, f'href="/breedings/?alert=cage_mismatch&amp;owner={self.user.pk}"')

    def test_recent_lists_show_created_dates(self):
        Cage.objects.create(cage_id="REC-CAGE", created_at=timezone.now() - timedelta(days=1))
        response = self.client.get(reverse("home"))
        self.assertContains(response, "Recently Created Cages")
        self.assertContains(response, "mini-list__date")
        self.assertContains(response, "REC-CAGE")
