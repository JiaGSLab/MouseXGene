from __future__ import annotations

from functools import wraps

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.contrib.auth.views import redirect_to_login

from core.models import Project, ProjectMembership
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


def get_accessible_project_ids(user) -> set[int]:
    if not getattr(user, "is_authenticated", False):
        return set()
    return set(Project.objects.values_list("id", flat=True))


def get_project_role(user, project: Project | None) -> str | None:
    if project is None or not getattr(user, "is_authenticated", False):
        return None
    if is_admin(user):
        return "admin"
    membership = (
        ProjectMembership.objects.filter(user=user, project=project).values_list("role", flat=True).first()
    )
    return membership


def is_project_manager(user, project: Project | None) -> bool:
    if is_admin(user) or is_manager(user):
        return True
    return get_project_role(user, project) == ProjectMembership.Role.MANAGER


def can_import(user) -> bool:
    return is_admin(user) or is_manager(user)


def can_manage_breeding(user) -> bool:
    return can_import(user)


def can_view_audit(user) -> bool:
    return is_admin(user)


def is_project_member(user, project: Project | None) -> bool:
    if project is None:
        return False
    if is_admin(user) or is_manager(user):
        return True
    if not getattr(user, "is_authenticated", False):
        return False
    return True


def ensure_can_manage_project(user, project: Project | None, *, denied_message: str = "") -> None:
    if not getattr(user, "is_authenticated", False):
        raise PermissionDenied(denied_message or "Authentication is required.")
    if project is None:
        return
    return


def ensure_can_manage_project_membership(user, project: Project | None, *, denied_message: str = "") -> None:
    if project is None:
        raise PermissionDenied(denied_message or "Project is required.")
    if is_admin(user) or is_manager(user):
        return
    raise PermissionDenied(denied_message or "Only admin or manager can manage project membership.")


def role_required(check_fn, denied_message: str = "You do not have permission to access this page."):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path(), login_url=settings.LOGIN_URL)
            if not check_fn(request.user):
                raise PermissionDenied(denied_message)
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator


def authenticated_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), login_url=settings.LOGIN_URL)
        return view_func(request, *args, **kwargs)

    return _wrapped
