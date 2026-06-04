from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from breeding.models import Breeding
from colony.models import Cage, Mouse


def breeding_sire_and_dams(breeding: Breeding) -> tuple[Mouse | None, list[Mouse]]:
    members = list(breeding.breeding_members.select_related("mouse").order_by("sort_order", "mouse__mouse_uid"))
    if members:
        sire: Mouse | None = None
        dams: list[Mouse] = []
        for row in members:
            if row.role == Breeding.MemberRole.SIRE and sire is None:
                sire = row.mouse
            elif row.role == Breeding.MemberRole.DAM:
                dams.append(row.mouse)
        return sire, dams
    sire = breeding.male
    dams = []
    if breeding.female_1_id:
        dams.append(breeding.female_1)
    if breeding.female_2_id:
        dams.append(breeding.female_2)
    for row in breeding.extra_female_links.select_related("mouse").order_by("mouse__mouse_uid"):
        if row.mouse not in dams:
            dams.append(row.mouse)
    return sire, dams


def resolve_breeding_for_import_cage(cage: Cage, *, birth_date: date | None = None) -> tuple[Breeding | None, str | None]:
    """Pick the breeding record on a cage for import pedigree linking."""
    qs = Breeding.objects.filter(cage=cage).order_by("-start_date", "-pk")
    active = list(qs.filter(active=True))
    pool = active if active else list(qs)
    if not pool:
        return None, f"No breeding record found for cage '{cage.cage_id}'."

    if birth_date:
        dated = [breeding for breeding in pool if breeding.start_date <= birth_date]
        if dated:
            pool = dated

    if len(pool) == 1:
        return pool[0], None

    best_date = pool[0].start_date
    best = [breeding for breeding in pool if breeding.start_date == best_date]
    if len(best) == 1:
        return best[0], None

    codes = ", ".join(b.breeding_code for b in best[:5])
    hint = "Add birth_date or close/archive older breedings on this cage."
    return None, f"Multiple breeding records match cage '{cage.cage_id}' ({codes}). {hint}"


@dataclass(frozen=True)
class MouseFamilyPedigree:
    sire: Mouse | None
    dams: list[Mouse]
    breeding_cage: Cage | None
    source_breeding: Breeding | None


def mouse_family_pedigree(mouse: Mouse) -> MouseFamilyPedigree:
    if mouse.source_breeding_id:
        breeding = mouse.source_breeding
        sire, dams = breeding_sire_and_dams(breeding)
        if mouse.sire_id and sire is None:
            sire = mouse.sire
        return MouseFamilyPedigree(
            sire=sire or mouse.sire,
            dams=dams,
            breeding_cage=breeding.cage,
            source_breeding=breeding,
        )
    dams = [mouse.dam] if mouse.dam_id else []
    return MouseFamilyPedigree(
        sire=mouse.sire if mouse.sire_id else None,
        dams=dams,
        breeding_cage=None,
        source_breeding=None,
    )


def littermate_queryset_for_mouse(mouse: Mouse, base_qs):
    if mouse.source_breeding_id:
        return base_qs.filter(source_breeding_id=mouse.source_breeding_id).exclude(pk=mouse.pk)
    if mouse.sire_id and mouse.dam_id:
        return base_qs.filter(sire_id=mouse.sire_id, dam_id=mouse.dam_id).exclude(pk=mouse.pk)
    return base_qs.none()
