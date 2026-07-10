from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse


class LoginSecurityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="rate-user", password="correct-password")

    def test_repeated_failed_logins_are_rate_limited(self):
        url = reverse("accounts:login")
        for _ in range(8):
            response = self.client.post(url, {"username": self.user.username, "password": "wrong"})
            self.assertEqual(response.status_code, 200)

        response = self.client.post(url, {"username": self.user.username, "password": "wrong"})

        self.assertContains(response, "Too many failed sign-in attempts")
