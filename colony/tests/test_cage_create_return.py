from urllib.parse import parse_qs, urlsplit

from django.contrib.messages import get_messages
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from colony.models import Cage
from core.models import Project
from users.models import UserProfile


class CageCreateReturnTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="cagereturn", password="x")
        UserProfile.objects.filter(user=self.user).update(role=UserProfile.Role.MANAGER)
        self.project = Project.objects.create(name="Cage Return Project", owner=self.user)
        self.client = Client()
        self.client.login(username="cagereturn", password="x")

    def test_create_cage_can_return_to_workflow_and_select_field(self):
        next_url = "/breedings/77/end/?foo=bar&created_cage=old&select_field=old"
        response = self.client.get(
            reverse("colony:cage_create"),
            {
                "next": next_url,
                "select_field": "destination_cage_123",
                "cage_use": Cage.CageUse.HOLDING,
                "status": Cage.Status.ACTIVE,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="next"')
        self.assertContains(response, "/breedings/77/end/?foo=bar")
        self.assertContains(response, 'name="select_field" value="destination_cage_123"')

        response = self.client.post(
            reverse("colony:cage_create"),
            {
                "cage_id": "RETURN-CAGE-1",
                "created_date": "2026-06-10",
                "room": "",
                "rack": "",
                "position": "",
                "cage_use": Cage.CageUse.HOLDING,
                "status": Cage.Status.ACTIVE,
                "notes": "",
                "next": next_url,
                "select_field": "destination_cage_123",
            },
        )

        self.assertEqual(response.status_code, 302)
        created = Cage.objects.get(cage_id="RETURN-CAGE-1")
        location = response["Location"]
        parsed = urlsplit(location)
        params = parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/breedings/77/end/")
        self.assertEqual(params["foo"], ["bar"])
        self.assertEqual(params["created_cage"], [str(created.pk)])
        self.assertEqual(params["select_field"], ["destination_cage_123"])
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertIn("Cage RETURN-CAGE-1 created.", messages)

    def test_unsafe_next_url_is_ignored(self):
        response = self.client.get(
            reverse("colony:cage_create"),
            {
                "next": "https://example.invalid/phish",
                "select_field": "destination_cage_123",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="next"')
        self.assertContains(response, 'name="select_field" value="destination_cage_123"')
