"""JSON picker endpoints for lazy-loaded form widgets."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.http import HttpRequest, JsonResponse
from django.utils import timezone

from core.models import Project, format_project_owner_label
from users.permissions import authenticated_required

from .cage_form_helpers import filter_active_cage_choices_payload
from .models import Mouse, StrainLine


PICKER_MOUSE_LIMIT = 400
PICKER_CAGE_LIMIT = 400


def _parse_int_param(value: str | None) -> int | None:
    raw = (value or "").strip()
    if not raw.isdigit():
        return None
    return int(raw)


@authenticated_required
def mouse_picker_api(request: HttpRequest) -> JsonResponse:
    owner_id = _parse_int_param(request.GET.get("owner_id") or request.GET.get("owner"))
    project_id = _parse_int_param(request.GET.get("project_id") or request.GET.get("project"))
    strain_line_id = _parse_int_param(request.GET.get("strain_line_id") or request.GET.get("strain_line"))
    q = (request.GET.get("q") or "").strip()
    sex = (request.GET.get("sex") or "").strip().upper()
    exclude_breeding_id = _parse_int_param(request.GET.get("exclude_breeding_id"))

    mice = Mouse.objects.select_related(
        "project",
        "project__owner",
        "project__owner__profile",
        "strain_line",
    ).order_by("mouse_uid")
    if owner_id:
        mice = mice.filter(project__owner_id=owner_id)
    if project_id:
        mice = mice.filter(project_id=project_id)
    if strain_line_id:
        mice = mice.filter(strain_line_id=strain_line_id)
    if sex in {Mouse.Sex.MALE, Mouse.Sex.FEMALE}:
        mice = mice.filter(sex=sex)
    if q:
        mice = mice.filter(
            Q(mouse_uid__icontains=q)
            | Q(ear_tag__icontains=q)
            | Q(toe_tag__icontains=q)
        )
    mice = list(mice[:PICKER_MOUSE_LIMIT])
    mouse_ids = [m.pk for m in mice]

    from breeding.views import _active_breeding_codes_for_mouse_ids

    active_codes_map = _active_breeding_codes_for_mouse_ids(
        mouse_ids,
        exclude_breeding_id=exclude_breeding_id,
    )

    today = timezone.localdate()
    payload = []
    for m in mice:
        age_days = (today - m.birth_date).days if m.birth_date else None
        payload.append(
            {
                "id": m.pk,
                "uid": m.mouse_uid,
                "sex": m.sex,
                "project_id": m.project_id,
                "project_name": m.project.name if m.project_id else "",
                "project_owner_id": m.project.owner_id if m.project_id else None,
                "project_owner_name": (
                    format_project_owner_label(m.project.owner) if m.project_id and m.project.owner_id else ""
                ),
                "strain_line_id": m.strain_line_id,
                "strain_line_name": m.strain_line.line_name if m.strain_line_id else "",
                "status": m.status,
                "status_label": m.get_status_display(),
                "age_days": age_days,
                "genotype_summary": m.genotype_summary or "",
                "active_breeding_codes": active_codes_map.get(m.pk, []),
            }
        )
    return JsonResponse({"mice": payload, "truncated": len(payload) >= PICKER_MOUSE_LIMIT})


@authenticated_required
def cage_picker_api(request: HttpRequest) -> JsonResponse:
    project_id = _parse_int_param(request.GET.get("project_id") or request.GET.get("project"))
    owner_id = _parse_int_param(request.GET.get("owner_id") or request.GET.get("owner"))
    strain_line_id = _parse_int_param(request.GET.get("strain_line_id") or request.GET.get("strain_line"))
    q = (request.GET.get("q") or "").strip()
    cages = filter_active_cage_choices_payload(
        project_id=project_id,
        owner_id=owner_id,
        strain_line_id=strain_line_id,
        q=q,
        limit=PICKER_CAGE_LIMIT,
    )
    return JsonResponse({"cages": cages, "truncated": len(cages) >= PICKER_CAGE_LIMIT})


@authenticated_required
def mouse_strain_line_map_api(request: HttpRequest) -> JsonResponse:
    """Active mice id -> strain_line_id for genotype template JS (bounded payload)."""
    rows = Mouse.objects.filter(strain_line_id__isnull=False).values_list("pk", "strain_line_id")[:5000]
    return JsonResponse({str(pk): str(strain_id) for pk, strain_id in rows})
