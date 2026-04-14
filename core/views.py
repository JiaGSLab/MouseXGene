from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.db.models import Q
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta

from colony.models import Cage, Mouse
from breeding.models import Breeding, Litter
from .models import AuditLog
from users.permissions import authenticated_required, role_required, can_view_audit


@authenticated_required
def home(request: HttpRequest) -> HttpResponse:
    today = timezone.localdate()
    wean_due_end = today + timedelta(days=3)

    mice_without_cage_qs = Mouse.objects.filter(current_cage__isnull=True).select_related(
        "strain_line", "project"
    )
    mice_without_genotype_qs = (
        Mouse.objects.filter(genotypes__isnull=True)
        .select_related("strain_line", "project")
        .distinct()
    )
    cages_without_mice_qs = (
        Cage.objects.filter(status=Cage.Status.ACTIVE, current_mice__isnull=True)
        .order_by("cage_id")
        .distinct()
    )
    active_mice_qs = Mouse.objects.filter(status=Mouse.Status.ACTIVE)
    mice_without_cage_qs = mice_without_cage_qs.filter(status=Mouse.Status.ACTIVE)
    mice_without_genotype_qs = mice_without_genotype_qs.filter(status=Mouse.Status.ACTIVE)

    weaning_due_soon_qs = Litter.objects.filter(
        birth_date__isnull=False,
        wean_date__isnull=True,
        birth_date__range=(today - timedelta(days=21), wean_due_end - timedelta(days=21)),
    ).select_related("breeding")

    breeding_without_litter_qs = (
        Breeding.objects.filter(Q(active=True) | Q(status=Breeding.Status.PLUGGED), litters__isnull=True)
        .select_related("cage", "male", "female_1")
        .distinct()
    )

    context = {
        "total_cages": Cage.objects.count(),
        "active_cages": Cage.objects.filter(status=Cage.Status.ACTIVE).count(),
        "total_mice": Mouse.objects.count(),
        "active_mice": active_mice_qs.count(),
        "mice_without_cage_count": mice_without_cage_qs.count(),
        "mice_without_genotype_count": mice_without_genotype_qs.count(),
        "cages_without_mice_count": cages_without_mice_qs.count(),
        "weaning_due_soon_count": weaning_due_soon_qs.count(),
        "breeding_without_litter_count": breeding_without_litter_qs.count(),
        "mice_without_cage": mice_without_cage_qs.order_by("mouse_uid")[:8],
        "mice_without_genotype": mice_without_genotype_qs.order_by("mouse_uid")[:8],
        "cages_without_mice": cages_without_mice_qs[:8],
        "weaning_due_soon": weaning_due_soon_qs.order_by("birth_date")[:8],
        "breeding_without_litter": breeding_without_litter_qs.order_by("-start_date")[:8],
        "recent_mice": Mouse.objects.select_related("strain_line", "current_cage").order_by("-created_at")[:8],
        "recent_cages": Cage.objects.order_by("-created_at")[:8],
    }
    return render(request, "core/home.html", context)


@role_required(can_view_audit)
def audit_log_list(request: HttpRequest) -> HttpResponse:
    action = (request.GET.get("action") or "").strip()
    object_type = (request.GET.get("object_type") or "").strip()
    user_id = (request.GET.get("user") or "").strip()

    logs = AuditLog.objects.select_related("user").all()
    if action:
        logs = logs.filter(action=action)
    if object_type:
        logs = logs.filter(object_type=object_type)
    if user_id:
        logs = logs.filter(user_id=user_id)

    context = {
        "logs": logs.order_by("-created_at")[:200],
        "action": action,
        "object_type": object_type,
        "user_id": user_id,
        "action_options": AuditLog.Action.choices,
        "object_type_options": AuditLog.objects.values_list("object_type", flat=True).distinct().order_by("object_type"),
        "user_options": get_user_model().objects.order_by("username"),
    }
    return render(request, "core/audit_list.html", context)
