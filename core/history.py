"""Audit trail helpers for object detail pages."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.forms import ModelForm

from .models import AuditLog, ImportLog, format_project_owner_label


def audit_entries_for_object(object_type: str, object_id: str, *, limit: int = 100) -> list[AuditLog]:
    return list(
        AuditLog.objects.filter(object_type=object_type, object_id=str(object_id))
        .select_related("user")
        .order_by("-created_at")[:limit]
    )


def actor_summary_for_audit_entries(entries: list[AuditLog]) -> dict[str, str]:
    """Best-effort actor labels for created/updated summaries."""
    created_by = "—"
    updated_by = "—"

    if entries:
        latest_with_user = next((e for e in entries if e.user_id), None)
        if latest_with_user is not None:
            updated_by = latest_with_user.user.get_username()

        create_with_user = next((e for e in reversed(entries) if e.action == AuditLog.Action.CREATE and e.user_id), None)
        if create_with_user is not None:
            created_by = create_with_user.user.get_username()
        else:
            oldest_with_user = next((e for e in reversed(entries) if e.user_id), None)
            if oldest_with_user is not None:
                created_by = oldest_with_user.user.get_username()

    return {"created_by": created_by, "updated_by": updated_by}


def _infer_import_actor_label(record: Any) -> str | None:
    """Match bulk file import user from ImportLog when per-row audit/actors are missing."""
    model_name = record._meta.model_name
    if model_name == "mouse":
        import_type = ImportLog.ImportType.MOUSE
    elif model_name == "cage":
        import_type = ImportLog.ImportType.CAGE
    elif model_name in ("strainline", "project"):
        import_type = ImportLog.ImportType.MOUSE
    else:
        return None
    created_at = getattr(record, "created_at", None)
    if created_at is None:
        return None
    logs = list(
        ImportLog.objects.filter(
            import_type=import_type,
            success=True,
            user_id__isnull=False,
            created_at__gte=created_at - timedelta(minutes=10),
            created_at__lte=created_at + timedelta(minutes=15),
        ).select_related("user", "user__profile")
    )
    if not logs:
        return None
    best = min(logs, key=lambda log: abs((log.created_at - created_at).total_seconds()))
    return (format_project_owner_label(best.user) or "").strip() or None


def merge_actor_labels(record: Any | None, entries: list[AuditLog]) -> dict[str, str]:
    """Prefer persisted created_by / updated_by on the record; fall back to audit-derived names."""
    summary = actor_summary_for_audit_entries(entries)
    if record is None:
        return summary
    if getattr(record, "created_by_id", None):
        label = (format_project_owner_label(record.created_by) or "").strip()
        if label:
            summary["created_by"] = label
    if getattr(record, "updated_by_id", None):
        label = (format_project_owner_label(record.updated_by) or "").strip()
        if label:
            summary["updated_by"] = label
    # Bulk import logs one summary row (object_id is count, not PK); ImportLog still has the user.
    dash = "—"
    if not getattr(record, "created_by_id", None) and summary.get("created_by") == dash:
        lbl = _infer_import_actor_label(record)
        if lbl:
            summary["created_by"] = lbl
    if not getattr(record, "updated_by_id", None) and summary.get("updated_by") == dash:
        lbl = _infer_import_actor_label(record)
        if lbl:
            summary["updated_by"] = lbl
    return summary


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
