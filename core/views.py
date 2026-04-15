from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta

from colony.models import Cage, Mouse
from breeding.models import Breeding, Litter
from .forms import ProjectForm, ProjectMembershipFormSet
from .models import AuditLog
from .models import Project
from users.permissions import (
    authenticated_required,
    can_import,
    can_view_audit,
    is_admin,
    is_project_manager,
    role_required,
)


@authenticated_required
def home(request: HttpRequest) -> HttpResponse:
    today = timezone.localdate()
    wean_due_end = today + timedelta(days=3)

    mice_queryset = Mouse.objects.all()
    cages_queryset = Cage.objects.all()
    breedings_queryset = Breeding.objects.all()
    litters_queryset = Litter.objects.all()

    mice_without_cage_qs = mice_queryset.filter(current_cage__isnull=True).select_related(
        "strain_line", "project"
    )
    mice_without_genotype_qs = (
        mice_queryset.filter(genotypes__isnull=True)
        .select_related("strain_line", "project")
        .distinct()
    )
    cages_without_mice_qs = (
        cages_queryset.filter(status=Cage.Status.ACTIVE, current_mice__isnull=True)
        .order_by("cage_id")
        .distinct()
    )
    active_mice_qs = mice_queryset.filter(status=Mouse.Status.ACTIVE)
    mice_without_cage_qs = mice_without_cage_qs.filter(status=Mouse.Status.ACTIVE)
    mice_without_genotype_qs = mice_without_genotype_qs.filter(status=Mouse.Status.ACTIVE)

    weaning_due_soon_qs = litters_queryset.filter(
        birth_date__isnull=False,
        wean_date__isnull=True,
        birth_date__range=(today - timedelta(days=21), wean_due_end - timedelta(days=21)),
    ).select_related("breeding")

    breeding_without_litter_qs = (
        breedings_queryset.filter(Q(active=True) | Q(status=Breeding.Status.PLUGGED), litters__isnull=True)
        .select_related("cage", "male", "female_1")
        .distinct()
    )

    context = {
        "total_cages": cages_queryset.count(),
        "active_cages": cages_queryset.filter(status=Cage.Status.ACTIVE).count(),
        "total_mice": mice_queryset.count(),
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
        "recent_mice": mice_queryset.select_related("strain_line", "current_cage").order_by("-created_at")[:8],
        "recent_cages": cages_queryset.order_by("-created_at")[:8],
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


@authenticated_required
def project_list(request: HttpRequest) -> HttpResponse:
    q = (request.GET.get("q") or "").strip()
    projects = Project.objects.all()
    if q:
        projects = projects.filter(Q(name__icontains=q) | Q(owner_name__icontains=q) | Q(description__icontains=q))
    context = {
        "projects": projects.order_by("name"),
        "q": q,
    }
    return render(request, "core/project_list.html", context)


@authenticated_required
def project_create(request: HttpRequest) -> HttpResponse:
    if not (is_admin(request.user) or can_import(request.user)):
        raise PermissionDenied("Only admin or project managers can create projects.")
    if request.method == "POST":
        form = ProjectForm(request.POST)
        if form.is_valid():
            project = form.save()
            # Non-admin creator becomes manager of new project.
            if not is_admin(request.user):
                project.memberships.get_or_create(user=request.user, defaults={"role": "manager"})
            return redirect("project_list")
    else:
        form = ProjectForm()
    return render(
        request,
        "core/project_form.html",
        {"form": form, "page_title": "Create Project", "submit_label": "Save Project"},
    )


@authenticated_required
def project_edit(request: HttpRequest, pk: int) -> HttpResponse:
    project = get_object_or_404(Project, pk=pk)
    if not (is_admin(request.user) or is_project_manager(request.user, project)):
        raise PermissionDenied("You cannot edit this project.")
    if request.method == "POST":
        form = ProjectForm(request.POST, instance=project)
        if form.is_valid():
            form.save()
            return redirect("project_list")
    else:
        form = ProjectForm(instance=project)
    return render(
        request,
        "core/project_form.html",
        {"form": form, "page_title": f"Edit Project {project.name}", "submit_label": "Save Changes"},
    )


@authenticated_required
def project_membership_manage(request: HttpRequest, pk: int) -> HttpResponse:
    project = get_object_or_404(Project, pk=pk)
    if not (is_admin(request.user) or is_project_manager(request.user, project)):
        raise PermissionDenied("You cannot manage membership for this project.")
    if request.method == "POST":
        formset = ProjectMembershipFormSet(request.POST, instance=project)
        if formset.is_valid():
            formset.save()
            return redirect("project_list")
    else:
        formset = ProjectMembershipFormSet(instance=project)
    return render(
        request,
        "core/project_membership_form.html",
        {"project": project, "formset": formset},
    )


@authenticated_required
def guide(request: HttpRequest) -> HttpResponse:
    return render(request, "core/guide.html")
