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

    def test_post_permission_denied_redirects_back_to_same_form_with_not_saved_message(self):
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

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "http://testserver/mice/new/")
        stored_messages = [str(message) for message in request._messages]
        self.assertEqual(
            stored_messages,
            ["Nothing was saved. You do not have permission to modify data in this project."],
        )

    def test_post_permission_denied_without_referer_redirects_to_current_path(self):
        request = self._request_with_messages(self.factory.post("/mice/new/?project=20"))

        response = permission_denied(request, PermissionDenied("Project is required for this action."))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/mice/new/?project=20")
