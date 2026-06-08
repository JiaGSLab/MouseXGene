from __future__ import annotations

from django.core.exceptions import ValidationError

from .models import Cage, Mouse


def normalize_identifier(value: str | None) -> str:
    return (value or "").strip()


def find_conflicting_cage(cage_id: str, *, exclude_pk: int | None = None) -> Cage | None:
    normalized = normalize_identifier(cage_id)
    if not normalized:
        return None
    qs = Cage.objects.filter(cage_id__iexact=normalized)
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    return qs.first()


def find_conflicting_mouse(mouse_uid: str, *, exclude_pk: int | None = None) -> Mouse | None:
    normalized = normalize_identifier(mouse_uid)
    if not normalized:
        return None
    qs = Mouse.objects.filter(mouse_uid__iexact=normalized)
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    return qs.first()


def _format_id_conflict_message(*, kind: str, identifier: str, conflict_status: str, conflict_pk: int) -> str:
    return (
        f"{kind} '{identifier}' is already used by record #{conflict_pk} ({conflict_status}). "
        "IDs are permanently reserved and cannot be reused, including inactive, retired, or archived records."
    )


def validate_cage_id_available(cage_id: str, *, exclude_pk: int | None = None) -> None:
    conflict = find_conflicting_cage(cage_id, exclude_pk=exclude_pk)
    if conflict is None:
        return
    raise ValidationError(
        _format_id_conflict_message(
            kind="Cage ID",
            identifier=normalize_identifier(cage_id),
            conflict_status=conflict.get_status_display(),
            conflict_pk=conflict.pk,
        )
    )


def validate_mouse_uid_available(mouse_uid: str, *, exclude_pk: int | None = None) -> None:
    conflict = find_conflicting_mouse(mouse_uid, exclude_pk=exclude_pk)
    if conflict is None:
        return
    raise ValidationError(
        _format_id_conflict_message(
            kind="Mouse UID",
            identifier=normalize_identifier(mouse_uid),
            conflict_status=conflict.get_status_display(),
            conflict_pk=conflict.pk,
        )
    )
