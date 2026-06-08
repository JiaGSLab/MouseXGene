"""Shared project-owner filters for colony list views."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.http import HttpRequest

from core.models import format_project_owner_label
from users.permissions import is_admin

OWNER_FILTER_ALL = "all"


def resolve_project_owner_filter(request: HttpRequest) -> str:
    """Return project owner user pk as string, or '' for all owners."""
    if "owner" in request.GET or "owner_id" in request.GET:
        owner = (request.GET.get("owner") or request.GET.get("owner_id") or "").strip()
        if owner == OWNER_FILTER_ALL:
            return ""
        return owner
    if (request.GET.get("strain_line") or request.GET.get("strain_line_id") or "").strip():
        return ""
    if getattr(request.user, "is_authenticated", False):
        if is_admin(request.user):
            return ""
        return str(request.user.pk)
    return ""


def project_owner_filter_options():
    from colony.models import Mouse

    owner_ids = (
        Mouse.objects.exclude(project__owner_id__isnull=True)
        .values_list("project__owner_id", flat=True)
        .distinct()
    )
    return [
        {
            "pk": user.pk,
            "label": (format_project_owner_label(user) or user.get_username() or "").strip() or str(user.pk),
        }
        for user in get_user_model().objects.filter(pk__in=owner_ids).order_by("username")
    ]


def breeding_project_owner_filter_q(owner_id: str | int) -> Q:
    return (
        Q(male__project__owner_id=owner_id)
        | Q(female_1__project__owner_id=owner_id)
        | Q(female_2__project__owner_id=owner_id)
        | Q(extra_female_links__mouse__project__owner_id=owner_id)
    )


def litter_project_owner_filter_q(owner_id: str | int) -> Q:
    return (
        Q(breeding__male__project__owner_id=owner_id)
        | Q(breeding__female_1__project__owner_id=owner_id)
        | Q(breeding__female_2__project__owner_id=owner_id)
        | Q(breeding__extra_female_links__mouse__project__owner_id=owner_id)
    )
