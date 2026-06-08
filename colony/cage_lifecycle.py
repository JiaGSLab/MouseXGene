"""Keep cage status and breeding records aligned with colony workflow."""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from breeding.models import Breeding, BreedingExtraFemale

from .models import Cage, Mouse

logger = logging.getLogger(__name__)


def _generate_breeding_code() -> str:
    prefix = timezone.localdate().strftime("BR-%Y%m%d")
    n = 1
    while True:
        candidate = f"{prefix}-{n:03d}"
        if not Breeding.objects.filter(breeding_code=candidate).exists():
            return candidate
        n += 1


def mark_cage_as_breeding(cage: Cage | None) -> None:
    if cage is None:
        return
    updates: list[str] = []
    if cage.purpose != Cage.Purpose.BREEDING:
        cage.purpose = Cage.Purpose.BREEDING
        updates.append("purpose")
    if cage.cage_type != Cage.CageType.BREEDING:
        cage.cage_type = Cage.CageType.BREEDING
        updates.append("cage_type")
    if updates:
        updates.append("updated_at")
        cage.save(update_fields=updates)


def sync_cage_status_from_mice(cage: Cage | None) -> bool:
    """Close active cages whose current occupants are all non-active. Returns True if status changed."""
    if cage is None:
        return False
    mice = list(cage.current_mice.all())
    if not mice:
        return False
    if any(mouse.status == Mouse.Status.ACTIVE for mouse in mice):
        return False
    if cage.status != Cage.Status.ACTIVE:
        return False
    cage.status = Cage.Status.CLOSED
    cage.save(update_fields=["status", "updated_at"])
    return True


def sync_cage_status_for_cage_id(cage_id: int | None) -> bool:
    if not cage_id:
        return False
    cage = Cage.objects.filter(pk=cage_id).first()
    if cage is None:
        return False
    return sync_cage_status_from_mice(cage)


def ensure_breeding_for_cage(cage: Cage | None) -> Breeding | None:
    """Create or refresh an active breeding when a cage is marked for breeding and has breeders."""
    if cage is None or cage.purpose != Cage.Purpose.BREEDING:
        return None

    active_mice = list(
        cage.current_mice.filter(status=Mouse.Status.ACTIVE).order_by("mouse_uid")
    )
    males = [mouse for mouse in active_mice if mouse.sex == Mouse.Sex.MALE]
    females = [mouse for mouse in active_mice if mouse.sex == Mouse.Sex.FEMALE]
    if not males or not females:
        return None

    sire = males[0]
    dams = females[:3]
    breeding_type = Breeding.BreedingType.PAIR
    if len(dams) == 2:
        breeding_type = Breeding.BreedingType.TRIO
    elif len(dams) >= 3:
        breeding_type = Breeding.BreedingType.CUSTOM

    existing = (
        Breeding.objects.filter(cage=cage, active=True)
        .exclude(status=Breeding.Status.CLOSED)
        .select_related("male", "female_1", "female_2")
        .order_by("-start_date", "-pk")
        .first()
    )

    with transaction.atomic():
        if existing is None:
            breeding = Breeding(
                breeding_code=_generate_breeding_code(),
                cage=cage,
                breeding_type=breeding_type,
                male=sire,
                female_1=dams[0],
                female_2=dams[1] if len(dams) > 1 else None,
                start_date=timezone.localdate(),
                status=Breeding.Status.SETUP,
                active=True,
                notes="Auto-created when cage purpose is breeding.",
            )
            breeding.full_clean()
            breeding.save()
        else:
            breeding = existing
            breeding.breeding_type = breeding_type
            breeding.male = sire
            breeding.female_1 = dams[0]
            breeding.female_2 = dams[1] if len(dams) > 1 else None
            breeding.full_clean()
            breeding.save(
                update_fields=[
                    "breeding_type",
                    "male",
                    "female_1",
                    "female_2",
                    "updated_at",
                ]
            )

        extra_dams = dams[2:]
        extra_ids = {mouse.pk for mouse in extra_dams}
        BreedingExtraFemale.objects.filter(breeding=breeding).exclude(mouse_id__in=extra_ids).delete()
        existing_extra_ids = set(
            BreedingExtraFemale.objects.filter(breeding=breeding).values_list("mouse_id", flat=True)
        )
        BreedingExtraFemale.objects.bulk_create(
            [
                BreedingExtraFemale(breeding=breeding, mouse=mouse)
                for mouse in extra_dams
                if mouse.pk not in existing_extra_ids
            ]
        )
        try:
            breeding.sync_members_from_legacy_fields()
        except Exception:
            logger.exception("Failed to sync breeding members for cage %s", cage.cage_id)

    return breeding


def breeding_setup_status(cage: Cage) -> dict:
    active_mice = list(cage.current_mice.filter(status=Mouse.Status.ACTIVE).order_by("mouse_uid"))
    males = [mouse for mouse in active_mice if mouse.sex == Mouse.Sex.MALE]
    females = [mouse for mouse in active_mice if mouse.sex == Mouse.Sex.FEMALE]
    has_active_breeding = (
        Breeding.objects.filter(cage=cage, active=True)
        .exclude(status=Breeding.Status.CLOSED)
        .exists()
    )
    return {
        "ready": bool(males and females),
        "missing_sire": not males,
        "missing_dam": not females,
        "male_count": len(males),
        "female_count": len(females),
        "has_breeding_record": has_active_breeding,
        "males": males,
        "females": females,
    }


def breeding_setup_message(cage: Cage) -> str:
    status = breeding_setup_status(cage)
    if status["has_breeding_record"]:
        return ""
    if status["ready"]:
        return (
            f"Cage {cage.cage_id} is ready for breeding but no breeding record was created. "
            "Save again or add mice to retry."
        )
    if status["missing_sire"] and status["missing_dam"]:
        return (
            f"Cage {cage.cage_id} marked as breeding. "
            "Add at least one active male and one active female to create a breeding record."
        )
    if status["missing_sire"]:
        return (
            f"Cage {cage.cage_id} marked as breeding. "
            "Add at least one active male to create a breeding record "
            f"({status['female_count']} active female(s) already in cage)."
        )
    return (
        f"Cage {cage.cage_id} marked as breeding. "
        "Add at least one active female to create a breeding record "
        f"({status['male_count']} active male(s) already in cage)."
    )


def pending_breeding_cages_queryset():
    """Active breeding-purpose cages that do not yet have an active breeding record."""
    active_breeding_cage_ids = (
        Breeding.objects.filter(active=True)
        .exclude(status=Breeding.Status.CLOSED)
        .exclude(cage_id__isnull=True)
        .values_list("cage_id", flat=True)
    )
    return (
        Cage.objects.filter(purpose=Cage.Purpose.BREEDING, status=Cage.Status.ACTIVE)
        .exclude(pk__in=active_breeding_cage_ids)
        .prefetch_related("current_mice")
        .order_by("cage_id")
    )


def enrich_pending_breeding_cage(cage: Cage) -> None:
    status = breeding_setup_status(cage)
    cage.pending_setup = True
    cage.pending_ready = status["ready"]
    cage.pending_males = status["males"]
    cage.pending_females = status["females"]
    cage.pending_status_label = "Ready to create" if status["ready"] else "Awaiting breeders"
    if status["missing_sire"] and status["missing_dam"]:
        cage.pending_hint = "Need active sire and dam"
    elif status["missing_sire"]:
        cage.pending_hint = "Need active sire"
    elif status["missing_dam"]:
        cage.pending_hint = "Need active dam"
    else:
        cage.pending_hint = ""


def sync_cage_breeding_workflow(cage: Cage | None) -> Breeding | None:
    """Apply breeding-purpose side effects for a cage."""
    if cage is None:
        return None
    if cage.purpose == Cage.Purpose.BREEDING:
        mark_cage_as_breeding(cage)
        return ensure_breeding_for_cage(cage)
    return None


def sync_breeding_member_cages(breeding: Breeding | None) -> int:
    """Move all breeding members into the breeding cage so colony views stay consistent."""
    if breeding is None or not breeding.cage_id:
        return 0
    moved = 0
    for mouse in breeding.member_mice():
        if mouse.current_cage_id == breeding.cage_id:
            continue
        mouse.current_cage_id = breeding.cage_id
        mouse.save(update_fields=["current_cage", "updated_at"])
        moved += 1
    return moved


def sync_cages_after_mouse_change(
    *,
    current_cage_id: int | None,
    previous_cage_id: int | None = None,
) -> None:
    for cage_id in {cid for cid in (current_cage_id, previous_cage_id) if cid}:
        cage = Cage.objects.filter(pk=cage_id).first()
        if cage is None:
            continue
        sync_cage_status_from_mice(cage)
        sync_cage_breeding_workflow(cage)
