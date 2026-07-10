from .permissions import (
    can_create_project,
    can_create_breeding,
    can_import,
    can_manage_breeding,
    can_manage_strain_lines,
    can_view_audit,
    get_user_role,
    is_admin,
    is_manager,
)


def role_permissions(request):
    user = request.user
    return {
        "current_user_role": get_user_role(user),
        "is_admin": is_admin(user),
        "is_manager": is_manager(user),
        "can_create_project": can_create_project(user),
        "can_create_breeding": can_create_breeding(user),
        "can_import": can_import(user),
        "can_manage_breeding": can_manage_breeding(user),
        "can_manage_strain_lines": can_manage_strain_lines(user),
        "can_view_audit": can_view_audit(user),
        "has_project_access": bool(getattr(user, "is_authenticated", False)),
    }
