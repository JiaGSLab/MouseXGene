"""Consistency checks for breeding cage workflows."""

from __future__ import annotations

from typing import Iterable

from django.db.models import F, Q, QuerySet

from colony.models import Mouse

from .models import Breeding


def _related_rows(instance, related_name: str, *, select_related: tuple[str, ...] = ()):
    prefetched = getattr(instance, "_prefetched_objects_cache", {})
    if related_name in prefetched:
        return list(prefetched[related_name])
    qs = getattr(instance, related_name).all()
    if select_related:
        qs = qs.select_related(*select_related)
    return list(qs)


def breeding_member_role_rows(breeding: Breeding) -> list[dict]:
    """Return sire/dam member rows with roles, preferring the flexible member table."""
    rows: list[dict] = []
    seen: set[int] = set()
    try:
        member_rows = _related_rows(
            breeding,
            "breeding_members",
            select_related=("mouse", "mouse__current_cage"),
        )
    except Exception:
        member_rows = []
    if member_rows:
        for member in member_rows:
            if member.mouse_id in seen:
                continue
            seen.add(member.mouse_id)
            rows.append(
                {
                    "role": member.get_role_display(),
                    "mouse": member.mouse,
                }
            )
        return rows

    legacy_rows = [
        ("Sire", getattr(breeding, "male", None)),
        ("Dam", getattr(breeding, "female_1", None)),
        ("Dam", getattr(breeding, "female_2", None)),
    ]
    try:
        legacy_rows.extend(
            ("Dam", link.mouse)
            for link in _related_rows(
                breeding,
                "extra_female_links",
                select_related=("mouse", "mouse__current_cage"),
            )
        )
    except Exception:
        pass
    for role, mouse in legacy_rows:
        if mouse is None or mouse.pk in seen:
            continue
        seen.add(mouse.pk)
        rows.append({"role": role, "mouse": mouse})
    return rows


def breeding_cage_mismatch_rows(breeding: Breeding) -> list[dict]:
    """Active breeding members must occupy the breeding cage."""
    if not breeding.active or breeding.status == Breeding.Status.CLOSED or not breeding.cage_id:
        return []
    rows: list[dict] = []
    for member in breeding_member_role_rows(breeding):
        mouse: Mouse = member["mouse"]
        if mouse.status != Mouse.Status.ACTIVE:
            continue
        if mouse.current_cage_id == breeding.cage_id:
            continue
        rows.append(
            {
                "role": member["role"],
                "mouse": mouse,
                "expected_cage": breeding.cage,
                "current_cage": mouse.current_cage,
            }
        )
    return rows


def active_breeding_cage_mismatches(breedings: QuerySet[Breeding] | Iterable[Breeding]) -> list[Breeding]:
    """Attach cage_mismatch_rows to active breedings with off-cage breeders."""
    if isinstance(breedings, QuerySet):
        breedings = (
            breedings.filter(active=True)
            .exclude(status=Breeding.Status.CLOSED)
            .select_related(
                "cage",
                "male",
                "male__current_cage",
                "female_1",
                "female_1__current_cage",
                "female_2",
                "female_2__current_cage",
            )
            .prefetch_related("extra_female_links__mouse__current_cage", "breeding_members__mouse__current_cage")
            .order_by("breeding_code")
        )
    mismatches: list[Breeding] = []
    for breeding in breedings:
        rows = breeding_cage_mismatch_rows(breeding)
        if not rows:
            continue
        breeding.cage_mismatch_rows = rows
        mismatches.append(breeding)
    return mismatches


def active_breeding_cage_mismatch_candidates(breedings: QuerySet[Breeding]) -> QuerySet[Breeding]:
    """Filter mismatch candidates in SQL before detailed Python enrichment."""
    return (
        breedings.filter(active=True, cage__isnull=False)
        .exclude(status=Breeding.Status.CLOSED)
        .filter(
            Q(male__status=Mouse.Status.ACTIVE)
            & (Q(male__current_cage__isnull=True) | ~Q(male__current_cage_id=F("cage_id")))
            | Q(female_1__status=Mouse.Status.ACTIVE)
            & (Q(female_1__current_cage__isnull=True) | ~Q(female_1__current_cage_id=F("cage_id")))
            | Q(female_2__status=Mouse.Status.ACTIVE)
            & (Q(female_2__current_cage__isnull=True) | ~Q(female_2__current_cage_id=F("cage_id")))
            | Q(extra_female_links__mouse__status=Mouse.Status.ACTIVE)
            & (
                Q(extra_female_links__mouse__current_cage__isnull=True)
                | ~Q(extra_female_links__mouse__current_cage_id=F("cage_id"))
            )
            | Q(breeding_members__mouse__status=Mouse.Status.ACTIVE)
            & (
                Q(breeding_members__mouse__current_cage__isnull=True)
                | ~Q(breeding_members__mouse__current_cage_id=F("cage_id"))
            )
        )
        .distinct()
    )


def active_breedings_for_mouse(mouse: Mouse) -> QuerySet[Breeding]:
    """Active breedings where the mouse is a sire or dam."""
    return (
        Breeding.objects.filter(active=True)
        .exclude(status=Breeding.Status.CLOSED)
        .filter(
            models_q_for_mouse(mouse)
        )
        .select_related("cage")
        .distinct()
    )


def models_q_for_mouse(mouse: Mouse):
    return (
        Q(male_id=mouse.pk)
        | Q(female_1_id=mouse.pk)
        | Q(female_2_id=mouse.pk)
        | Q(extra_female_links__mouse_id=mouse.pk)
        | Q(breeding_members__mouse_id=mouse.pk)
    )
