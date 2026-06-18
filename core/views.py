from django.contrib import messages
from django.core.cache import cache
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.db.models import Count, Max, Q
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta

from colony.models import Cage, Colony, Mouse, StrainLine
from breeding.models import Breeding, Litter
from breeding.models import LitterPup
from breeding.analytics import breeding_litter_timing_alert
from breeding.consistency import active_breeding_cage_mismatches
from core.audit import log_audit_event
from core.list_sort import PROJECT_LIST_SORT, apply_list_sort, build_list_sort_context
from core.history import audit_entries_for_object, merge_actor_labels, summarize_modelform_changes
from core.owner_filters import (
    breeding_project_owner_filter_q,
    litter_project_owner_filter_q,
    project_owner_filter_options,
    resolve_project_owner_filter,
)
from .forms import ProjectForm, ProjectMembershipFormSet
from .models import AuditLog
from .models import Project, ProjectMembership, format_project_owner_label
from users.permissions import (
    authenticated_required,
    can_create_project,
    can_manage_project_settings,
    can_view_audit,
    is_admin,
    role_required,
)


DASHBOARD_STATS_CACHE_TIMEOUT = 30


def _dashboard_stats_cache_key(user, owner: str) -> str:
    user_key = getattr(user, "pk", None) or "anon"
    owner_key = owner or "all"
    return f"dashboard-stats:v2:user:{user_key}:owner:{owner_key}"


@authenticated_required
def home(request: HttpRequest) -> HttpResponse:
    today = timezone.localdate()
    wean_due_end = today + timedelta(days=3)

    mice_queryset = Mouse.objects.all()
    cages_queryset = Cage.objects.all() if request.user.is_authenticated else Cage.objects.none()
    breedings_queryset = Breeding.objects.all()
    litters_queryset = Litter.objects.all()

    home_owner = ""
    if request.user.is_authenticated and not is_admin(request.user):
        home_owner = resolve_project_owner_filter(request)
        if home_owner:
            mice_queryset = mice_queryset.filter(project__owner_id=home_owner)
            breedings_queryset = breedings_queryset.filter(
                breeding_project_owner_filter_q(home_owner)
            ).distinct()
            litters_queryset = litters_queryset.filter(
                litter_project_owner_filter_q(home_owner)
            ).distinct()
            cages_queryset = cages_queryset.filter(
                Q(current_mice__isnull=True) | Q(current_mice__project__owner_id=home_owner)
            ).distinct()

    mice_without_cage_qs = mice_queryset.filter(current_cage__isnull=True).select_related(
        "strain_line", "project"
    )
    mice_without_genotype_qs = (
        mice_queryset.filter(genotype_components__isnull=True)
        .select_related("strain_line", "project", "current_cage")
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
    pups_lacking_genotype_qs = (
        LitterPup.objects.select_related("litter", "mouse")
        .filter(tail_tag_date__isnull=False, mouse__isnull=False)
        .filter(mouse__genotypes__isnull=True)
        .filter(litter__in=litters_queryset)
        .order_by("-tail_tag_date")
        .distinct()
    )
    empty_cutoff = timezone.now() - timedelta(days=14)
    empty_active_cages_long_qs = (
        cages_queryset.filter(status=Cage.Status.ACTIVE, current_mice__isnull=True)
        .annotate(last_mouse_left=Max("memberships__end_date"))
        .filter(
            Q(last_mouse_left__lt=empty_cutoff.date())
            | Q(last_mouse_left__isnull=True, created_at__lt=empty_cutoff)
        )
        .order_by("last_mouse_left", "created_at")
        .distinct()
    )

    dashboard_list_limit = 8
    stats_cache_key = _dashboard_stats_cache_key(request.user, home_owner)
    cached_stats = cache.get(stats_cache_key)
    if cached_stats is None:
        cached_stats = {
            "total_cages": cages_queryset.count(),
            "active_cages": cages_queryset.filter(status=Cage.Status.ACTIVE).count(),
            "total_mice": mice_queryset.count(),
            "active_mice": active_mice_qs.count(),
            "mice_without_cage_count": mice_without_cage_qs.count(),
            "mice_without_genotype_count": mice_without_genotype_qs.count(),
            "cages_without_mice_count": cages_without_mice_qs.count(),
            "weaning_due_soon_count": weaning_due_soon_qs.count(),
            "breeding_without_litter_count": breeding_without_litter_qs.count(),
            "pups_lacking_genotype_count": pups_lacking_genotype_qs.count(),
            "empty_active_cages_long_count": empty_active_cages_long_qs.count(),
        }
        cache.set(stats_cache_key, cached_stats, DASHBOARD_STATS_CACHE_TIMEOUT)
    total_cages = cached_stats["total_cages"]
    active_cages = cached_stats["active_cages"]
    total_mice = cached_stats["total_mice"]
    active_mice = cached_stats["active_mice"]
    mice_without_cage_count = cached_stats["mice_without_cage_count"]
    mice_without_genotype_count = cached_stats["mice_without_genotype_count"]
    cages_without_mice_count = cached_stats["cages_without_mice_count"]
    weaning_due_soon_count = cached_stats["weaning_due_soon_count"]
    breeding_without_litter_count = cached_stats["breeding_without_litter_count"]
    pups_lacking_genotype_count = cached_stats["pups_lacking_genotype_count"]
    empty_active_cages_long_count = cached_stats["empty_active_cages_long_count"]
    breeding_overdue_cutoff = today - timedelta(days=22)
    breeding_overdue_qs = (
        breedings_queryset.filter(Q(active=True) & ~Q(status=Breeding.Status.CLOSED))
        .select_related("cage", "male", "female_1")
        .annotate(litter_count=Count("litters", distinct=True), latest_litter_date=Max("litters__birth_date"))
        .filter(
            Q(litter_count=0, start_date__lte=breeding_overdue_cutoff)
            | Q(litter_count__gt=0, latest_litter_date__lte=breeding_overdue_cutoff)
        )
        .order_by("-start_date")[:200]
    )
    breeding_overdue_all: list[Breeding] = []
    for b in breeding_overdue_qs:
        alert = breeding_litter_timing_alert(
            start_date=b.start_date,
            latest_litter_date=b.latest_litter_date,
            litter_count=b.litter_count or 0,
            is_active=b.active,
            status=b.status,
            today=today,
        )
        if alert:
            b.litter_timing_alert = alert
            breeding_overdue_all.append(b)
    breeding_overdue_count = len(breeding_overdue_all)
    breeding_cage_mismatch_all = active_breeding_cage_mismatches(breedings_queryset)
    breeding_cage_mismatch_count = len(breeding_cage_mismatch_all)

    weaning_due_soon = list(weaning_due_soon_qs.order_by("birth_date")[:dashboard_list_limit])
    mice_without_cage = list(mice_without_cage_qs.order_by("mouse_uid")[:dashboard_list_limit])
    mice_without_genotype = list(mice_without_genotype_qs.order_by("mouse_uid")[:dashboard_list_limit])
    cages_without_mice = list(cages_without_mice_qs[:dashboard_list_limit])
    breeding_without_litter = list(breeding_without_litter_qs.order_by("-start_date")[:dashboard_list_limit])
    breeding_overdue = breeding_overdue_all[:dashboard_list_limit]
    breeding_cage_mismatches = breeding_cage_mismatch_all[:dashboard_list_limit]
    pups_lacking_genotype = list(pups_lacking_genotype_qs[:dashboard_list_limit])
    empty_active_cages_long = list(empty_active_cages_long_qs[:dashboard_list_limit])

    dashboard_alerts = [
        {
            "kind": "weaning",
            "title": "Weaning Due Soon",
            "list_url": "litters:litter_list",
            "count": weaning_due_soon_count,
            "items": weaning_due_soon,
        },
        {
            "kind": "mice_no_cage",
            "title": "Mice Without Current Cage",
            "list_url": "mice:mouse_list",
            "count": mice_without_cage_count,
            "items": mice_without_cage,
        },
        {
            "kind": "mice_no_genotype",
            "title": "Mice Without Genotype Records",
            "list_url": "mice:mouse_list",
            "count": mice_without_genotype_count,
            "items": mice_without_genotype,
        },
        {
            "kind": "cages_no_mice",
            "title": "Cages With No Current Mice",
            "list_url": "colony:cage_list",
            "count": cages_without_mice_count,
            "items": cages_without_mice,
        },
        {
            "kind": "breeding_no_litter",
            "title": "Active/Plugged Breedings Without Litters",
            "list_url": "breeding:breeding_list",
            "count": breeding_without_litter_count,
            "items": breeding_without_litter,
        },
        {
            "kind": "breeding_overdue",
            "title": "Breeding Overdue / Review Pair",
            "list_url": "breeding:breeding_list",
            "count": breeding_overdue_count,
            "items": breeding_overdue,
        },
        {
            "kind": "breeding_cage_mismatch",
            "title": "Breeding Cage Mismatch",
            "list_url": "breeding:breeding_list",
            "count": breeding_cage_mismatch_count,
            "items": breeding_cage_mismatches,
        },
        {
            "kind": "pups_no_genotype",
            "title": "Tail-tagged Pups Missing Genotype",
            "list_url": "litters:litter_list",
            "count": pups_lacking_genotype_count,
            "items": pups_lacking_genotype,
        },
        {
            "kind": "empty_cages_long",
            "title": "Active Cages Empty >14 Days",
            "list_url": "colony:cage_list",
            "count": empty_active_cages_long_count,
            "items": empty_active_cages_long,
        },
    ]
    # Keep a stable workflow-first layout: cage-related alerts first, then mouse, then breeding/litter.
    alert_order = {
        "cages_no_mice": 10,
        "empty_cages_long": 20,
        "mice_no_cage": 30,
        "mice_no_genotype": 40,
        "weaning": 50,
        "pups_no_genotype": 60,
        "breeding_cage_mismatch": 65,
        "breeding_no_litter": 70,
        "breeding_overdue": 80,
    }
    dashboard_alerts.sort(key=lambda a: alert_order.get(a["kind"], 999))

    inactive_cages = total_cages - active_cages
    inactive_mice = total_mice - active_mice

    context = {
        "dashboard_list_limit": dashboard_list_limit,
        "total_cages": total_cages,
        "active_cages": active_cages,
        "total_mice": total_mice,
        "active_mice": active_mice,
        "inactive_cages": inactive_cages,
        "inactive_mice": inactive_mice,
        "mice_without_cage_count": mice_without_cage_count,
        "mice_without_genotype_count": mice_without_genotype_count,
        "cages_without_mice_count": cages_without_mice_count,
        "weaning_due_soon_count": weaning_due_soon_count,
        "breeding_without_litter_count": breeding_without_litter_count,
        "pups_lacking_genotype_count": pups_lacking_genotype_count,
        "empty_active_cages_long_count": empty_active_cages_long_count,
        "breeding_cage_mismatch_count": breeding_cage_mismatch_count,
        "mice_without_cage": mice_without_cage,
        "mice_without_genotype": mice_without_genotype,
        "cages_without_mice": cages_without_mice,
        "weaning_due_soon": weaning_due_soon,
        "breeding_without_litter": breeding_without_litter,
        "breeding_cage_mismatches": breeding_cage_mismatches,
        "pups_lacking_genotype": pups_lacking_genotype,
        "empty_active_cages_long": empty_active_cages_long,
        "dashboard_alerts": dashboard_alerts,
        "recent_mice": mice_queryset.select_related("strain_line", "current_cage").order_by("-created_at")[
            :dashboard_list_limit
        ],
        "recent_cages": cages_queryset.order_by("-created_at")[:dashboard_list_limit],
        "home_owner": home_owner,
        "owner_options": project_owner_filter_options(),
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


def _enrich_projects_for_list(projects, user) -> None:
    project_rows = list(projects)
    if not project_rows:
        return
    memberships_by_project: dict[int, list[str]] = {}
    for membership in (
        ProjectMembership.objects.filter(project_id__in=[project.pk for project in project_rows])
        .select_related("user", "user__profile")
        .order_by("project_id", "user__username")
    ):
        label = (format_project_owner_label(membership.user) or membership.user.get_username() or "").strip()
        memberships_by_project.setdefault(membership.project_id, []).append(label or membership.user.get_username())
    for project in project_rows:
        labels = memberships_by_project.get(project.pk, [])
        project.member_labels = labels
        project.members_display = ", ".join(labels) if labels else "—"
        project.can_manage = can_manage_project_settings(user, project)


@authenticated_required
def project_list(request: HttpRequest) -> HttpResponse:
    q = (request.GET.get("q") or "").strip()
    projects = Project.objects.select_related("owner", "owner__profile").all()
    if q:
        projects = projects.filter(
            Q(name__icontains=q)
            | Q(owner_name__icontains=q)
            | Q(owner__username__icontains=q)
            | Q(owner__first_name__icontains=q)
            | Q(owner__last_name__icontains=q)
            | Q(owner__profile__display_name__icontains=q)
            | Q(description__icontains=q)
        )
    projects = apply_list_sort(projects, request, PROJECT_LIST_SORT)
    active_projects = list(projects.filter(is_active=True))
    inactive_projects = list(projects.filter(is_active=False))
    _enrich_projects_for_list(active_projects, request.user)
    _enrich_projects_for_list(inactive_projects, request.user)
    context = {
        "active_projects": active_projects,
        "inactive_projects": inactive_projects,
        "q": q,
        **build_list_sort_context(request, "project_list", PROJECT_LIST_SORT),
    }
    return render(request, "core/project_list.html", context)


@authenticated_required
def project_detail(request: HttpRequest, pk: int) -> HttpResponse:
    project = get_object_or_404(
        Project.objects.select_related(
            "owner",
            "owner__profile",
            "created_by",
            "created_by__profile",
            "updated_by",
            "updated_by__profile",
        ),
        pk=pk,
    )
    memberships = list(project.memberships.select_related("user", "user__profile").order_by("user__username"))
    can_manage = is_admin(request.user) or can_manage_project_settings(request.user, project)
    strain_lines = list(
        StrainLine.objects.filter(Q(projects=project) | Q(mice__project=project))
        .select_related("owner", "owner__profile", "created_by", "created_by__profile")
        .annotate(
            project_mice_count=Count("mice", filter=Q(mice__project=project), distinct=True),
            project_active_mice_count=Count(
                "mice",
                filter=Q(mice__project=project, mice__status=Mouse.Status.ACTIVE),
                distinct=True,
            ),
        )
        .distinct()
        .order_by("name", "line_name")
    )
    colonies = list(
        Colony.objects.filter(project=project)
        .select_related("strain_line")
        .annotate(
            active_mice_count=Count("mice", filter=Q(mice__status=Mouse.Status.ACTIVE), distinct=True),
            total_mice_count=Count("mice", distinct=True),
            cage_count=Count("cages", distinct=True),
        )
        .order_by("strain_line__line_name", "name")
    )
    audit_entries = audit_entries_for_object("Project", project.pk)
    actors = merge_actor_labels(project, audit_entries)
    return render(
        request,
        "core/project_detail.html",
        {
            "project": project,
            "memberships": memberships,
            "strain_lines": strain_lines,
            "colonies": colonies,
            "can_manage": can_manage,
            "audit_entries": audit_entries,
            **actors,
        },
    )


@authenticated_required
def project_create(request: HttpRequest) -> HttpResponse:
    if not can_create_project(request.user):
        raise PermissionDenied("Only lab admins or lab managers can create projects.")
    if request.method == "POST":
        form = ProjectForm(request.POST)
        if form.is_valid():
            project = form.save(commit=False)
            if not project.owner_id:
                project.owner = request.user
            project.save()
            # Non-admin creator becomes manager of new project.
            if not is_admin(request.user):
                project.memberships.get_or_create(
                    user=request.user,
                    defaults={"role": ProjectMembership.Role.MANAGER},
                )
            log_audit_event(
                user=request.user,
                action=AuditLog.Action.CREATE,
                obj=project,
                message=f"Created project {project.name}.",
            )
            return redirect("project_detail", pk=project.pk)
    else:
        form = ProjectForm(initial={"owner": request.user})
    return render(
        request,
        "core/project_form.html",
        {"form": form, "page_title": "Create Project", "submit_label": "Save Project"},
    )


@authenticated_required
def project_edit(request: HttpRequest, pk: int) -> HttpResponse:
    project = get_object_or_404(Project, pk=pk)
    if not (is_admin(request.user) or can_manage_project_settings(request.user, project)):
        raise PermissionDenied("You cannot edit this project.")
    if request.method == "POST":
        form = ProjectForm(request.POST, instance=project)
        if form.is_valid():
            msg = summarize_modelform_changes(form)
            form.save()
            log_audit_event(
                user=request.user,
                action=AuditLog.Action.UPDATE,
                obj=project,
                message=msg[:4000],
            )
            return redirect("project_detail", pk=project.pk)
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
    if not (is_admin(request.user) or can_manage_project_settings(request.user, project)):
        raise PermissionDenied("You cannot manage membership for this project.")
    if request.method == "POST":
        formset = ProjectMembershipFormSet(request.POST, instance=project)
        if formset.is_valid():
            formset.save()
            log_audit_event(
                user=request.user,
                action=AuditLog.Action.UPDATE,
                obj=project,
                message=f"Updated memberships for project {project.name}.",
            )
            return redirect("project_detail", pk=project.pk)
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


def permission_denied(request: HttpRequest, exception: PermissionDenied) -> HttpResponse:
    """Show a friendly flash message instead of a bare 403 page."""
    message = str(exception) or "You do not have permission to perform this action."
    if request.user.is_authenticated:
        messages.error(request, message)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            referer = request.META.get("HTTP_REFERER", "").strip()
            if referer and referer != request.build_absolute_uri():
                if url_has_allowed_host_and_scheme(
                    referer,
                    allowed_hosts={request.get_host()},
                    require_https=request.is_secure(),
                ):
                    return redirect(referer)
            return redirect(reverse("home"))
        return render(request, "403.html", {"message": message}, status=403)
    return redirect(reverse("login") + f"?next={request.path}")
