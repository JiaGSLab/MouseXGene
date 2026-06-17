"""Helpers for creating workflow cages without leaving the current form."""

from __future__ import annotations

from datetime import date

from django.db import IntegrityError, transaction
from django.utils import timezone

from colony.id_uniqueness import normalize_identifier
from colony.models import Cage, Colony, Mouse, StrainLine
from core.models import Project

AUTO_CAGE_RETRY_LIMIT = 20


def generate_auto_cage_id(prefix: str, when: date | None = None) -> str:
    day = (when or timezone.localdate()).strftime("%Y%m%d")
    base = f"{prefix}-{day}"
    for n in range(1, 10000):
        candidate = f"{base}-{n:03d}"
        if not Cage.objects.filter(cage_id__iexact=candidate).exists():
            return candidate
    raise IntegrityError(f"Could not allocate an auto cage ID for {base}.")


def validate_requested_auto_cage_id(cage_id: str) -> str:
    normalized = normalize_identifier(cage_id)
    if not normalized:
        return ""
    if Cage.objects.filter(cage_id__iexact=normalized).exists():
        raise ValueError(f'Cage ID "{normalized}" already exists. Leave it blank for an automatic ID.')
    return normalized


def infer_project_for_breeding_cage(mice: list[Mouse], *, preferred: Mouse | None = None) -> Project | None:
    if preferred is not None and preferred.project_id:
        return preferred.project
    projects = {mouse.project_id: mouse.project for mouse in mice if mouse and mouse.project_id}
    if len(projects) == 1:
        return next(iter(projects.values()))
    return None


def infer_shared_colony(mice: list[Mouse], *, project: Project | None = None) -> Colony | None:
    colonies = {mouse.colony_id: mouse.colony for mouse in mice if mouse and mouse.colony_id}
    if len(colonies) != 1:
        return None
    colony = next(iter(colonies.values()))
    if project is not None and colony.project_id != project.pk:
        return None
    return colony


def infer_source_cage(mice: list[Mouse], *, preferred: Mouse | None = None) -> Cage | None:
    if preferred is not None and preferred.current_cage_id:
        return preferred.current_cage
    for mouse in mice:
        if mouse and mouse.current_cage_id:
            return mouse.current_cage
    return None


def colony_for_project_and_strain(project: Project | None, strain_line: StrainLine | None) -> Colony | None:
    if project is None or strain_line is None:
        return None
    return Colony.get_or_create_for(project_id=project.pk, strain_line_id=strain_line.pk)


def create_auto_cage(
    *,
    prefix: str,
    cage_type: str,
    purpose: str,
    created_date: date | None = None,
    requested_cage_id: str = "",
    project: Project | None = None,
    colony: Colony | None = None,
    source_cage: Cage | None = None,
    notes: str = "",
) -> Cage:
    normalized_requested_id = validate_requested_auto_cage_id(requested_cage_id)
    for _attempt in range(AUTO_CAGE_RETRY_LIMIT):
        cage_id = normalized_requested_id or generate_auto_cage_id(prefix, when=created_date)
        try:
            with transaction.atomic():
                return Cage.objects.create(
                    cage_id=cage_id,
                    created_date=created_date or timezone.localdate(),
                    room=source_cage.room if source_cage else "",
                    rack=source_cage.rack if source_cage else "",
                    position="",
                    cage_type=cage_type,
                    purpose=purpose,
                    status=Cage.Status.ACTIVE,
                    project=project,
                    colony=colony,
                    notes=notes,
                )
        except IntegrityError:
            if normalized_requested_id:
                raise
    raise IntegrityError(f"Could not create an auto cage with prefix {prefix}.")
