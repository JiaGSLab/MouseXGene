from django import template

from colony.mouse_age import (
    HIGH_AGE_BANNER,
    TIER_SHORT_LABEL,
    BreedingAgeTier,
    age_days,
    breeding_age_tier_for_mouse,
)

register = template.Library()


@register.inclusion_tag("includes/mouse_breeding_age_chip.html")
def mouse_breeding_age_chip(mouse):
    if not mouse:
        return {"show": False}
    tier = breeding_age_tier_for_mouse(mouse)
    if tier == BreedingAgeTier.NONE:
        return {"show": False}
    d = age_days(mouse.birth_date)
    return {
        "show": True,
        "tier": tier,
        "days": d,
        "short_label": TIER_SHORT_LABEL.get(tier, ""),
    }


@register.inclusion_tag("includes/mouse_breeding_high_age_banner.html")
def mouse_breeding_high_age_banner(mouse):
    if not mouse:
        return {"show": False}
    tier = breeding_age_tier_for_mouse(mouse)
    if tier != BreedingAgeTier.HIGH:
        return {"show": False}
    d = age_days(mouse.birth_date)
    if d is None:
        return {"show": False}
    return {"show": True, "message": HIGH_AGE_BANNER.format(days=d)}
