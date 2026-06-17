from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from colony.forms import MouseForm
from colony.models import Mouse, StrainLine
from core.models import Project, ProjectMembership
from users.models import UserProfile


class MouseStrainLineLockTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            username="strainadmin",
            email="strainadmin@example.test",
            password="x",
        )
        self.member = get_user_model().objects.create_user(username="strainmember", password="x")
        UserProfile.objects.filter(user=self.admin).update(role=UserProfile.Role.ADMIN)
        UserProfile.objects.filter(user=self.member).update(role=UserProfile.Role.MEMBER)
        self.project = Project.objects.create(name="StrainLockProject", owner=self.member)
        ProjectMembership.objects.create(
            project=self.project,
            user=self.member,
            role=ProjectMembership.Role.MEMBER,
        )
        self.strain_a = StrainLine.objects.create(line_name="Strain-A", name="Strain-A")
        self.strain_b = StrainLine.objects.create(line_name="Strain-B", name="Strain-B")
        self.mouse = Mouse.objects.create(
            mouse_uid="M-STRAIN-LOCK",
            strain_line=self.strain_a,
            project=self.project,
        )

    def test_non_admin_form_keeps_existing_strain_line_locked(self):
        form = MouseForm(
            data={
                "mouse_uid": self.mouse.mouse_uid,
                "sex": self.mouse.sex,
                "status": self.mouse.status,
                "strain_line": self.strain_b.pk,
                "project": self.project.pk,
            },
            instance=self.mouse,
            user=self.member,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["strain_line"].pk, self.strain_a.pk)
        self.assertIn("strain_line", form.admin_correction_frozen_fields)

    def test_edit_page_locks_strain_line_for_non_admin(self):
        client = Client()
        client.login(username="strainmember", password="x")
        response = client.get(reverse("mice:mouse_edit", args=[self.mouse.pk]))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('id="id_strain_line"', html)
        self.assertIn('name="strain_line"', html)
        self.assertIn("disabled", html)

    def test_edit_page_includes_status_initial_attribute(self):
        client = Client()
        client.login(username="strainmember", password="x")
        response = client.get(reverse("mice:mouse_edit", args=[self.mouse.pk]))
        self.assertContains(response, 'data-initial-status="active"')

    def test_non_admin_post_cannot_spoof_strain_line_change(self):
        client = Client()
        client.login(username="strainmember", password="x")
        response = client.post(
            reverse("mice:mouse_edit", args=[self.mouse.pk]),
            {
                "mouse_uid": self.mouse.mouse_uid,
                "sex": self.mouse.sex,
                "birth_date": "",
                "death_date": "",
                "euthanasia_date": "",
                "death_reason": "",
                "status": self.mouse.status,
                "strain_line": self.strain_b.pk,
                "project": self.project.pk,
                "ear_tag": "E1",
                "toe_tag": "",
                "origin": "",
                "coat_color": "",
                "notes": "allowed note",
            },
        )
        self.assertRedirects(response, reverse("mice:mouse_detail", args=[self.mouse.pk]))
        self.mouse.refresh_from_db()
        self.assertEqual(self.mouse.strain_line_id, self.strain_a.pk)
        self.assertEqual(self.mouse.ear_tag, "E1")
        self.assertEqual(self.mouse.notes, "allowed note")

    def test_admin_locked_change_requires_reason(self):
        client = Client()
        client.login(username="strainadmin", password="x")
        response = client.post(
            reverse("mice:mouse_edit", args=[self.mouse.pk]),
            {
                "admin_correction_unlocked": "1",
                "mouse_uid": self.mouse.mouse_uid,
                "sex": self.mouse.sex,
                "birth_date": "",
                "death_date": "",
                "euthanasia_date": "",
                "death_reason": "",
                "status": self.mouse.status,
                "strain_line": self.strain_b.pk,
                "project": self.project.pk,
                "ear_tag": "",
                "toe_tag": "",
                "origin": "",
                "coat_color": "",
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Correction reason is required")
        self.mouse.refresh_from_db()
        self.assertEqual(self.mouse.strain_line_id, self.strain_a.pk)

    def test_admin_unlock_with_reason_can_change_strain_line(self):
        client = Client()
        client.login(username="strainadmin", password="x")
        response = client.post(
            reverse("mice:mouse_edit", args=[self.mouse.pk]),
            {
                "admin_correction_unlocked": "1",
                "admin_correction_reason": "wrong strain selected during setup",
                "mouse_uid": self.mouse.mouse_uid,
                "sex": self.mouse.sex,
                "birth_date": "",
                "death_date": "",
                "euthanasia_date": "",
                "death_reason": "",
                "status": self.mouse.status,
                "strain_line": self.strain_b.pk,
                "project": self.project.pk,
                "ear_tag": "",
                "toe_tag": "",
                "origin": "",
                "coat_color": "",
                "notes": "",
            },
        )
        self.assertRedirects(response, reverse("mice:mouse_detail", args=[self.mouse.pk]))
        self.mouse.refresh_from_db()
        self.assertEqual(self.mouse.strain_line_id, self.strain_b.pk)
