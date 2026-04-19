from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from colony.models import Cage, Mouse, StrainLine
from core.models import Project, ProjectMembership
from users.models import UserProfile


class MouseEditPermissionTests(TestCase):
    def setUp(self) -> None:
        self.client = Client(enforce_csrf_checks=False)

        self.admin = User.objects.create_user(username="admin", password="pass")
        UserProfile.objects.filter(user=self.admin).update(role=UserProfile.Role.ADMIN)

        self.member = User.objects.create_user(username="member", password="pass")
        UserProfile.objects.filter(user=self.member).update(role=UserProfile.Role.MEMBER)

        self.manager_member_only = User.objects.create_user(username="mgr_mem", password="pass")
        UserProfile.objects.filter(user=self.manager_member_only).update(role=UserProfile.Role.MANAGER)

        self.manager_project_mgr = User.objects.create_user(username="mgr_pm", password="pass")
        UserProfile.objects.filter(user=self.manager_project_mgr).update(role=UserProfile.Role.MANAGER)

        self.strain = StrainLine.objects.create(
            line_name="PermTestLine",
            name="PermTestLine",
            short_name="PTL",
            category=StrainLine.Category.OTHER,
        )
        self.cage = Cage.objects.create(cage_id="P-C01", status=Cage.Status.ACTIVE)

        self.project_a = Project.objects.create(name="Project A", owner=self.admin)
        self.project_b = Project.objects.create(name="Project B", owner=self.admin)

        ProjectMembership.objects.create(
            project=self.project_a, user=self.member, role=ProjectMembership.Role.MEMBER
        )
        ProjectMembership.objects.create(
            project=self.project_a,
            user=self.manager_project_mgr,
            role=ProjectMembership.Role.MANAGER,
        )
        ProjectMembership.objects.create(
            project=self.project_b,
            user=self.manager_member_only,
            role=ProjectMembership.Role.MEMBER,
        )

        self.mouse_a = Mouse.objects.create(
            mouse_uid="M-A-1",
            sex=Mouse.Sex.MALE,
            status=Mouse.Status.ACTIVE,
            strain_line=self.strain,
            current_cage=self.cage,
            project=self.project_a,
        )
        self.mouse_b = Mouse.objects.create(
            mouse_uid="M-B-1",
            sex=Mouse.Sex.FEMALE,
            status=Mouse.Status.ACTIVE,
            strain_line=self.strain,
            current_cage=self.cage,
            project=self.project_b,
        )

    def test_member_can_open_edit_for_own_project_mouse(self) -> None:
        self.client.login(username="member", password="pass")
        r = self.client.get(reverse("mice:mouse_edit", args=[self.mouse_a.pk]))
        self.assertEqual(r.status_code, 200)

    def test_member_cannot_open_edit_for_other_project_mouse(self) -> None:
        self.client.login(username="member", password="pass")
        r = self.client.get(reverse("mice:mouse_edit", args=[self.mouse_b.pk]))
        self.assertEqual(r.status_code, 403)

    def test_manager_with_project_manager_role_can_edit_managed_mouse(self) -> None:
        self.client.login(username="mgr_pm", password="pass")
        r = self.client.get(reverse("mice:mouse_edit", args=[self.mouse_a.pk]))
        self.assertEqual(r.status_code, 200)

    def test_global_manager_without_project_manager_cannot_edit_unrelated_mouse(self) -> None:
        self.client.login(username="mgr_mem", password="pass")
        r = self.client.get(reverse("mice:mouse_edit", args=[self.mouse_a.pk]))
        self.assertEqual(r.status_code, 403)

    def test_lab_manager_with_project_member_role_can_edit_mouse_in_that_project(self) -> None:
        """Lab-level Manager + project Membership as Member should edit mice in that project."""
        self.client.login(username="mgr_mem", password="pass")
        r = self.client.get(reverse("mice:mouse_edit", args=[self.mouse_b.pk]))
        self.assertEqual(r.status_code, 200)

    def test_admin_can_edit_any_mouse(self) -> None:
        self.client.login(username="admin", password="pass")
        r = self.client.get(reverse("mice:mouse_edit", args=[self.mouse_b.pk]))
        self.assertEqual(r.status_code, 200)
