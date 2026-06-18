"""Keep cage status and breeding records aligned with colony workflow."""

from __future__ import annotations

import logging
from datetime import date

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from breeding.models import Breeding, BreedingExtraFemale

from .models import Cage, CageMembership, Mouse

logger = logging.getLogger(__name__)
BREEDING_CODE_RETRY_LIMIT = 5
TERMINAL_MOUSE_STATUSES = frozenset(
    {
        Mouse.Status.ARCHIVED,
        Mouse.Status.DEAD,
        Mouse.Status.CULLED,
        Mouse.Status.TRANSFERRED,
        Mouse.Status.EUTHANIZED,
    }
)


def _generate_breeding_code() -> str:
    prefix = timezone.localdate().strftime("BR-%Y%m%d")
    n = 1
    while True:
        candidate = f"{prefix}-{n:03d}"
        if not Breeding.objects.filter(breeding_code=candidate).exists():
            return candidate
        n += 1


def _create_breeding_with_code_retry(
    *,
    cage: Cage,
    breeding_type: str,
    sire: Mouse,
    dams: list[Mouse],
) -> Breeding:
    last_error: IntegrityError | None = None
    for _attempt in range(BREEDING_CODE_RETRY_LIMIT):
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
        try:
            with transaction.atomic():
                breeding.full_clean(validate_unique=False)
                breeding.save()
            return breeding
        except IntegrityError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise IntegrityError("Failed to allocate a breeding code.")


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


def sync_cage_after_occupancy_change(cage: Cage | None) -> bool:
    """Close an active cage once it has no active current occupants."""
    if cage is None or cage.status != Cage.Status.ACTIVE:
        return False
    if cage.current_mice.filter(status=Mouse.Status.ACTIVE).exists():
        return False
    cage.status = Cage.Status.CLOSED
    cage.save(update_fields=["status", "updated_at"])
    return True


def _terminal_mouse_exit_date(mouse: Mouse, fallback: date | None = None) -> date:
    return mouse.euthanasia_date or mouse.death_date or fallback or timezone.localdate()


def _sync_cage_after_terminal_mouse_exit(cage: Cage | None) -> bool:
    if cage is None:
        return False
    if sync_cage_status_from_mice(cage):
        return True
    return sync_cage_after_occupancy_change(cage)


def _active_breeding_q_for_mouse(mouse: Mouse) -> Q:
    return (
        Q(male_id=mouse.pk)
        | Q(female_1_id=mouse.pk)
        | Q(female_2_id=mouse.pk)
        | Q(extra_female_links__mouse_id=mouse.pk)
        | Q(breeding_members__mouse_id=mouse.pk)
    )


def close_active_breedings_for_terminal_mouse(
    mouse: Mouse,
    *,
    end_date: date | None = None,
    reason: str = "",
    exclude_breeding_id: int | None = None,
) -> tuple[str, ...]:
    """Close active breeding records that still reference a terminal mouse."""
    if mouse.status not in TERMINAL_MOUSE_STATUSES:
        return ()
    candidate_qs = (
        Breeding.objects
        .filter(active=True)
        .exclude(status=Breeding.Status.CLOSED)
        .filter(_active_breeding_q_for_mouse(mouse))
        .distinct()
    )
    if exclude_breeding_id:
        candidate_qs = candidate_qs.exclude(pk=exclude_breeding_id)
    breeding_ids = list(candidate_qs.values_list("pk", flat=True))
    closed_codes: list[str] = []
    affected_cages: list[Cage] = []
    with transaction.atomic():
        locked_breedings = (
            Breeding.objects.select_for_update()
            .filter(pk__in=breeding_ids)
            .select_related("cage")
            .order_by("breeding_code")
        )
        for breeding in locked_breedings:
            breeding.status = Breeding.Status.CLOSED
            breeding.active = False
            if not breeding.archived_at:
                breeding.archived_at = timezone.now()
            breeding.save(update_fields=["status", "active", "archived_at"])
            closed_codes.append(breeding.breeding_code)
            if breeding.cage_id:
                affected_cages.append(breeding.cage)
        for cage in affected_cages:
            sync_cage_after_occupancy_change(cage)
    return tuple(closed_codes)


def cage_allows_mixed_active_sexes(cage: Cage | None) -> bool:
    if cage is None:
        return True
    return cage.purpose == Cage.Purpose.BREEDING or cage.cage_type == Cage.CageType.BREEDING


def active_mixed_sex_cage_error(
    cage: Cage | None,
    incoming_sexes,
    *,
    exclude_mouse_ids=None,
) -> str:
    """Return a user-facing error if a non-breeding cage would mix active male and female mice."""
    if cage is None or cage_allows_mixed_active_sexes(cage):
        return ""
    sexes = {sex for sex in incoming_sexes if sex in {Mouse.Sex.MALE, Mouse.Sex.FEMALE}}
    exclude_mouse_ids = [pk for pk in (exclude_mouse_ids or []) if pk]
    existing_qs = Mouse.objects.filter(current_cage=cage, status=Mouse.Status.ACTIVE)
    if exclude_mouse_ids:
        existing_qs = existing_qs.exclude(pk__in=exclude_mouse_ids)
    sexes.update(
        sex
        for sex in existing_qs.values_list("sex", flat=True)
        if sex in {Mouse.Sex.MALE, Mouse.Sex.FEMALE}
    )
    if Mouse.Sex.MALE in sexes and Mouse.Sex.FEMALE in sexes:
        return (
            f"Cage {cage.cage_id} is not a breeding cage. Active male and female mice "
            "cannot be housed together there. Use a breeding cage or choose separate same-sex cages."
        )
    return ""


def validate_active_sex_compatible_with_cage(
    cage: Cage | None,
    incoming_sexes,
    *,
    exclude_mouse_ids=None,
) -> None:
    error = active_mixed_sex_cage_error(
        cage,
        incoming_sexes,
        exclude_mouse_ids=exclude_mouse_ids,
    )
    if error:
        raise ValidationError(error)


def remove_terminal_mouse_from_current_cage(
    mouse: Mouse,
    *,
    exit_date: date | None = None,
    reason: str = "",
) -> tuple[str, ...]:
    """
    Terminal mice should keep historical cage memberships but stop occupying a current cage.

    Returns the affected cage IDs for user-facing messages and audit logs.
    """
    if mouse.status not in TERMINAL_MOUSE_STATUSES:
        return ()

    affected_cages: dict[int, Cage] = {}
    with transaction.atomic():
        locked_mouse = Mouse.objects.select_for_update().get(pk=mouse.pk)
        if locked_mouse.status not in TERMINAL_MOUSE_STATUSES:
            return ()

        current_cage = None
        if locked_mouse.current_cage_id:
            current_cage = Cage.objects.filter(pk=locked_mouse.current_cage_id).first()
            if current_cage is not None:
                affected_cages[current_cage.pk] = current_cage

        current_memberships = list(
            CageMembership.objects.select_for_update()
            .filter(mouse=locked_mouse, is_current=True)
            .select_related("cage")
        )
        for membership in current_memberships:
            affected_cages[membership.cage_id] = membership.cage

        if not affected_cages and not current_memberships:
            return ()

        resolved_exit_date = _terminal_mouse_exit_date(locked_mouse, fallback=exit_date)
        membership_reason = (reason or f"Mouse status changed to {locked_mouse.get_status_display()}.")[:128]
        current_membership_cage_ids = {membership.cage_id for membership in current_memberships}
        for membership in current_memberships:
            membership_end_date = resolved_exit_date
            if membership.start_date and membership_end_date < membership.start_date:
                membership_end_date = membership.start_date
            membership.end_date = membership_end_date
            membership.is_current = False
            membership.reason = membership_reason
            membership.save(update_fields=["end_date", "is_current", "reason", "updated_at"])

        if (
            current_cage is not None
            and locked_mouse.current_cage_id not in current_membership_cage_ids
            and not CageMembership.objects.filter(mouse=locked_mouse, cage=current_cage).exists()
        ):
            CageMembership.objects.create(
                mouse=locked_mouse,
                cage=current_cage,
                start_date=resolved_exit_date,
                end_date=resolved_exit_date,
                is_current=False,
                reason=membership_reason,
            )

        if locked_mouse.current_cage_id:
            locked_mouse.current_cage = None
            locked_mouse.save(update_fields=["current_cage", "updated_at"])
            mouse.current_cage = None
            mouse.current_cage_id = None

        for cage in affected_cages.values():
            _sync_cage_after_terminal_mouse_exit(cage)

    return tuple(sorted(cage.cage_id for cage in affected_cages.values()))


def _membership_end_date(start_date: date | None, fallback: date) -> date:
    if start_date and fallback < start_date:
        return start_date
    return fallback


def reconcile_mouse_cage_membership(
    mouse: Mouse,
    *,
    repair_date: date | None = None,
    reason: str = "Admin cage history reconciliation.",
    apply: bool = True,
) -> dict:
    """
    Align CageMembership current rows with Mouse.current_cage.

    This is intentionally a reconciliation helper, not a replacement for normal
    workflows such as Move Cage, End Mouse, Restore Mouse, breeding end, or wean.
    It repairs historical/import/admin-edit drift while preserving existing rows.
    """
    resolved_date = repair_date or timezone.localdate()
    membership_reason = (reason or "Admin cage history reconciliation.")[:128]
    result = {
        "mouse_uid": mouse.mouse_uid,
        "status": mouse.status,
        "target_cage": "",
        "closed_membership_cages": [],
        "created_membership": False,
        "terminal_cleanup": False,
        "changed": False,
    }

    def inspect_and_optionally_apply(locked_mouse: Mouse) -> dict:
        current_memberships = list(
            CageMembership.objects.filter(mouse=locked_mouse, is_current=True)
            .select_related("cage")
            .order_by("-start_date", "-created_at", "-pk")
        )
        result["mouse_uid"] = locked_mouse.mouse_uid
        result["status"] = locked_mouse.status
        result["target_cage"] = locked_mouse.current_cage.cage_id if locked_mouse.current_cage_id else ""

        if locked_mouse.status in TERMINAL_MOUSE_STATUSES:
            if locked_mouse.current_cage_id or current_memberships:
                result["terminal_cleanup"] = True
                result["closed_membership_cages"] = [
                    membership.cage.cage_id for membership in current_memberships
                ]
                result["changed"] = True
                if apply:
                    remove_terminal_mouse_from_current_cage(
                        locked_mouse,
                        exit_date=resolved_date,
                        reason=membership_reason,
                    )
            return result

        if not locked_mouse.current_cage_id:
            to_close = current_memberships
            result["closed_membership_cages"] = [membership.cage.cage_id for membership in to_close]
            result["changed"] = bool(to_close)
            if apply:
                for membership in to_close:
                    membership.end_date = _membership_end_date(membership.start_date, resolved_date)
                    membership.is_current = False
                    membership.reason = membership_reason
                    membership.save(update_fields=["end_date", "is_current", "reason", "updated_at"])
            return result

        matching = [
            membership for membership in current_memberships if membership.cage_id == locked_mouse.current_cage_id
        ]
        stale = [
            membership for membership in current_memberships if membership.cage_id != locked_mouse.current_cage_id
        ]
        duplicate_matching = matching[1:]
        to_close = stale + duplicate_matching
        result["closed_membership_cages"] = [membership.cage.cage_id for membership in to_close]
        result["created_membership"] = not matching
        result["changed"] = bool(to_close) or not matching

        if apply:
            for membership in to_close:
                membership.end_date = _membership_end_date(membership.start_date, resolved_date)
                membership.is_current = False
                membership.reason = membership_reason
                membership.save(update_fields=["end_date", "is_current", "reason", "updated_at"])
            if not matching:
                CageMembership.objects.create(
                    mouse=locked_mouse,
                    cage=locked_mouse.current_cage,
                    start_date=resolved_date,
                    end_date=None,
                    is_current=True,
                    reason=membership_reason,
                )
        return result

    if apply:
        with transaction.atomic():
            locked = Mouse.objects.select_for_update(of=("self",)).get(pk=mouse.pk)
            return inspect_and_optionally_apply(locked)

    inspected = Mouse.objects.select_related("current_cage").get(pk=mouse.pk)
    return inspect_and_optionally_apply(inspected)


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
    dams = females
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
            breeding = _create_breeding_with_code_retry(
                cage=cage,
                breeding_type=breeding_type,
                sire=sire,
                dams=dams,
            )
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
    move_date = breeding.start_date or timezone.localdate()
    with transaction.atomic():
        breeding_cage = Cage.objects.select_for_update().get(pk=breeding.cage_id)
        for member in breeding.member_mice():
            mouse = Mouse.objects.select_for_update().get(pk=member.pk)
            if mouse.status != Mouse.Status.ACTIVE:
                remove_terminal_mouse_from_current_cage(
                    mouse,
                    reason=f"Terminal mouse skipped during breeding cage sync: {breeding.breeding_code}",
                )
                continue
            if mouse.current_cage_id == breeding_cage.pk:
                if not CageMembership.objects.filter(mouse=mouse, cage=breeding_cage, is_current=True).exists():
                    for membership in (
                        CageMembership.objects.select_for_update()
                        .filter(mouse=mouse, is_current=True)
                        .exclude(cage=breeding_cage)
                    ):
                        membership_end_date = move_date
                        if membership.start_date and membership_end_date < membership.start_date:
                            membership_end_date = membership.start_date
                        membership.end_date = membership_end_date
                        membership.is_current = False
                        membership.reason = f"Moved to breeding cage: {breeding.breeding_code}"[:128]
                        membership.save(
                            update_fields=["end_date", "is_current", "reason", "updated_at"]
                        )
                    CageMembership.objects.create(
                        mouse=mouse,
                        cage=breeding_cage,
                        start_date=move_date,
                        is_current=True,
                        reason=f"Breeding setup: {breeding.breeding_code}"[:128],
                    )
                continue

            current_memberships = list(
                CageMembership.objects.select_for_update().filter(mouse=mouse, is_current=True)
            )
            for membership in current_memberships:
                membership_end_date = move_date
                if membership.start_date and membership_end_date < membership.start_date:
                    membership_end_date = membership.start_date
                membership.end_date = membership_end_date
                membership.is_current = False
                membership.reason = f"Moved to breeding cage: {breeding.breeding_code}"[:128]
                membership.save(update_fields=["end_date", "is_current", "reason", "updated_at"])

            mouse.current_cage = breeding_cage
            mouse.save(update_fields=["current_cage", "updated_at"])
            CageMembership.objects.create(
                mouse=mouse,
                cage=breeding_cage,
                start_date=move_date,
                end_date=None,
                is_current=True,
                reason=f"Breeding setup: {breeding.breeding_code}"[:128],
            )
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
