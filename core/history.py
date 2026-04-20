"""Audit trail helpers for object detail pages."""

from __future__ import annotations

from typing import Any

from django.forms import ModelForm

from .models import AuditLog


def audit_entries_for_object(object_type: str, object_id: str, *, limit: int = 100) -> list[AuditLog]:
    return list(
        AuditLog.objects.filter(object_type=object_type, object_id=str(object_id))
        .select_related("user")
        .order_by("-created_at")[:limit]
    )


def summarize_modelform_changes(form: ModelForm) -> str:
    """Short text describing fields that changed (uses ModelForm.changed_data)."""
    inst = getattr(form, "instance", None)
    if inst is None or not getattr(inst, "pk", None):
        return "Created record."
    changed = getattr(form, "changed_data", None) or []
    if not changed:
        return "Saved (no field changes detected)."
    parts: list[str] = []
    for name in changed:
        if name not in form.cleaned_data:
            continue
        old_raw = form.initial.get(name, "")
        new_val = form.cleaned_data[name]
        if hasattr(new_val, "pk"):
            new_display: Any = new_val.pk
        else:
            new_display = new_val
        if hasattr(old_raw, "pk"):
            old_raw = old_raw.pk
        parts.append(f"{name}: {old_raw!s} → {new_display!s}")
    text = "; ".join(parts[:30])
    if len(parts) > 30:
        text += "; …"
    return text
