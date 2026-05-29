"""Preset + custom choice helpers for strain line Definition fields."""

from __future__ import annotations

CUSTOM_SELECT_VALUE = "__custom__"


def choice_field_with_custom(
    presets: type,
    *,
    custom_label: str = "Custom (type below)",
) -> list[tuple[str, str]]:
    return list(presets.choices) + [(CUSTOM_SELECT_VALUE, custom_label)]


def resolve_choice_or_custom(
    selected: str,
    custom_text: str,
    presets: type,
    *,
    field_label: str,
) -> str:
    selected = (selected or "").strip()
    custom_text = (custom_text or "").strip()
    if selected == CUSTOM_SELECT_VALUE:
        if not custom_text:
            raise ValueError(f"{field_label} is required when Custom is selected.")
        return custom_text
    if selected in presets.values:
        return selected
    if custom_text:
        return custom_text
    if selected:
        return selected
    raise ValueError(f"Select a {field_label.lower()} or enter a custom value.")


def preset_select_initial(stored: str, presets: type) -> tuple[str, str]:
    """Return (select_value, custom_text) for form init."""
    stored = (stored or "").strip()
    if stored in presets.values:
        return stored, ""
    if stored:
        return CUSTOM_SELECT_VALUE, stored
    return "", ""
