"""Shared cage picker payloads for mouse and breeding forms."""

from __future__ import annotations

from django.db.models import Q

from core.models import Project
from core.owner_filters import project_owner_wean_options

from .models import Cage, StrainLine


def active_cage_choices_payload() -> list[dict]:
    return filter_active_cage_choices_payload(limit=2000)


def filter_active_cage_choices_payload(
    *,
    project_id: int | None = None,
    owner_id: int | None = None,
    strain_line_id: int | None = None,
    q: str = "",
    limit: int = 400,
) -> list[dict]:
    cages = (
        Cage.objects.filter(status=Cage.Status.ACTIVE)
        .prefetch_related(
            "current_mice__project",
            "current_mice__project__owner",
            "current_mice__strain_line",
        )
        .order_by("cage_id")
    )
    if q:
        cages = cages.filter(cage_id__icontains=q)
    payload: list[dict] = []
    scan_limit = max(limit * 5, limit)
    for cage in cages[:scan_limit]:
        mice = list(cage.current_mice.all())
        project_ids = sorted({m.project_id for m in mice if m.project_id})
        owner_ids = sorted(
            {m.project.owner_id for m in mice if getattr(m, "project_id", None) and m.project.owner_id}
        )
        strain_line_ids = sorted({m.strain_line_id for m in mice if m.strain_line_id})
        if mice:
            if project_id and project_id not in project_ids:
                continue
            if owner_id and owner_id not in owner_ids:
                continue
            if strain_line_id and strain_line_id not in strain_line_ids:
                continue
        payload.append(
            {
                "id": cage.pk,
                "cage_id": cage.cage_id,
                "is_empty": not mice,
                "project_ids": project_ids,
                "owner_ids": owner_ids,
                "strain_line_ids": strain_line_ids,
            }
        )
        if len(payload) >= limit:
            break
    return payload


def cage_filter_form_context() -> dict:
    projects = list(Project.objects.filter(is_active=True).order_by("name").values("id", "name"))
    owners = project_owner_wean_options()
    strain_lines = list(
        StrainLine.objects.filter(is_active=True).order_by("line_name").values("id", "line_name")
    )
    return {
        "breeding_cage_choices": [],
        "cage_project_options": projects,
        "cage_owner_options": owners,
        "cage_strain_line_options": strain_lines,
        "cage_picker_api_url": "/cages/api/picker/",
        "mouse_picker_api_url": "/mice/api/picker/",
        "mouse_strain_map_api_url": "/mice/api/strain-line-map/",
    }
