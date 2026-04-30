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
            return self.get_response(request)
        finally:
            clear_current_actor()
