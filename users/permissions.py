from __future__ import annotations

from functools import wraps

from django.core.exceptions import PermissionDenied
from django.contrib.auth.views import redirect_to_login

from .models import UserProfile


def get_user_role(user) -> str:
    if not getattr(user, "is_authenticated", False):
        return UserProfile.Role.MEMBER
    if getattr(user, "is_superuser", False):
        return UserProfile.Role.ADMIN
    profile = getattr(user, "profile", None)
    if profile is None:
        return UserProfile.Role.MEMBER
    return profile.role or UserProfile.Role.MEMBER


def is_admin(user) -> bool:
    return get_user_role(user) == UserProfile.Role.ADMIN


def is_manager(user) -> bool:
    return get_user_role(user) == UserProfile.Role.MANAGER


def can_import(user) -> bool:
    return is_admin(user) or is_manager(user)


def can_manage_breeding(user) -> bool:
    return is_admin(user) or is_manager(user)


def can_view_audit(user) -> bool:
    return is_admin(user) or is_manager(user)


def role_required(check_fn, denied_message: str = "You do not have permission to access this page."):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path(), "/admin/login/")
            if not check_fn(request.user):
                raise PermissionDenied(denied_message)
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator


def authenticated_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), "/admin/login/")
        return view_func(request, *args, **kwargs)

    return _wrapped
