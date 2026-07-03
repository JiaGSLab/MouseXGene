from __future__ import annotations

from collections.abc import Iterable
from functools import wraps

from django.conf import settings
from django.contrib.auth import get_user_model
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
    """Deprecated for row-level read scoping; lab-wide read uses all projects."""
    if not getattr(user, "is_authenticated", False):
        return set()
    return set(Project.objects.values_list("id", flat=True))


def get_project_membership(user, project: Project | None) -> ProjectMembership | None:
    if project is None or not getattr(user, "is_authenticated", False):
        return None
    return ProjectMembership.objects.filter(user=user, project=project).first()


def get_project_role(user, project: Project | None) -> str | None:
    if project is None or not getattr(user, "is_authenticated", False):
        return None
    if is_admin(user):
        return "admin"
    membership = get_project_membership(user, project)
    return membership.role if membership else None


def can_edit_project_data(user, project: Project | None) -> bool:
    """
    Edit permission for project-scoped business objects (mice, breeding, etc.).

    - Lab Admin: always.
    - Project owner: always.
    - Project membership with role Manager: always (project-level manager rights).
    - Project membership with role Member: yes if the user's lab role is Member or Manager
      (both are expected to work on projects they belong to; import/bulk actions still use
      separate ``can_import`` / admin checks where stricter).
    """
    if project is None or not getattr(user, "is_authenticated", False):
        return False
    if is_admin(user):
        return True
    if getattr(project, "owner_id", None) == user.id:
        return True
    membership = get_project_membership(user, project)
    if membership is None:
        return False
    if membership.role == ProjectMembership.Role.MANAGER:
        return True
    if membership.role == ProjectMembership.Role.MEMBER:
        lab = get_user_role(user)
        if lab in (UserProfile.Role.MEMBER, UserProfile.Role.MANAGER):
            return True
    return False


def ensure_can_edit_project_data(user, project: Project | None, *, denied_message: str = "") -> None:
    if not getattr(user, "is_authenticated", False):
        raise PermissionDenied(denied_message or "Authentication is required.")
    if project is None:
        raise PermissionDenied(denied_message or "A project is required for this action.")
    if not can_edit_project_data(user, project):
        raise PermissionDenied(denied_message or "You do not have permission to modify data in this project.")


def can_archive_or_change_terminal_status(user, project: Project | None) -> bool:
    """End-of-life style changes (mouse terminal status, etc.): admin or project-level Manager only."""
    if project is None or not getattr(user, "is_authenticated", False):
        return False
    if is_admin(user):
        return True
    m = get_project_membership(user, project)
    return bool(m and m.role == ProjectMembership.Role.MANAGER)


def ensure_can_archive_or_change_terminal_status(user, project: Project | None, *, denied_message: str = "") -> None:
    if not getattr(user, "is_authenticated", False):
        raise PermissionDenied(denied_message or "Authentication is required.")
    if project is None:
        raise PermissionDenied(denied_message or "A project is required for this action.")
    if not can_archive_or_change_terminal_status(user, project):
        raise PermissionDenied(
            denied_message or "Only lab admins or project managers can perform this status change."
        )


def can_manage_project_settings(user, project: Project | None) -> bool:
    """Edit project record, membership UI, etc."""
    if project is None or not getattr(user, "is_authenticated", False):
        return False
    if is_admin(user):
        return True
    if getattr(project, "owner_id", None) == user.id:
        return True
    m = get_project_membership(user, project)
    return bool(m and m.role == ProjectMembership.Role.MANAGER)


def is_project_manager(user, project: Project | None) -> bool:
    """Backwards-compatible name: project settings / local manager (not global Manager alone)."""
    return can_manage_project_settings(user, project)


def can_import(user) -> bool:
    return is_admin(user)


def can_create_project(user) -> bool:
    """Create a new project-level permission boundary."""
    return is_admin(user) or is_manager(user)


def can_manage_strain_lines(user) -> bool:
    """Maintain lab-wide strain-line definitions and attachments."""
    return is_admin(user) or is_manager(user)


def can_edit_strain_line(user, strain_line) -> bool:
    """Open the strain-line edit screen without changing lab-wide manager rules."""
    if not getattr(user, "is_authenticated", False):
        return False
    if can_manage_strain_lines(user):
        return True
    user_id = getattr(user, "id", None)
    return bool(
        user_id
        and (
            getattr(strain_line, "owner_id", None) == user_id
            or getattr(strain_line, "created_by_id", None) == user_id
        )
    )


def can_manage_breeding(user) -> bool:
    return is_admin(user) or is_manager(user)


def can_view_audit(user) -> bool:
    return is_admin(user)


def is_project_member(user, project: Project | None) -> bool:
    """Whether the user has any membership on the project (for display helpers, not edit)."""
    if project is None or not getattr(user, "is_authenticated", False):
        return False
    if is_admin(user):
        return True
    return ProjectMembership.objects.filter(user=user, project=project).exists()


def ensure_can_manage_project_membership(user, project: Project | None, *, denied_message: str = "") -> None:
    if project is None:
        raise PermissionDenied(denied_message or "Project is required.")
    if not can_manage_project_settings(user, project):
        raise PermissionDenied(denied_message or "You cannot manage membership for this project.")


def ensure_can_edit_mice_projects(user, mice: Iterable) -> None:
    """Require edit rights for every distinct non-null project among the given Mouse instances."""
    seen: set[int] = set()
    for mouse in mice:
        if mouse is None:
            continue
        project = getattr(mouse, "project", None)
        if project is None:
            continue
        pid = project.pk
        if pid in seen:
            continue
        seen.add(pid)
        ensure_can_edit_project_data(user, project)


def ensure_can_edit_cage(user, cage) -> None:
    """Cage inherits edit scope from mice currently housed in it; empty cages remain editable by any user."""
    from colony.models import Mouse

    if is_admin(user):
        return
    project_ids = set(
        Mouse.objects.filter(current_cage=cage).values_list("project_id", flat=True).distinct()
    )
    if not project_ids:
        return
    for project in Project.objects.filter(pk__in=project_ids):
        ensure_can_edit_project_data(user, project)


def ensure_cage_status_change(user, cage, previous: str, new: str) -> None:
    from colony.models import Cage, Mouse

    if previous == new:
        return
    inactive = {
        Cage.Status.CLOSED,
        Cage.Status.SANITIZING,
        Cage.Status.RETIRED,
        Cage.Status.ARCHIVED,
    }
    has_mice = Mouse.objects.filter(current_cage=cage).exists()
    if not has_mice:
        if not (is_admin(user) or is_manager(user)):
            raise PermissionDenied("Only lab admins or managers can change status on cages without mice.")
        return
    project_ids = set(Mouse.objects.filter(current_cage=cage).values_list("project_id", flat=True).distinct())
    if new in inactive or previous in inactive:
        for project in Project.objects.filter(pk__in=project_ids):
            ensure_can_archive_or_change_terminal_status(user, project)
    else:
        for project in Project.objects.filter(pk__in=project_ids):
            ensure_can_edit_project_data(user, project)


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


def resolve_fallback_owner_user():
    """First superuser, else first profile-marked admin, else first user by id."""
    User = get_user_model()
    u = User.objects.filter(is_superuser=True).order_by("id").first()
    if u:
        return u
    prof = UserProfile.objects.filter(role=UserProfile.Role.ADMIN).select_related("user").order_by("user_id").first()
    if prof:
        return prof.user
    return User.objects.order_by("id").first()
