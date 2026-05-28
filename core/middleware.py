from __future__ import annotations

from typing import Callable

from django.http import HttpRequest, HttpResponse

from .current_actor import clear_current_actor, set_current_actor


class CurrentActorMiddleware:
    """Expose request.user to model saves for created_by / updated_by stamping."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        user = getattr(request, "user", None)
        if getattr(user, "is_authenticated", False):
            set_current_actor(user)
        else:
            set_current_actor(None)
        try:
            response = self.get_response(request)
        finally:
            clear_current_actor()
        return response


class NoCacheHtmlForAuthenticatedMiddleware:
    """Prevent browsers from serving stale list pages (sort/export UI) to logged-in users."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        user = getattr(request, "user", None)
        if not getattr(user, "is_authenticated", False):
            return response
        content_type = (response.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type == "text/html":
            response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response["Pragma"] = "no-cache"
            response["Expires"] = "0"
        return response
