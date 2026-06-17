"""Shared cage picker payloads for mouse and breeding forms."""

from __future__ import annotations

from django.db.models import Exists, OuterRef, Q

from core.models import Project
from core.owner_filters import project_owner_wean_options

from .models import Cage, Mouse, StrainLine


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
    mice_for_cage = Mouse.objects.filter(current_cage_id=OuterRef("pk"))
    cages = Cage.objects.filter(status=Cage.Status.ACTIVE).annotate(
        _has_current_mice=Exists(mice_for_cage),
    )
    if q:
        cages = cages.filter(cage_id__icontains=q)
    if project_id:
        cages = cages.annotate(
            _matches_project=Exists(mice_for_cage.filter(project_id=project_id)),
        ).filter(Q(project_id=project_id) | Q(_has_current_mice=False, project_id__isnull=True) | Q(_matches_project=True))
    if owner_id:
        cages = cages.annotate(
            _matches_owner=Exists(mice_for_cage.filter(project__owner_id=owner_id)),
        ).filter(
            Q(project__owner_id=owner_id)
            | Q(_has_current_mice=False, project_id__isnull=True)
            | Q(_matches_owner=True)
        )
    if strain_line_id:
        cages = cages.annotate(
            _matches_strain=Exists(mice_for_cage.filter(strain_line_id=strain_line_id)),
        ).filter(Q(colony__strain_line_id=strain_line_id) | Q(_has_current_mice=False) | Q(_matches_strain=True))
    cages = cages.select_related(
        "project",
        "project__owner",
        "project__owner__profile",
        "colony",
        "colony__strain_line",
    ).prefetch_related(
        "current_mice__project",
        "current_mice__project__owner",
        "current_mice__strain_line",
    ).order_by("cage_id")
    payload: list[dict] = []
    for cage in cages[:limit]:
        mice = list(cage.current_mice.all())
        project_pairs = sorted(
            {
                (m.project_id, m.project.name)
                for m in mice
                if m.project_id and getattr(m, "project", None)
            }
            | ({(cage.project_id, cage.project.name)} if cage.project_id and cage.project else set()),
            key=lambda item: item[1].lower(),
        )
        strain_pairs = sorted(
            {
                (m.strain_line_id, m.strain_line.line_name)
                for m in mice
                if m.strain_line_id and getattr(m, "strain_line", None)
            }
            | (
                {(cage.colony.strain_line_id, cage.colony.strain_line.line_name)}
                if cage.colony_id and cage.colony and cage.colony.strain_line_id
                else set()
            ),
            key=lambda item: item[1].lower(),
        )
        sex_values = sorted({m.sex for m in mice if m.sex})
        sex_counts = {
            sex: sum(1 for m in mice if m.sex == sex)
            for sex in sex_values
        }
        project_ids = [pid for pid, _name in project_pairs]
        owner_ids = sorted(
            {m.project.owner_id for m in mice if getattr(m, "project_id", None) and m.project.owner_id}
            | ({cage.project.owner_id} if cage.project_id and cage.project.owner_id else set())
        )
        strain_line_ids = [sid for sid, _name in strain_pairs]
        payload.append(
            {
                "id": cage.pk,
                "cage_id": cage.cage_id,
                "purpose": cage.purpose,
                "purpose_label": cage.get_purpose_display(),
                "cage_type": cage.cage_type,
                "cage_type_label": cage.get_cage_type_display(),
                "home_project_id": cage.project_id,
                "home_project_name": cage.project.name if cage.project_id else "",
                "colony_id": cage.colony_id,
                "colony_name": cage.colony.name if cage.colony_id else "",
                "is_empty": not mice,
                "mouse_count": len(mice),
                "mouse_uids": [m.mouse_uid for m in mice[:8]],
                "sexes": sex_values,
                "sex_counts": sex_counts,
                "project_ids": project_ids,
                "project_names": [name for _pid, name in project_pairs],
                "owner_ids": owner_ids,
                "strain_line_ids": strain_line_ids,
                "strain_line_names": [name for _sid, name in strain_pairs],
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
        "mouse_uid_check_api_url": "/mice/api/uid-check/",
    }
