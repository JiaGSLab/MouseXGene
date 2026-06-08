import csv
import logging
from datetime import timedelta
from io import BytesIO

from django.contrib.auth import get_user_model
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib import messages
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.core.exceptions import PermissionDenied
from django.db import OperationalError, ProgrammingError, transaction
from django.db.models import Count, Max, Q
from django.urls import reverse
from django.utils import timezone
from openpyxl import Workbook

from colony.cage_lifecycle import enrich_pending_breeding_cage, mark_cage_as_breeding, pending_breeding_cages_queryset, sync_breeding_member_cages
from colony.models import Cage, CageMembership, Mouse
from colony.mouse_age import TIER_HINT, tier_map_for_breeding_select_mice

from .forms import BreedingForm, LitterForm, LitterPupFormSet, WeanLitterForm, get_pup_formset
from .models import Breeding, Litter, LitterPup
from .analytics import breeding_litter_timing_alert, mendelian_single_locus_review_for_breeding
from core.audit import log_audit_event
from core.list_sort import BREEDING_LIST_SORT, LITTER_LIST_SORT, apply_list_sort, build_list_sort_context
from core.history import audit_entries_for_object, merge_actor_labels
from core.models import AuditLog, Project, ProjectMembership, format_project_owner_label
from users.permissions import (
    authenticated_required,
    ensure_can_edit_mice_projects,
    ensure_can_edit_project_data,
)

logger = logging.getLogger(__name__)

LIST_PAGE_SIZES = (25, 50, 100)
LIST_PAGE_DEFAULT = 25
LIST_ALL_RESULTS_MAX = 500


def _mouse_age_days(mouse: Mouse | None, *, today=None) -> int | None:
    if mouse is None or not mouse.birth_date:
        return None
    local_today = today or timezone.localdate()
    return max((local_today - mouse.birth_date).days, 0)


def _pagination_hrefs(request: HttpRequest, page_obj, viewname: str) -> dict[str, str | None]:
    def href(n: int) -> str:
        q = request.GET.copy()
        q.pop("export", None)
        if n <= 1:
            q.pop("page", None)
        else:
            q["page"] = str(n)
        qs = q.urlencode()
        base = reverse(viewname)
        return f"{base}?{qs}" if qs else base

    np = page_obj.paginator.num_pages
    hrefs: dict[str, str | None] = {
        "first": href(1),
        "last": href(np) if np else href(1),
    }
    hrefs["prev"] = href(page_obj.previous_page_number()) if page_obj.has_previous() else None
    hrefs["next"] = href(page_obj.next_page_number()) if page_obj.has_next() else None
    return hrefs


def _paginate_queryset_for_list(request: HttpRequest, queryset, *, viewname: str) -> dict:
    total = queryset.count()
    raw_per = (request.GET.get("per_page") or "").strip().lower()

    use_all = raw_per == "all" and total <= LIST_ALL_RESULTS_MAX
    if raw_per == "all" and total > LIST_ALL_RESULTS_MAX:
        messages.warning(
            request,
            (
                f"Cannot show all {total} rows at once (limit is {LIST_ALL_RESULTS_MAX}). "
                f"Using {LIST_PAGE_DEFAULT} per page - narrow filters or use export."
            ),
        )
        use_all = False

    if use_all:
        return {
            "page_obj": None,
            "paginator": None,
            "pagination_hrefs": None,
            "per_page": "all",
            "total_count": total,
            "all_allowed": True,
            "items": list(queryset),
        }

    try:
        per_int = int(raw_per) if raw_per and raw_per != "all" else LIST_PAGE_DEFAULT
    except ValueError:
        per_int = LIST_PAGE_DEFAULT
    if per_int not in LIST_PAGE_SIZES:
        per_int = LIST_PAGE_DEFAULT

    paginator = Paginator(queryset, per_int)
    raw_page = request.GET.get("page") or "1"
    try:
        pnum = int(raw_page)
    except ValueError:
        pnum = 1
    try:
        page_obj = paginator.page(pnum)
    except EmptyPage:
        page_obj = paginator.page(max(1, paginator.num_pages))
    except PageNotAnInteger:
        page_obj = paginator.page(1)

    return {
        "page_obj": page_obj,
        "paginator": paginator,
        "pagination_hrefs": _pagination_hrefs(request, page_obj, viewname),
        "per_page": str(per_int),
        "total_count": total,
        "all_allowed": total <= LIST_ALL_RESULTS_MAX,
        "items": page_obj.object_list,
    }


def _scoped_breedings(user):
    return Breeding.objects.select_related(
        "cage",
        "male",
        "male__project",
        "female_1",
        "female_1__project",
        "female_2",
        "female_2__project",
        "created_by",
        "created_by__profile",
        "updated_by",
        "updated_by__profile",
    ).prefetch_related("extra_female_links__mouse")


def _breeding_setup_by_label(breeding: Breeding, audit_entries: list | None = None) -> str:
    if breeding.created_by_id:
        label = (format_project_owner_label(breeding.created_by) or "").strip()
        if label:
            return label
    if audit_entries is not None:
        actors = merge_actor_labels(breeding, audit_entries)
        created = (actors.get("created_by") or "").strip()
        if created and created != "—":
            return created
    return "—"


def _breeding_setup_by_filter_options():
    User = get_user_model()
    creator_ids = set(
        Breeding.objects.filter(created_by_id__isnull=False).values_list("created_by_id", flat=True).distinct()
    )
    audit_creator_ids = AuditLog.objects.filter(
        object_type="Breeding",
        action=AuditLog.Action.CREATE,
        user_id__isnull=False,
    ).values_list("user_id", flat=True).distinct()
    creator_ids.update(audit_creator_ids)
    return list(User.objects.filter(pk__in=creator_ids).select_related("profile").order_by("username"))


def _breeding_alert_display_styles(level: str) -> dict[str, str]:
    """Inline styles so list alerts stay colored even when static CSS is stale."""
    palettes = {
        "warning": ("#fffbeb", "#f59e0b", "#fef3c7", "#92400e", "#b45309"),
        "overdue": ("#fff7ed", "#ea580c", "#ffedd5", "#9a3412", "#c2410c"),
        "review": ("#fef2f2", "#dc2626", "#fee2e2", "#991b1b", "#b91c1c"),
    }
    row_bg, border, badge_bg, badge_fg, days = palettes.get(level, palettes["warning"])
    return {
        "row_style": f"background-color: {row_bg};",
        "cell_style": f"border-left: 4px solid {border}; background-color: {row_bg}; color: #1f2937;",
        "badge_style": (
            "display: inline-block; padding: 0.25rem 0.5rem; border-radius: 4px; "
            f"font-size: 0.72rem; font-weight: 700; line-height: 1.25; text-transform: uppercase; "
            f"letter-spacing: 0.02em; background: {badge_bg}; color: {badge_fg}; border: 1px solid {border};"
        ),
        "days_style": f"margin-top: 0.15rem; font-size: 0.75rem; font-weight: 600; color: {days};",
    }


def _batch_audit_entries_by_breeding_pk(pks: list[int]) -> dict[int, list[AuditLog]]:
    if not pks:
        return {}
    str_ids = [str(pk) for pk in pks]
    logs = AuditLog.objects.filter(object_type="Breeding", object_id__in=str_ids).select_related(
        "user",
        "user__profile",
    ).order_by("-created_at")
    out: dict[int, list[AuditLog]] = {pk: [] for pk in pks}
    for log in logs:
        try:
            pk = int(log.object_id)
        except (TypeError, ValueError):
            continue
        if pk in out:
            out[pk].append(log)
    return out


def _enrich_breedings_for_list(breedings: list[Breeding], *, today) -> None:
    """Attach list-row display fields (alerts, setup-by, sire/dam summaries)."""
    audit_map = _batch_audit_entries_by_breeding_pk([b.pk for b in breedings if b.pk])
    for b in breedings:
        b.display_expected_birth_date = b.expected_birth_date or (
            b.start_date + timedelta(days=21) if b.start_date else None
        )
        alert = breeding_litter_timing_alert(
            start_date=b.start_date,
            latest_litter_date=getattr(b, "latest_litter_date", None),
            litter_count=getattr(b, "litter_count", None) or 0,
            is_active=b.active,
            status=b.status,
            today=today,
        )
        b.litter_timing_alert = alert
        b.list_alert_level = ""
        b.alert_row_style = ""
        b.alert_cell_style = ""
        b.alert_badge_style = ""
        b.alert_days_style = ""
        if alert:
            b.list_alert_level = alert["level"]
            alert_styles = _breeding_alert_display_styles(alert["level"])
            b.alert_row_style = alert_styles["row_style"]
            b.alert_cell_style = alert_styles["cell_style"]
            b.alert_badge_style = alert_styles["badge_style"]
            b.alert_days_style = alert_styles["days_style"]
        b.display_sire, b.display_dams = _breeding_sire_and_dams(b)
        b.display_sire_age_days = _mouse_age_days(b.display_sire, today=today)
        b.display_sire_genotype = _mouse_genotype_summary(b.display_sire)
        b.display_dam_rows = [
            {
                "mouse": dam,
                "age_days": _mouse_age_days(dam, today=today),
                "genotype": _mouse_genotype_summary(dam),
            }
            for dam in (b.display_dams or [])
        ]
        b.setup_by_display = _breeding_setup_by_label(b, audit_map.get(b.pk, []))


def _breeding_member_mice(breeding: Breeding) -> list[Mouse]:
    try:
        members = [row.mouse for row in breeding.breeding_members.select_related("mouse").all()]
    except (ProgrammingError, OperationalError):
        members = []
    if members:
        return members
    fallback = [breeding.male, breeding.female_1, breeding.female_2, *[r.mouse for r in breeding.extra_female_links.all()]]
    return [m for m in fallback if m is not None]


def _breeding_sire_and_dams(breeding: Breeding) -> tuple[Mouse | None, list[Mouse]]:
    sire: Mouse | None = None
    dams: list[Mouse] = []
    try:
        members = list(
            breeding.breeding_members.select_related("mouse").all().order_by("role", "sort_order", "mouse__mouse_uid")
        )
    except (ProgrammingError, OperationalError):
        members = []
    if members:
        for row in members:
            if row.role == Breeding.MemberRole.SIRE and sire is None:
                sire = row.mouse
            elif row.role == Breeding.MemberRole.DAM:
                dams.append(row.mouse)
        return sire, dams
    sire = breeding.male
    if breeding.female_1:
        dams.append(breeding.female_1)
    if breeding.female_2:
        dams.append(breeding.female_2)
    for row in breeding.extra_female_links.select_related("mouse").all().order_by("mouse__mouse_uid"):
        if row.mouse not in dams:
            dams.append(row.mouse)
    return sire, dams


def _active_breeding_codes_for_mouse_ids(mouse_ids: list[int], *, exclude_breeding_id: int | None = None) -> dict[int, list[str]]:
    out: dict[int, set[str]] = {mid: set() for mid in mouse_ids}
    if not mouse_ids:
        return {}
    q = Breeding.objects.filter(active=True)
    if exclude_breeding_id:
        q = q.exclude(pk=exclude_breeding_id)
    for breeding in q.prefetch_related("extra_female_links__mouse"):
        code = breeding.breeding_code
        for mouse in _breeding_member_mice(breeding):
            if mouse.id in out:
                out[mouse.id].add(code)
    return {k: sorted(v) for k, v in out.items()}


def _breeder_mouse_choices_payload(*, editing_breeding_id: int | None = None) -> list[dict]:
    mice = list(
        Mouse.objects.select_related("project", "project__owner", "project__owner__profile", "strain_line")
        .order_by("mouse_uid")
        .only(
            "id",
            "mouse_uid",
            "sex",
            "status",
            "birth_date",
            "genotype_summary",
            "project_id",
            "project__name",
            "project__owner_id",
            "project__owner__username",
            "project__owner__first_name",
            "project__owner__last_name",
            "project__owner__profile__display_name",
            "strain_line_id",
            "strain_line__line_name",
        )
    )
    mouse_ids = [m.id for m in mice]
    active_codes_map = _active_breeding_codes_for_mouse_ids(mouse_ids, exclude_breeding_id=editing_breeding_id)
    tier_map = tier_map_for_breeding_select_mice()
    today = timezone.localdate()
    payload: list[dict] = []
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
                "age_tier": tier_map.get(str(m.pk), "none"),
                "active_breeding_codes": active_codes_map.get(m.pk, []),
            }
        )
    return payload


def user_can_edit_litter(user, litter: Litter) -> bool:
    try:
        ensure_can_edit_mice_projects(user, _breeding_member_mice(litter.breeding))
        return True
    except PermissionDenied:
        return False


def _mouse_genotype_summary(mouse: Mouse | None) -> str:
    if mouse is None:
        return "-"
    if mouse.genotype_summary:
        return mouse.genotype_summary
    # Fallback to legacy assay-style records when summary has not been prebuilt.
    records = list(mouse.genotypes.select_related("gene").all())
    parts: list[str] = []
    for gt in records[:3]:
        locus = gt.gene.symbol if gt.gene else (gt.locus_name or "locus")
        genotype_part = gt.zygosity_display or "/".join([p for p in [gt.allele_1, gt.allele_2] if p])
        parts.append(f"{locus}:{genotype_part}" if genotype_part else locus)
    if not parts:
        return "-"
    summary = ", ".join(parts)
    return f"{summary}..." if len(records) > 3 else summary


def _union_loci_from_strain_lines(*strain_lines) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for line in strain_lines:
        if line is None:
            continue
        for locus in line.expected_loci_list():
            text = (locus or "").strip()
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(text)
    return out


def _build_xlsx_response(filename: str, sheet_name: str, headers: list[str], rows: list[list]) -> HttpResponse:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = sheet_name
    worksheet.append(headers)
    for row in rows:
        worksheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@authenticated_required
def breeding_list(request: HttpRequest) -> HttpResponse:
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()
    breeding_type = (request.GET.get("breeding_type") or "").strip()
    cage = (request.GET.get("cage") or "").strip()
    setup_by = (request.GET.get("setup_by") or "").strip()
    include_inactive = (request.GET.get("include_inactive") or "").strip()
    export = (request.GET.get("export") or "").strip().lower()

    breedings = _scoped_breedings(request.user)
    if include_inactive != "yes":
        breedings = breedings.filter(active=True)
    if setup_by:
        audit_pks: list[int] = []
        for oid in AuditLog.objects.filter(
            object_type="Breeding",
            action=AuditLog.Action.CREATE,
            user_id=setup_by,
        ).values_list("object_id", flat=True):
            try:
                audit_pks.append(int(oid))
            except (TypeError, ValueError):
                continue
        breedings = breedings.filter(Q(created_by_id=setup_by) | Q(pk__in=audit_pks))
    if q:
        breedings = breedings.filter(
            Q(breeding_code__icontains=q)
            | Q(male__mouse_uid__icontains=q)
            | Q(female_1__mouse_uid__icontains=q)
            | Q(female_2__mouse_uid__icontains=q)
            | Q(created_by__username__icontains=q)
            | Q(created_by__first_name__icontains=q)
            | Q(created_by__last_name__icontains=q)
            | Q(created_by__profile__display_name__icontains=q)
        )
    if status:
        breedings = breedings.filter(status=status)
    if breeding_type:
        breedings = breedings.filter(breeding_type=breeding_type)
    if cage:
        breedings = breedings.filter(cage_id=cage)

    breedings = breedings.distinct().annotate(
        litter_count=Count("litters", distinct=True),
        latest_litter_date=Max("litters__birth_date"),
    )
    breedings = apply_list_sort(breedings, request, BREEDING_LIST_SORT)
    today = timezone.localdate()
    breedings_export_list = list(breedings)
    _enrich_breedings_for_list(breedings_export_list, today=today)

    if export in {"csv", "xlsx"}:
        headers = [
            "setup_by",
            "litter_alert",
            "days_without_litter",
            "breeding_code",
            "cage",
            "breeding_type",
            "sire",
            "dams",
            "start_date",
            "plug_date",
            "expected_birth_date",
            "status",
            "active",
        ]
        rows: list[list] = []
        for b in breedings_export_list:
            rows.append(
                [
                    b.setup_by_display,
                    (b.litter_timing_alert.get("label") if b.litter_timing_alert else ""),
                    (b.litter_timing_alert.get("days_without_litter") if b.litter_timing_alert else ""),
                    b.breeding_code,
                    b.cage.cage_id if b.cage else "",
                    b.get_breeding_type_display(),
                    b.display_sire.mouse_uid if b.display_sire else "",
                    ", ".join(d.mouse_uid for d in (b.display_dams or [])),
                    b.start_date,
                    b.plug_date or "",
                    b.display_expected_birth_date or "",
                    b.get_status_display(),
                    "yes" if b.active else "no",
                ]
            )
        if export == "csv":
            response = HttpResponse(content_type="text/csv")
            response["Content-Disposition"] = 'attachment; filename="breedings_export.csv"'
            writer = csv.writer(response)
            writer.writerow(headers)
            writer.writerows(rows)
            return response
        return _build_xlsx_response("breedings_export.xlsx", "Breedings", headers, rows)

    pagination = _paginate_queryset_for_list(request, breedings, viewname="breeding:breeding_list")
    breedings_page_items = list(pagination["items"])
    _enrich_breedings_for_list(breedings_page_items, today=today)

    pending_breeding_cages: list[Cage] = []
    if include_inactive != "yes" and not export:
        pending_qs = pending_breeding_cages_queryset()
        if cage:
            pending_qs = pending_qs.filter(pk=cage)
        if q:
            pending_qs = pending_qs.filter(cage_id__icontains=q)
        pending_breeding_cages = list(pending_qs)
        for pending_cage in pending_breeding_cages:
            enrich_pending_breeding_cage(pending_cage)

    context = {
        "breedings": breedings_page_items,
        "pending_breeding_cages": pending_breeding_cages,
        "q": q,
        "status": status,
        "breeding_type": breeding_type,
        "cage": cage,
        "setup_by": setup_by,
        "setup_by_options": [
            {
                "pk": user.pk,
                "label": (format_project_owner_label(user) or user.get_username() or str(user.pk)).strip(),
            }
            for user in _breeding_setup_by_filter_options()
        ],
        "include_inactive": include_inactive,
        "status_options": Breeding.Status.choices,
        "breeding_type_options": Breeding.BreedingType.choices,
        "cage_options": Breeding._meta.get_field("cage").related_model.objects.order_by("cage_id"),
        "page_obj": pagination["page_obj"],
        "paginator": pagination["paginator"],
        "pagination_hrefs": pagination["pagination_hrefs"],
        "per_page": pagination["per_page"],
        "total_count": pagination["total_count"],
        "all_allowed": pagination["all_allowed"],
        "list_all_max": LIST_ALL_RESULTS_MAX,
        **build_list_sort_context(request, "breeding:breeding_list", BREEDING_LIST_SORT),
    }
    return render(request, "breeding/breeding_list.html", context)


@authenticated_required
def breeding_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = BreedingForm(request.POST)
        try:
            if form.is_valid():
                ensure_can_edit_mice_projects(
                    request.user,
                    [
                        form.cleaned_data["male"],
                        form.cleaned_data["female_1"],
                        form.cleaned_data.get("female_2"),
                        *list(form.cleaned_data.get("extra_females") or []),
                    ],
                )
                breeding = form.save()
                mark_cage_as_breeding(breeding.cage)
                moved = sync_breeding_member_cages(breeding)
                if moved:
                    messages.info(request, f"Moved {moved} breeder(s) into cage {breeding.cage.cage_id}.")
                for warning in getattr(form, "warning_messages", []):
                    messages.warning(request, warning)
                log_audit_event(
                    user=request.user,
                    action=AuditLog.Action.CREATE,
                    obj=breeding,
                    message=f"Created breeding {breeding.breeding_code}.",
                )
                return redirect("breeding:breeding_detail", pk=breeding.pk)
        except Exception:
            logger.exception("Unexpected error during breeding create POST.")
            messages.error(
                request,
                "Failed to save breeding due to an unexpected server error. "
                "Please review member selection and try again.",
            )
    else:
        form = BreedingForm()

    context = {
        "form": form,
        "page_title": "Create Breeding",
        "submit_label": "Save Breeding",
        "cancel_url": "breeding:breeding_list",
        "breeding_age_tier_map": tier_map_for_breeding_select_mice(),
        "breeding_age_hints": TIER_HINT,
        "breeder_mouse_choices": _breeder_mouse_choices_payload(),
    }
    return render(request, "breeding/breeding_form.html", context)


@authenticated_required
def breeding_edit(request: HttpRequest, pk: int) -> HttpResponse:
    breeding = get_object_or_404(_scoped_breedings(request.user), pk=pk)
    ensure_can_edit_mice_projects(request.user, _breeding_member_mice(breeding))
    if request.method == "POST":
        form = BreedingForm(request.POST, instance=breeding)
        try:
            if form.is_valid():
                ensure_can_edit_mice_projects(
                    request.user,
                    [
                        form.cleaned_data["male"],
                        form.cleaned_data["female_1"],
                        form.cleaned_data.get("female_2"),
                        *list(form.cleaned_data.get("extra_females") or []),
                    ],
                )
                breeding = form.save()
                mark_cage_as_breeding(breeding.cage)
                moved = sync_breeding_member_cages(breeding)
                if moved:
                    messages.info(request, f"Moved {moved} breeder(s) into cage {breeding.cage.cage_id}.")
                for warning in getattr(form, "warning_messages", []):
                    messages.warning(request, warning)
                log_audit_event(
                    user=request.user,
                    action=AuditLog.Action.UPDATE,
                    obj=breeding,
                    message=f"Updated breeding {breeding.breeding_code} members/configuration.",
                )
                messages.success(request, f"Breeding {breeding.breeding_code} updated.")
                return redirect("breeding:breeding_detail", pk=breeding.pk)
        except Exception:
            logger.exception("Unexpected error during breeding edit POST. pk=%s", breeding.pk)
            messages.error(
                request,
                "Failed to update breeding due to an unexpected server error. "
                "Please review member selection and try again.",
            )
    else:
        form = BreedingForm(instance=breeding)

    context = {
        "form": form,
        "page_title": f"Edit Breeding {breeding.breeding_code}",
        "submit_label": "Save Changes",
        "cancel_url": "breeding:breeding_detail",
        "cancel_kwargs": {"pk": breeding.pk},
        "breeding_age_tier_map": tier_map_for_breeding_select_mice(),
        "breeding_age_hints": TIER_HINT,
        "breeder_mouse_choices": _breeder_mouse_choices_payload(editing_breeding_id=breeding.pk),
    }
    return render(request, "breeding/breeding_form.html", context)


@authenticated_required
def breeding_detail(request: HttpRequest, pk: int) -> HttpResponse:
    breeding = get_object_or_404(
        _scoped_breedings(request.user),
        pk=pk,
    )
    litters = breeding.litters.all()
    latest_litter_date = litters.aggregate(v=Max("birth_date")).get("v")
    litter_timing_alert = breeding_litter_timing_alert(
        start_date=breeding.start_date,
        latest_litter_date=latest_litter_date,
        litter_count=litters.count(),
        is_active=breeding.active,
        status=breeding.status,
        today=timezone.localdate(),
    )
    offspring = list(
        Mouse.objects.filter(sire=breeding.male, dam=breeding.female_1)
        .prefetch_related("genotype_components")
        .order_by("mouse_uid")
    )
    mendelian_reviews = mendelian_single_locus_review_for_breeding(breeding, offspring)
    mendelian_flag_count = sum(1 for r in mendelian_reviews if r["status"] == "review")
    expected_offspring_loci = _union_loci_from_strain_lines(
        breeding.male.strain_line,
        breeding.female_1.strain_line,
    )
    breeding_sire, breeding_dams = _breeding_sire_and_dams(breeding)
    today = timezone.localdate()
    breeder_member_rows = []
    if breeding_sire:
        breeder_member_rows.append(
            {
                "role": "Sire",
                "mouse": breeding_sire,
                "age_days": _mouse_age_days(breeding_sire, today=today),
                "genotype_summary": _mouse_genotype_summary(breeding_sire),
                "status": breeding_sire.get_status_display(),
            }
        )
    for dam in breeding_dams:
        breeder_member_rows.append(
            {
                "role": "Dam",
                "mouse": dam,
                "age_days": _mouse_age_days(dam, today=today),
                "genotype_summary": _mouse_genotype_summary(dam),
                "status": dam.get_status_display(),
            }
        )
    display_expected_birth_date = breeding.expected_birth_date or (
        breeding.start_date + timedelta(days=21) if breeding.start_date else None
    )
    breeding_audit_entries = audit_entries_for_object("Breeding", breeding.pk)
    actors = merge_actor_labels(breeding, breeding_audit_entries)
    return render(
        request,
        "breeding/breeding_detail.html",
        {
            "breeding": breeding,
            "setup_by_display": _breeding_setup_by_label(breeding, breeding_audit_entries),
            "litters": litters,
            "expected_offspring_loci": expected_offspring_loci,
            "breeding_sire": breeding_sire,
            "breeding_dams": breeding_dams,
            "litter_timing_alert": litter_timing_alert,
            "latest_litter_date": latest_litter_date,
            "mendelian_reviews": mendelian_reviews,
            "mendelian_flag_count": mendelian_flag_count,
            "breeder_member_rows": breeder_member_rows,
            "display_expected_birth_date": display_expected_birth_date,
            "audit_entries": breeding_audit_entries,
            **actors,
        },
    )


@authenticated_required
def breeding_end(request: HttpRequest, pk: int) -> HttpResponse:
    breeding = get_object_or_404(_scoped_breedings(request.user), pk=pk)
    ensure_can_edit_mice_projects(request.user, _breeding_member_mice(breeding))
    if request.method != "POST":
        return redirect("breeding:breeding_detail", pk=breeding.pk)
    if breeding.status == Breeding.Status.CLOSED and not breeding.active:
        messages.info(request, f"Breeding {breeding.breeding_code} is already closed.")
        return redirect("breeding:breeding_detail", pk=breeding.pk)

    breeding.status = Breeding.Status.CLOSED
    breeding.active = False
    if not breeding.archived_at:
        breeding.archived_at = timezone.now()
    breeding.save(update_fields=["status", "active", "archived_at"])
    log_audit_event(
        user=request.user,
        action=AuditLog.Action.UPDATE,
        obj=breeding,
        message=f"Ended breeding {breeding.breeding_code}.",
    )
    messages.success(request, f"Breeding {breeding.breeding_code} ended.")
    return redirect("breeding:breeding_detail", pk=breeding.pk)


@authenticated_required
def litter_list(request: HttpRequest) -> HttpResponse:
    q = (request.GET.get("q") or "").strip()
    weaned = (request.GET.get("weaned") or "").strip()
    breeding = (request.GET.get("breeding") or "").strip()
    birth_date_from = (request.GET.get("birth_date_from") or "").strip()
    birth_date_to = (request.GET.get("birth_date_to") or "").strip()
    include_inactive = (request.GET.get("include_inactive") or "").strip()
    litter_status = (request.GET.get("litter_status") or "").strip()
    export = (request.GET.get("export") or "").strip().lower()

    litters = (
        Litter.objects.filter(breeding__in=_scoped_breedings(request.user))
        .select_related(
            "breeding",
            "breeding__male",
            "breeding__male__strain_line",
            "breeding__female_1",
            "breeding__female_1__strain_line",
            "breeding__female_1__current_cage",
            "breeding__cage",
        )
        .annotate(
            _pup_total=Count("pups"),
            _pup_m=Count("pups", filter=Q(pups__sex=Mouse.Sex.MALE)),
            _pup_f=Count("pups", filter=Q(pups__sex=Mouse.Sex.FEMALE)),
            _max_pup_tail=Max("pups__tail_tag_date"),
            _created_mouse_count=Count("pups__mouse", filter=Q(pups__mouse__isnull=False), distinct=True),
        )
    )
    if include_inactive != "yes":
        litters = litters.exclude(
            litter_status__in=[Litter.LitterStatus.ENDED, Litter.LitterStatus.ARCHIVED],
        )
    if q:
        litters = litters.filter(
            Q(litter_code__icontains=q)
            | Q(breeding__breeding_code__icontains=q)
            | Q(breeding__male__mouse_uid__icontains=q)
            | Q(breeding__female_1__mouse_uid__icontains=q)
        )
    if weaned == "yes":
        litters = litters.filter(wean_date__isnull=False)
    elif weaned == "no":
        litters = litters.filter(wean_date__isnull=True)
    if breeding:
        litters = litters.filter(breeding_id=breeding)
    if birth_date_from:
        litters = litters.filter(birth_date__gte=birth_date_from)
    if birth_date_to:
        litters = litters.filter(birth_date__lte=birth_date_to)
    if litter_status:
        litters = litters.filter(litter_status=litter_status)

    litters_qs = apply_list_sort(litters, request, LITTER_LIST_SORT)
    litters = list(litters_qs)
    today = timezone.localdate()
    for litter in litters:
        sire_mouse, dam_mice = _breeding_sire_and_dams(litter.breeding)
        litter.sire_mouse = sire_mouse
        litter.dam_mice = dam_mice
        litter.breeder_member_rows = []
        if sire_mouse:
            litter.breeder_member_rows.append(
                {
                    "role": "Sire",
                    "mouse": sire_mouse,
                    "age_days": _mouse_age_days(sire_mouse, today=today),
                    "genotype_summary": _mouse_genotype_summary(sire_mouse),
                    "cage": sire_mouse.current_cage,
                }
            )
        for dam in dam_mice:
            litter.breeder_member_rows.append(
                {
                    "role": "Dam",
                    "mouse": dam,
                    "age_days": _mouse_age_days(dam, today=today),
                    "genotype_summary": _mouse_genotype_summary(dam),
                    "cage": dam.current_cage,
                }
            )
        litter.primary_dam = dam_mice[0] if dam_mice else litter.breeding.female_1
        litter.sire_genotype_summary = _mouse_genotype_summary(litter.sire_mouse or litter.breeding.male)
        litter.dam_genotype_summary = _mouse_genotype_summary(litter.primary_dam)
        litter.user_can_edit = user_can_edit_litter(request.user, litter)
        if litter.total_born is not None:
            litter.litter_size_display = litter.total_born
        elif litter.alive_count is not None:
            litter.litter_size_display = litter.alive_count
        elif litter._pup_total:
            litter.litter_size_display = litter._pup_total
        else:
            litter.litter_size_display = None
        if litter.male_count is not None:
            litter.males_display = litter.male_count
        elif litter._pup_total:
            litter.males_display = litter._pup_m
        else:
            litter.males_display = None
        if litter.female_count is not None:
            litter.females_display = litter.female_count
        elif litter._pup_total:
            litter.females_display = litter._pup_f
        else:
            litter.females_display = None
        litter.tail_tag_display = litter.tail_tag_date or litter._max_pup_tail
        litter.pups_count_display = litter.litter_size_display if litter.litter_size_display is not None else litter._pup_total
        litter.created_mice_count = litter._created_mouse_count or 0

        if litter.birth_date:
            litter.age_days = max((today - litter.birth_date).days, 0)
            litter.wean_due_date = litter.birth_date + timedelta(days=21)
            litter.wean_overdue_days = (
                (today - litter.wean_due_date).days if (not litter.wean_date and today > litter.wean_due_date) else 0
            )
            if litter.wean_date:
                litter.weaning_status = f"Weaned on {litter.wean_date.isoformat()}"
            elif today > litter.wean_due_date:
                litter.weaning_status = f"Overdue by {litter.wean_overdue_days}d"
            else:
                remaining = (litter.wean_due_date - today).days
                litter.weaning_status = f"Due in {remaining}d"
        else:
            litter.age_days = None
            litter.wean_due_date = None
            litter.wean_overdue_days = 0
            litter.weaning_status = "Unknown birth date"

        litter.tagging_status = "Completed" if litter.tail_tag_display else "Pending"
        litter.mice_created_status = (
            "Completed"
            if litter.pups_count_display is not None and litter.created_mice_count >= litter.pups_count_display
            else f"{litter.created_mice_count}/{litter.pups_count_display or '?'}"
        )

        litter.parent_lines = _union_loci_from_strain_lines(
            litter.sire_mouse.strain_line if litter.sire_mouse else None,
            litter.primary_dam.strain_line if litter.primary_dam else None,
        )
        litter.parent_line_names = []
        if litter.sire_mouse and litter.sire_mouse.strain_line:
            litter.parent_line_names.append(litter.sire_mouse.strain_line.name)
        if litter.primary_dam and litter.primary_dam.strain_line:
            dam_line_name = litter.primary_dam.strain_line.name
            if dam_line_name not in litter.parent_line_names:
                litter.parent_line_names.append(dam_line_name)

        alerts: list[str] = []
        if litter.wean_overdue_days > 0:
            alerts.append(f"Weaning overdue ({litter.wean_overdue_days}d)")
        if not litter.tail_tag_display:
            alerts.append("Tail-tagging pending")
        if litter.created_mice_count == 0 and (litter.pups_count_display or 0) > 0:
            alerts.append("Pups not converted to mice")
        if (
            litter.pups_count_display is not None
            and litter.created_mice_count > 0
            and litter.created_mice_count != litter.pups_count_display
        ):
            alerts.append("Pup count vs created mice mismatch")
        litter.workflow_alerts = alerts

    if export in {"csv", "xlsx"}:
        headers = [
            "litter_id",
            "breeding_code",
            "birth_date",
            "age_days",
            "pups_count",
            "wean_due",
            "weaning_status",
            "tagging_status",
            "mice_created",
            "status",
            "alerts",
        ]
        rows: list[list] = []
        for litter in litters:
            rows.append(
                [
                    litter.litter_id_display,
                    litter.breeding.breeding_code,
                    litter.birth_date or "",
                    litter.age_days if litter.age_days is not None else "",
                    litter.pups_count_display if litter.pups_count_display is not None else "",
                    litter.wean_due_date or "",
                    litter.weaning_status,
                    litter.tagging_status,
                    litter.created_mice_count,
                    litter.get_litter_status_display(),
                    "; ".join(litter.workflow_alerts),
                ]
            )
        if export == "csv":
            response = HttpResponse(content_type="text/csv")
            response["Content-Disposition"] = 'attachment; filename="litters_workflow_export.csv"'
            writer = csv.writer(response)
            writer.writerow(headers)
            writer.writerows(rows)
            return response
        return _build_xlsx_response("litters_workflow_export.xlsx", "Litters", headers, rows)

    selected_breeding = None
    if breeding:
        try:
            selected_breeding = _scoped_breedings(request.user).filter(pk=int(breeding)).first()
        except (TypeError, ValueError):
            selected_breeding = None

    pagination = _paginate_queryset_for_list(request, litters_qs, viewname="litters:litter_list")
    litters_page_items = list(pagination["items"])
    enriched_map = {l.pk: l for l in litters}
    for litter in litters_page_items:
        enriched = enriched_map.get(litter.pk)
        if enriched is None:
            continue
        litter.sire_mouse = enriched.sire_mouse
        litter.dam_mice = enriched.dam_mice
        litter.breeder_member_rows = enriched.breeder_member_rows
        litter.primary_dam = enriched.primary_dam
        litter.sire_genotype_summary = enriched.sire_genotype_summary
        litter.dam_genotype_summary = enriched.dam_genotype_summary
        litter.user_can_edit = enriched.user_can_edit
        litter.litter_size_display = enriched.litter_size_display
        litter.males_display = enriched.males_display
        litter.females_display = enriched.females_display
        litter.tail_tag_display = enriched.tail_tag_display
        litter.pups_count_display = enriched.pups_count_display
        litter.created_mice_count = enriched.created_mice_count
        litter.age_days = enriched.age_days
        litter.wean_due_date = enriched.wean_due_date
        litter.wean_overdue_days = enriched.wean_overdue_days
        litter.weaning_status = enriched.weaning_status
        litter.tagging_status = enriched.tagging_status
        litter.mice_created_status = enriched.mice_created_status
        litter.parent_lines = enriched.parent_lines
        litter.parent_line_names = enriched.parent_line_names
        litter.workflow_alerts = enriched.workflow_alerts

    context = {
        "litters": litters_page_items,
        "q": q,
        "weaned": weaned,
        "breeding": breeding,
        "birth_date_from": birth_date_from,
        "birth_date_to": birth_date_to,
        "include_inactive": include_inactive,
        "litter_status": litter_status,
        "litter_status_options": Litter.LitterStatus.choices,
        "breeding_options": _scoped_breedings(request.user).order_by("breeding_code"),
        "selected_breeding": selected_breeding,
        "page_obj": pagination["page_obj"],
        "paginator": pagination["paginator"],
        "pagination_hrefs": pagination["pagination_hrefs"],
        "per_page": pagination["per_page"],
        "total_count": pagination["total_count"],
        "all_allowed": pagination["all_allowed"],
        "list_all_max": LIST_ALL_RESULTS_MAX,
        **build_list_sort_context(request, "litters:litter_list", LITTER_LIST_SORT),
    }
    return render(request, "breeding/litter_list.html", context)


@authenticated_required
def litter_create_from_breeding(request: HttpRequest) -> HttpResponse:
    breeding_options = list(_scoped_breedings(request.user).order_by("-start_date", "breeding_code"))
    selected_breeding_id = (request.POST.get("breeding_id") or request.GET.get("breeding_id") or "").strip()
    if request.method == "POST":
        if not selected_breeding_id:
            messages.error(request, "Please select a breeding first.")
        else:
            try:
                selected_pk = int(selected_breeding_id)
            except ValueError:
                messages.error(request, "Invalid breeding selection.")
            else:
                breeding = _scoped_breedings(request.user).filter(pk=selected_pk).first()
                if breeding is None:
                    messages.error(request, "Selected breeding is not available.")
                else:
                    return redirect("breeding:litter_create", breeding_pk=breeding.pk)
    return render(
        request,
        "breeding/litter_create_from_breeding.html",
        {
            "breeding_options": breeding_options,
            "selected_breeding_id": selected_breeding_id,
        },
    )


@authenticated_required
def litter_create(request: HttpRequest, breeding_pk: int) -> HttpResponse:
    breeding = get_object_or_404(_scoped_breedings(request.user), pk=breeding_pk)
    ensure_can_edit_mice_projects(request.user, _breeding_member_mice(breeding))
    if request.method == "POST":
        form = LitterForm(request.POST)
        if form.is_valid():
            litter = form.save(commit=False)
            litter.breeding = breeding
            litter.save()
            if breeding.status != Breeding.Status.LITTERED:
                breeding.status = Breeding.Status.LITTERED
                breeding.save(update_fields=["status"])
            log_audit_event(
                user=request.user,
                action=AuditLog.Action.RECORD_LITTER,
                obj=litter,
                message=f"Recorded litter {litter.litter_code or litter.pk} for breeding {breeding.breeding_code}.",
            )
            messages.success(request, f"Litter {litter.litter_code or litter.pk} created.")
            return redirect("litters:litter_detail", pk=litter.pk)
    else:
        form = LitterForm()

    context = {
        "form": form,
        "breeding": breeding,
        "page_title": f"Record Litter for {breeding.breeding_code}",
    }
    return render(request, "breeding/litter_form.html", context)


@authenticated_required
def litter_detail(request: HttpRequest, pk: int) -> HttpResponse:
    litter = get_object_or_404(
        Litter.objects.select_related(
            "breeding",
            "breeding__male",
            "breeding__male__strain_line",
            "breeding__male__current_cage",
            "breeding__female_1",
            "breeding__female_1__strain_line",
            "breeding__female_1__current_cage",
            "breeding__cage",
        )
        .prefetch_related("pups__mouse")
        .filter(breeding__in=_scoped_breedings(request.user)),
        pk=pk,
    )
    pups = list(litter.pups.all().order_by("sort_order", "id"))
    breeding_sire, breeding_dams = _breeding_sire_and_dams(litter.breeding)
    today = timezone.localdate()
    breeder_member_rows = []
    if breeding_sire:
        breeder_member_rows.append(
            {
                "role": "Sire",
                "mouse": breeding_sire,
                "age_days": _mouse_age_days(breeding_sire, today=today),
                "genotype_summary": _mouse_genotype_summary(breeding_sire),
                "cage": breeding_sire.current_cage,
                "status": breeding_sire.get_status_display(),
            }
        )
    for dam in breeding_dams:
        breeder_member_rows.append(
            {
                "role": "Dam",
                "mouse": dam,
                "age_days": _mouse_age_days(dam, today=today),
                "genotype_summary": _mouse_genotype_summary(dam),
                "cage": dam.current_cage,
                "status": dam.get_status_display(),
            }
        )
    primary_dam = breeding_dams[0] if breeding_dams else litter.breeding.female_1
    registered_offspring = list(
        Mouse.objects.filter(
            dam_id=primary_dam.id if primary_dam else litter.breeding.female_1_id,
            sire_id=breeding_sire.id if breeding_sire else litter.breeding.male_id,
            birth_date=litter.birth_date,
        )
        .select_related("strain_line", "current_cage", "project")
        .order_by("mouse_uid")
    )
    context = {
        "litter": litter,
        "pups": pups,
        "registered_offspring": registered_offspring,
        "sire_genotype_summary": _mouse_genotype_summary(breeding_sire or litter.breeding.male),
        "dam_genotype_summary": _mouse_genotype_summary(primary_dam),
        "breeding_sire": breeding_sire,
        "breeding_dams": breeding_dams,
        "primary_dam": primary_dam,
        "breeder_member_rows": breeder_member_rows,
        "can_edit_litter": user_can_edit_litter(request.user, litter),
    }
    return render(request, "breeding/litter_detail.html", context)


@authenticated_required
def litter_edit(request: HttpRequest, pk: int) -> HttpResponse:
    litter = get_object_or_404(
        Litter.objects.select_related(
            "breeding",
            "breeding__male",
            "breeding__female_1",
            "breeding__female_2",
        ).filter(breeding__in=_scoped_breedings(request.user)),
        pk=pk,
    )
    if not user_can_edit_litter(request.user, litter):
        raise PermissionDenied("You do not have permission to edit this litter.")
    if request.method == "POST":
        form = LitterForm(request.POST, instance=litter)
        formset = LitterPupFormSet(request.POST, instance=litter)
        if form.is_valid() and formset.is_valid():
            litter = form.save(commit=False)
            if litter.litter_status in (Litter.LitterStatus.ARCHIVED, Litter.LitterStatus.ENDED):
                litter.is_archived = True
                if not litter.archived_at:
                    litter.archived_at = timezone.now()
            else:
                litter.is_archived = False
                litter.archived_at = None
            litter.save()
            formset.save()
            messages.success(request, "Litter and pup rows saved.")
            return redirect("litters:litter_detail", pk=litter.pk)
    else:
        form = LitterForm(instance=litter)
        formset = LitterPupFormSet(instance=litter)

    context = {
        "litter": litter,
        "form": form,
        "formset": formset,
        "page_title": f"Manage litter {litter.litter_id_display}",
    }
    return render(request, "breeding/litter_edit.html", context)


@authenticated_required
def litter_end(request: HttpRequest, pk: int) -> HttpResponse:
    litter = get_object_or_404(
        Litter.objects.select_related("breeding", "breeding__male", "breeding__female_1", "breeding__female_2").filter(
            breeding__in=_scoped_breedings(request.user)
        ),
        pk=pk,
    )
    if not user_can_edit_litter(request.user, litter):
        raise PermissionDenied("You do not have permission to end this litter.")
    if request.method != "POST":
        return redirect("litters:litter_detail", pk=litter.pk)
    if litter.litter_status in (Litter.LitterStatus.ENDED, Litter.LitterStatus.ARCHIVED):
        messages.info(request, "This litter is already closed.")
        return redirect("litters:litter_detail", pk=litter.pk)

    litter.litter_status = Litter.LitterStatus.ENDED
    litter.is_archived = True
    if not litter.archived_at:
        litter.archived_at = timezone.now()
    litter.save(update_fields=["litter_status", "is_archived", "archived_at"])
    log_audit_event(
        user=request.user,
        action=AuditLog.Action.UPDATE,
        obj=litter,
        message=f"Ended litter workflow for {litter.litter_id_display}.",
    )
    messages.success(request, f"Litter {litter.litter_id_display} marked as ended.")
    return redirect("litters:litter_detail", pk=litter.pk)


@authenticated_required
def litter_wean(request: HttpRequest, pk: int) -> HttpResponse:
    litter = get_object_or_404(
        Litter.objects.select_related("breeding", "breeding__male", "breeding__female_1").filter(
            breeding__in=_scoped_breedings(request.user)
        ),
        pk=pk,
    )
    breeding = litter.breeding
    breeding_sire, breeding_dams = _breeding_sire_and_dams(breeding)
    sire_project = breeding.male.project
    dam_project = breeding.female_1.project
    offspring_template_loci = _union_loci_from_strain_lines(
        breeding.male.strain_line,
        breeding.female_1.strain_line,
    )
    ensure_can_edit_mice_projects(request.user, _breeding_member_mice(breeding))
    if litter.litter_status in (Litter.LitterStatus.ENDED, Litter.LitterStatus.ARCHIVED):
        messages.error(request, "This litter is closed; you cannot wean additional pups.")
        return redirect("litters:litter_detail", pk=litter.pk)
    if request.method == "POST":
        wean_form = WeanLitterForm(request.POST, sire_project=sire_project, dam_project=dam_project)
        number_of_pups = 1
        if wean_form.is_valid():
            number_of_pups = wean_form.cleaned_data["number_of_pups"]
        else:
            try:
                number_of_pups = max(1, int(request.POST.get("number_of_pups", "1")))
            except ValueError:
                number_of_pups = 1

        PupFormSet = get_pup_formset(number_of_pups)
        pup_formset = PupFormSet(request.POST, prefix="pups")

        if "refresh_forms" in request.POST:
            return render(
                request,
                "breeding/litter_wean.html",
                {
                    "litter": litter,
                    "wean_form": wean_form,
                    "pup_formset": pup_formset,
                    "offspring_template_loci": offspring_template_loci,
                },
            )

        if wean_form.is_valid():
            wean_date = wean_form.cleaned_data["wean_date"]
            if litter.alive_count is not None and number_of_pups > litter.alive_count:
                wean_form.add_error("number_of_pups", "number_of_pups cannot exceed litter.alive_count.")

            if pup_formset.is_valid() and not wean_form.errors:
                uid_list = [form.cleaned_data["mouse_uid"] for form in pup_formset.forms]
                duplicate_in_form = {uid for uid in uid_list if uid_list.count(uid) > 1}
                if duplicate_in_form:
                    wean_form.add_error(
                        None,
                        f"Duplicate mouse_uid in form: {', '.join(sorted(duplicate_in_form))}.",
                    )

                existing_uids = set(Mouse.objects.filter(mouse_uid__in=uid_list).values_list("mouse_uid", flat=True))
                if existing_uids:
                    wean_form.add_error(
                        None,
                        f"mouse_uid already exists in database: {', '.join(sorted(existing_uids))}.",
                    )

                if not wean_form.errors:
                    target_cage = wean_form.cleaned_data["target_cage"]
                    assignment_mode = wean_form.cleaned_data["project_assignment_mode"]
                    inherited_project = None
                    if assignment_mode == WeanLitterForm.ProjectAssignmentMode.SIRE:
                        inherited_project = sire_project
                    elif assignment_mode == WeanLitterForm.ProjectAssignmentMode.DAM:
                        inherited_project = dam_project
                    else:
                        new_project_name = wean_form.cleaned_data["new_project_name"].strip()
                        inherited_project, created = Project.objects.get_or_create(
                            name=new_project_name,
                            defaults={
                                "description": (
                                    f"Created during litter wean for {litter.litter_id_display} "
                                    f"({breeding.breeding_code})."
                                ),
                                "is_active": True,
                                "owner": request.user,
                            },
                        )
                        if created:
                            ProjectMembership.objects.get_or_create(
                                project=inherited_project,
                                user=request.user,
                                defaults={"role": ProjectMembership.Role.MANAGER},
                            )
                if not wean_form.errors:
                    ensure_can_edit_project_data(request.user, inherited_project)
                    created_uids: list[str] = []
                    with transaction.atomic():
                        new_mice: list[Mouse] = []
                        for form in pup_formset.forms:
                            mouse = Mouse.objects.create(
                                mouse_uid=form.cleaned_data["mouse_uid"],
                                sex=form.cleaned_data["sex"],
                                birth_date=litter.birth_date,
                                status=Mouse.Status.ACTIVE,
                                strain_line=breeding.female_1.strain_line,
                                current_cage=target_cage,
                                sire=breeding.male,
                                dam=breeding.female_1,
                                project=inherited_project,
                                ear_tag=form.cleaned_data["ear_tag"],
                                coat_color=form.cleaned_data["coat_color"],
                                notes=form.cleaned_data["notes"],
                            )
                            mouse.ensure_template_genotype_components(extra_loci=offspring_template_loci)
                            new_mice.append(mouse)
                            created_uids.append(mouse.mouse_uid)

                        CageMembership.objects.bulk_create(
                            [
                                CageMembership(
                                    mouse=mouse,
                                    cage=target_cage,
                                    start_date=wean_date,
                                    end_date=None,
                                    is_current=True,
                                    reason="Weaned from litter",
                                    notes="",
                                )
                                for mouse in new_mice
                            ]
                        )

                        orphan_pups = list(
                            LitterPup.objects.filter(litter=litter, mouse_id__isnull=True).order_by(
                                "sort_order", "id"
                            )
                        )
                        for i, mouse in enumerate(new_mice):
                            if i < len(orphan_pups):
                                pup = orphan_pups[i]
                                pup.mouse = mouse
                                pup.save(update_fields=["mouse_id", "updated_at"])

                        litter.wean_date = wean_date
                        if litter.litter_status == Litter.LitterStatus.ACTIVE:
                            litter.litter_status = Litter.LitterStatus.WEANED
                        litter.save(update_fields=["wean_date", "litter_status", "updated_at"])

                    messages.success(
                        request,
                        f"Weaned {len(created_uids)} pups: {', '.join(created_uids)}.",
                    )
                    log_audit_event(
                        user=request.user,
                        action=AuditLog.Action.WEAN,
                        obj=litter,
                        message=(
                            f"Weaned {len(created_uids)} pups from litter {litter.litter_code or litter.pk} "
                            f"into cage {target_cage.cage_id} under project {inherited_project.name}: "
                            f"{', '.join(created_uids)}."
                        ),
                    )
                    return redirect("litters:litter_detail", pk=litter.pk)
    else:
        initial_pups = 1
        wean_form = WeanLitterForm(
            initial={"wean_date": litter.wean_date, "number_of_pups": initial_pups},
            sire_project=sire_project,
            dam_project=dam_project,
        )
        PupFormSet = get_pup_formset(initial_pups)
        pup_formset = PupFormSet(prefix="pups")

    context = {
        "litter": litter,
        "wean_form": wean_form,
        "pup_formset": pup_formset,
        "offspring_template_loci": offspring_template_loci,
        "breeding_sire": breeding_sire or breeding.male,
        "breeding_dams": breeding_dams,
    }
    return render(request, "breeding/litter_wean.html", context)
