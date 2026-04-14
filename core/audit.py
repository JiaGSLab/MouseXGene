from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser

from .models import AuditLog


def log_audit_event(
    *,
    user: AbstractBaseUser | None,
    action: str,
    message: str,
    obj=None,
    object_type: str = "",
    object_id: str = "",
    object_repr: str = "",
) -> AuditLog:
    resolved_object_type = object_type or (obj.__class__.__name__ if obj is not None else "Unknown")
    resolved_object_id = object_id or (str(getattr(obj, "pk", "")) if obj is not None else "")
    resolved_object_repr = object_repr or (str(obj) if obj is not None else "")
    resolved_user = user if getattr(user, "is_authenticated", False) else None

    return AuditLog.objects.create(
        user=resolved_user,
        action=action,
        object_type=resolved_object_type,
        object_id=resolved_object_id,
        object_repr=resolved_object_repr,
        message=message,
    )
