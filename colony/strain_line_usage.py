"""Strain-line usage counts and related cage/breeding query helpers."""

from __future__ import annotations

from django.db.models import Q, QuerySet

from breeding.models import Breeding, Litter
from colony.models import Cage, Mouse


def strain_line_member_breeding_filter(strain_line_id: int) -> Q:
    return (
        Q(male__strain_line_id=strain_line_id)
        | Q(female_1__strain_line_id=strain_line_id)
        | Q(female_2__strain_line_id=strain_line_id)
        | Q(extra_female_links__mouse__strain_line_id=strain_line_id)
        | Q(breeding_members__mouse__strain_line_id=strain_line_id)
    )


def strain_line_member_litter_filter(strain_line_id: int) -> Q:
    return (
        Q(breeding__male__strain_line_id=strain_line_id)
        | Q(breeding__female_1__strain_line_id=strain_line_id)
        | Q(breeding__female_2__strain_line_id=strain_line_id)
        | Q(breeding__extra_female_links__mouse__strain_line_id=strain_line_id)
        | Q(breeding__breeding_members__mouse__strain_line_id=strain_line_id)
    )


def strain_line_breeding_queryset(*, strain_line_id: int, active_only: bool = False) -> QuerySet[Breeding]:
    qs = Breeding.objects.filter(strain_line_member_breeding_filter(strain_line_id))
    if active_only:
        qs = qs.filter(active=True)
    return qs.distinct()


def strain_line_litter_queryset(*, strain_line_id: int, active_only: bool = False) -> QuerySet[Litter]:
    active_litter_statuses = [
        Litter.LitterStatus.ACTIVE,
        Litter.LitterStatus.WEANED,
        Litter.LitterStatus.TAIL_TAGGED,
    ]
    qs = Litter.objects.filter(strain_line_member_litter_filter(strain_line_id))
    if active_only:
        qs = qs.filter(litter_status__in=active_litter_statuses)
    return qs.distinct()


def _strain_line_breeding_cage_ids(*, strain_line_id: int, active_only: bool) -> set[int]:
    qs = Breeding.objects.filter(strain_line_member_breeding_filter(strain_line_id)).exclude(cage_id__isnull=True)
    if active_only:
        qs = qs.filter(active=True, cage__status=Cage.Status.ACTIVE)
    return set(qs.values_list("cage_id", flat=True))


def _strain_line_housing_cage_ids(*, strain_line_id: int, active_only: bool) -> set[int]:
    mice_qs = Mouse.objects.filter(strain_line_id=strain_line_id).exclude(current_cage_id__isnull=True)
    if active_only:
        mice_qs = mice_qs.filter(status=Mouse.Status.ACTIVE, current_cage__status=Cage.Status.ACTIVE)
    return set(mice_qs.values_list("current_cage_id", flat=True))


def strain_line_cage_ids(*, strain_line_id: int, active_only: bool) -> set[int]:
    """Cages housing strain mice and/or active breedings for this strain."""
    return _strain_line_housing_cage_ids(strain_line_id=strain_line_id, active_only=active_only) | _strain_line_breeding_cage_ids(
        strain_line_id=strain_line_id, active_only=active_only
    )


def strain_line_cage_queryset(*, strain_line_id: int, active_only: bool) -> QuerySet[Cage]:
    cage_ids = strain_line_cage_ids(strain_line_id=strain_line_id, active_only=active_only)
    if not cage_ids:
        return Cage.objects.none()
    qs = Cage.objects.filter(pk__in=cage_ids)
    if active_only:
        qs = qs.filter(status=Cage.Status.ACTIVE)
    return qs.distinct().order_by("cage_id")


def compute_strain_line_usage_counts(strain_line_id: int) -> dict[str, int]:
    active_litter_statuses = [
        Litter.LitterStatus.ACTIVE,
        Litter.LitterStatus.WEANED,
        Litter.LitterStatus.TAIL_TAGGED,
    ]
    mice_qs = Mouse.objects.filter(strain_line_id=strain_line_id)
    breeding_qs = strain_line_breeding_queryset(strain_line_id=strain_line_id)
    litter_qs = strain_line_litter_queryset(strain_line_id=strain_line_id)
    return {
        "active_mice_count": mice_qs.filter(status=Mouse.Status.ACTIVE).count(),
        "total_mice_count": mice_qs.count(),
        "active_cages_count": len(strain_line_cage_ids(strain_line_id=strain_line_id, active_only=True)),
        "total_cages_count": len(strain_line_cage_ids(strain_line_id=strain_line_id, active_only=False)),
        "active_breedings_count": breeding_qs.filter(active=True).count(),
        "total_breedings_count": breeding_qs.count(),
        "active_litters_count": litter_qs.filter(litter_status__in=active_litter_statuses).count(),
        "total_litters_count": litter_qs.count(),
    }


def enrich_strain_line_cage_rows(cages: list[Cage], *, strain_line_id: int) -> None:
    if not cages:
        return
    cage_ids = [cage.pk for cage in cages]
    breeding_by_cage: dict[int, list[str]] = {cid: [] for cid in cage_ids}
    for cage_id, code in (
        Breeding.objects.filter(
            strain_line_member_breeding_filter(strain_line_id),
            cage_id__in=cage_ids,
            active=True,
        )
        .values_list("cage_id", "breeding_code")
        .order_by("breeding_code")
    ):
        breeding_by_cage.setdefault(cage_id, []).append(code)
    for cage in cages:
        strain_mice = [mouse for mouse in cage.current_mice.all() if mouse.strain_line_id == strain_line_id]
        cage.strain_active_mouse_count = sum(1 for mouse in strain_mice if mouse.status == Mouse.Status.ACTIVE)
        cage.strain_total_mouse_count = len(strain_mice)
        cage.strain_breeding_codes = breeding_by_cage.get(cage.pk, [])
