"""Helpers for optional per-user import ID prefixes (e.g. JG-M001 vs M001)."""

from __future__ import annotations

import re

from django.contrib.auth.models import AbstractBaseUser


_PREFIX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,14}$")


def normalize_import_prefix(raw: str) -> str:
    text = (raw or "").strip().upper()
    return text


def validate_import_prefix_format(value: str) -> str:
    """Return normalized prefix or raise ValidationError."""
    from django.core.exceptions import ValidationError

    text = (value or "").strip()
    if not text:
        return ""
    if not _PREFIX_RE.match(text):
        raise ValidationError(
            "Use 1–15 letters, numbers, or hyphens (must start with a letter or number)."
        )
    return text.upper()


def get_effective_import_prefix(user: AbstractBaseUser) -> str | None:
    """Return non-empty normalized prefix if the user has one configured."""
    profile = getattr(user, "profile", None)
    if profile is None:
        return None
    p = normalize_import_prefix(getattr(profile, "import_uid_prefix", "") or "")
    return p or None


def apply_import_prefix_to_id(raw: str, prefix: str) -> str:
    """Return PREFIX-id unless empty or already prefixed with PREFIX-."""
    text = (raw or "").strip()
    if not text:
        return ""
    p = normalize_import_prefix(prefix)
    if not p:
        return text
    sep = f"{p}-"
    if text.upper().startswith(sep):
        return text
    return f"{sep}{text}"
