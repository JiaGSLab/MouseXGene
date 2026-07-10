from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase

from core.views import permission_denied


class PermissionDeniedHandlerTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_user(username="post403", password="x")

    def _request_with_messages(self, request):
        request.user = self.user
        request.session = {}
        request._messages = FallbackStorage(request)
        return request

    def test_post_permission_denied_shows_explicit_not_saved_page_with_return_link(self):
        request = self._request_with_messages(
            self.factory.post(
                "/mice/new/",
                HTTP_REFERER="http://testserver/mice/new/",
            )
        )

        response = permission_denied(
            request,
            PermissionDenied("You do not have permission to modify data in this project."),
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn(
            b"Nothing was saved. You do not have permission to modify data in this project.",
            response.content,
        )
        self.assertIn(b'href="http://testserver/mice/new/"', response.content)

    def test_post_permission_denied_without_referer_has_no_return_link(self):
        request = self._request_with_messages(self.factory.post("/mice/new/?project=20"))

        response = permission_denied(request, PermissionDenied("Project is required for this action."))

        self.assertEqual(response.status_code, 403)
        self.assertIn(b"Nothing was saved. Project is required for this action.", response.content)
        self.assertNotIn(b"Return to previous page", response.content)
