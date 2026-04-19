"""Age tiers for breeding / pairing prompts (calendar days from birth_date)."""

from __future__ import annotations

from datetime import date

from django.utils import timezone

# ~3 months, ~4 months, ~6 months (avoid calendar-month ambiguity)
DAY_TIER_ELEVATED = 90
DAY_TIER_CAUTION = 122
DAY_TIER_HIGH = 183

# Mouse list row styling (calendar days)
DAY_LIST_SIX_MONTH = 183
DAY_LIST_ONE_YEAR = 365


class BreedingAgeTier:
    NONE = "none"
    ELEVATED = "elevated"
    CAUTION = "caution"
    HIGH = "high"


# Short labels for chips (English UI)
TIER_SHORT_LABEL = {
    BreedingAgeTier.NONE: "",
    BreedingAgeTier.ELEVATED: "~3mo+",
    BreedingAgeTier.CAUTION: "~4mo+",
    BreedingAgeTier.HIGH: "~6mo+",
}

# Longer hint text for forms / banners
TIER_HINT = {
    BreedingAgeTier.ELEVATED: "Breeding note: about 3+ months old — monitor pairing.",
    BreedingAgeTier.CAUTION: "Breeding caution: about 4+ months old — fertility and outcomes may vary.",
    BreedingAgeTier.HIGH: "Breeding warning: about 6+ months old — review breeding plans carefully.",
}

HIGH_AGE_BANNER = (
    "This mouse is about six months old or older ({days} days). "
    "Older breeders may have lower fertility and higher risk — plan pairings carefully."
)


def age_days(birth_date: date | None, today: date | None = None) -> int | None:
    if birth_date is None:
        return None
    today = today or timezone.localdate()
    d = (today - birth_date).days
    return d if d >= 0 else None


def breeding_age_tier(birth_date: date | None, today: date | None = None) -> str:
    days = age_days(birth_date, today)
    if days is None:
        return BreedingAgeTier.NONE
    if days >= DAY_TIER_HIGH:
        return BreedingAgeTier.HIGH
    if days >= DAY_TIER_CAUTION:
        return BreedingAgeTier.CAUTION
    if days >= DAY_TIER_ELEVATED:
        return BreedingAgeTier.ELEVATED
    return BreedingAgeTier.NONE


def breeding_age_tier_for_mouse(mouse, today: date | None = None) -> str:
    return breeding_age_tier(mouse.birth_date, today)


def mouse_list_age_band(birth_date: date | None, today: date | None = None) -> str:
    """
    CSS / template band for the Mice list page.

    Returns:
        '' — young or no highlight (< 183d, or future birth date)
        'unknown' — no birth_date
        '6mo' — >= 183d and < 365d
        '1yr' — >= 365d
    """
    if birth_date is None:
        return "unknown"
    today = today or timezone.localdate()
    d = (today - birth_date).days
    if d < 0:
        return ""
    if d >= DAY_LIST_ONE_YEAR:
        return "1yr"
    if d >= DAY_LIST_SIX_MONTH:
        return "6mo"
    return ""


def tier_map_for_breeding_select_mice(today: date | None = None) -> dict[str, str]:
    """All male + female mice pk -> tier string for JSON in breeding form."""
    from colony.models import Mouse

    today = today or timezone.localdate()
    merged: dict[str, str] = {}
    for pk, bd in (
        Mouse.objects.filter(sex=Mouse.Sex.MALE)
        .values_list("pk", "birth_date")
        .iterator()
    ):
        merged[str(pk)] = breeding_age_tier(bd, today)
    for pk, bd in (
        Mouse.objects.filter(sex=Mouse.Sex.FEMALE)
        .values_list("pk", "birth_date")
        .iterator()
    ):
        merged[str(pk)] = breeding_age_tier(bd, today)
    return merged
