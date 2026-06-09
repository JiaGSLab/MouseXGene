from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from colony.forms import MouseForm
from colony.models import Cage, Mouse, StrainLine
from core.models import Project


class MouseCageLookupTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="mouse_cage_user", password="x")
        self.project = Project.objects.create(name="P1", owner=self.user, is_active=True)
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

    def test_create_form_renders_cage_filters(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("mice:mouse_create"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('id="id_current_cage_lookup"', html)
        self.assertIn('id="id_mouse_cage_owner_filter"', html)
        self.assertIn('id="id_mouse_cage_strain_filter"', html)
        self.assertIn("Create cage", html)
