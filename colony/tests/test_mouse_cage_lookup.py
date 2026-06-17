from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from breeding.models import Breeding
from colony.forms import MouseForm
from colony.models import Cage, Mouse, StrainLine
from core.models import Project, ProjectMembership


class MouseCageLookupTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="mouse_cage_user", password="x")
        self.project = Project.objects.create(name="P1", owner=self.user, is_active=True)
        ProjectMembership.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectMembership.Role.MANAGER,
        )
        self.strain = StrainLine.objects.create(line_name="SL1", is_active=True)
        self.cage = Cage.objects.create(cage_id="MC-CAGE-1", status=Cage.Status.ACTIVE)
        self.other_cage = Cage.objects.create(cage_id="MC-CAGE-2", status=Cage.Status.ACTIVE)

    def test_resolve_lookup_sets_current_cage(self):
        form = MouseForm(
            data={
                "mouse_uid": "M-LOOKUP-1",
                "sex": Mouse.Sex.MALE,
                "status": Mouse.Status.ACTIVE,
                "strain_line": self.strain.pk,
                "project": self.project.pk,
                "current_cage_lookup": "CAGE-1",
            },
            user=self.user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["current_cage"], self.cage)

    def test_picker_selected_cage_and_parents_validate_without_full_querysets(self):
        sire = Mouse.objects.create(
            mouse_uid="PARENT-SIRE",
            sex=Mouse.Sex.MALE,
            status=Mouse.Status.ACTIVE,
            project=self.project,
            strain_line=self.strain,
        )
        dam = Mouse.objects.create(
            mouse_uid="PARENT-DAM",
            sex=Mouse.Sex.FEMALE,
            status=Mouse.Status.ACTIVE,
            project=self.project,
            strain_line=self.strain,
        )
        form = MouseForm(
            data={
                "mouse_uid": "M-PICKER-POST",
                "sex": Mouse.Sex.MALE,
                "status": Mouse.Status.ACTIVE,
                "strain_line": self.strain.pk,
                "project": self.project.pk,
                "current_cage": self.cage.pk,
                "sire": sire.pk,
                "dam": dam.pk,
            },
            user=self.user,
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["current_cage"], self.cage)
        self.assertEqual(form.cleaned_data["sire"], sire)
        self.assertEqual(form.cleaned_data["dam"], dam)

    def test_create_form_renders_cage_filters(self):
        Mouse.objects.create(
            mouse_uid="PARENT-NOT-EMBEDDED",
            sex=Mouse.Sex.MALE,
            status=Mouse.Status.ACTIVE,
            project=self.project,
            strain_line=self.strain,
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("mice:mouse_create"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('id="id_current_cage_lookup"', html)
        self.assertIn('id="id_mouse_cage_owner_filter"', html)
        self.assertIn('id="id_mouse_cage_strain_filter"', html)
        self.assertIn('id="mouse-parent-picker"', html)
        self.assertNotIn("PARENT-NOT-EMBEDDED", html)
        self.assertNotIn("MC-CAGE-1 (", html)
        self.assertIn("Create cage", html)

    def test_move_cage_page_uses_lazy_picker(self):
        mouse = Mouse.objects.create(
            mouse_uid="M-MOVE-PICKER",
            sex=Mouse.Sex.MALE,
            status=Mouse.Status.ACTIVE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("mice:mouse_move", args=[mouse.pk]))

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('id="id_move_cage_project_filter"', html)
        self.assertIn('id="id_move_cage_lookup"', html)
        self.assertNotIn("MC-CAGE-2 (", html)

    def test_mouse_detail_shows_move_cage_for_editable_active_mouse(self):
        mouse = Mouse.objects.create(
            mouse_uid="M-MOVE-DETAIL",
            sex=Mouse.Sex.MALE,
            status=Mouse.Status.ACTIVE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("mice:mouse_detail", args=[mouse.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{reverse("mice:mouse_move", args=[mouse.pk])}"')
        self.assertContains(response, "Move Cage")

    def test_edit_mouse_keeps_current_cage_read_only(self):
        mouse = Mouse.objects.create(
            mouse_uid="M-EDIT-CAGE-LOCKED",
            sex=Mouse.Sex.MALE,
            status=Mouse.Status.ACTIVE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("mice:mouse_edit", args=[mouse.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Use the dedicated Move Cage workflow")
        self.assertContains(response, f'href="{reverse("mice:mouse_move", args=[mouse.pk])}"')
        self.assertNotContains(response, 'id="id_current_cage_lookup"')
        self.assertNotContains(response, 'name="current_cage"')

    def test_edit_mouse_cannot_spoof_current_cage_change(self):
        mouse = Mouse.objects.create(
            mouse_uid="M-EDIT-CAGE-SPOOF",
            sex=Mouse.Sex.MALE,
            status=Mouse.Status.ACTIVE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage,
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("mice:mouse_edit", args=[mouse.pk]),
            {
                "mouse_uid": mouse.mouse_uid,
                "sex": mouse.sex,
                "birth_date": "",
                "death_date": "",
                "euthanasia_date": "",
                "death_reason": "",
                "status": Mouse.Status.ACTIVE,
                "strain_line": self.strain.pk,
                "current_cage": self.other_cage.pk,
                "current_cage_lookup": self.other_cage.cage_id,
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

        self.assertRedirects(response, reverse("mice:mouse_detail", args=[mouse.pk]))
        mouse.refresh_from_db()
        self.assertEqual(mouse.current_cage_id, self.cage.pk)

    def test_inactive_mouse_cannot_open_move_cage_page(self):
        mouse = Mouse.objects.create(
            mouse_uid="M-MOVE-INACTIVE",
            sex=Mouse.Sex.MALE,
            status=Mouse.Status.EUTHANIZED,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("mice:mouse_move", args=[mouse.pk]))

        self.assertRedirects(response, reverse("mice:mouse_detail", args=[mouse.pk]))

    def test_move_cage_blocks_active_breeder_outside_breeding_cage(self):
        sire = Mouse.objects.create(
            mouse_uid="M-MOVE-ACTIVE-SIRE",
            sex=Mouse.Sex.MALE,
            status=Mouse.Status.ACTIVE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage,
        )
        dam = Mouse.objects.create(
            mouse_uid="M-MOVE-ACTIVE-DAM",
            sex=Mouse.Sex.FEMALE,
            status=Mouse.Status.ACTIVE,
            strain_line=self.strain,
            project=self.project,
            current_cage=self.cage,
        )
        breeding = Breeding.objects.create(
            breeding_code="MC-MOVE-BR",
            cage=self.cage,
            male=sire,
            female_1=dam,
            start_date="2026-01-01",
            active=True,
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("mice:mouse_move", args=[sire.pk]),
            {
                "destination_cage": self.other_cage.pk,
                "move_date": "2026-01-02",
                "reason": "test",
                "notes": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, breeding.breeding_code)
        self.assertContains(response, "Move it only to the breeding cage")
        sire.refresh_from_db()
        self.assertEqual(sire.current_cage_id, self.cage.pk)
