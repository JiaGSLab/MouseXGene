from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from colony.forms import MouseForm
from colony.models import Mouse, StrainLine
from core.models import Project, ProjectMembership
from users.models import UserProfile


class MouseStrainLineLockTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(username="strainadmin", password="x")
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

    def test_non_admin_form_keeps_existing_strain_line(self):
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

    def test_edit_page_disables_strain_line_for_non_admin(self):
        client = Client()
        client.login(username="strainmember", password="x")
        response = client.get(reverse("mice:mouse_edit", args=[self.mouse.pk]))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("Only lab admins can change strain line after it is set.", html)
        self.assertIn('id="id_strain_line"', html)

    def test_edit_page_includes_status_initial_attribute(self):
        client = Client()
        client.login(username="strainmember", password="x")
        response = client.get(reverse("mice:mouse_edit", args=[self.mouse.pk]))
        self.assertContains(response, 'data-initial-status="active"')
