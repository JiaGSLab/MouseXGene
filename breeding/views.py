import logging
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib import messages
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.core.exceptions import PermissionDenied
from django.db import OperationalError, ProgrammingError, transaction
from django.db.models import Count, Max, Prefetch, Q
from django.urls import reverse
from django.utils import timezone

from colony.cage_form_helpers import cage_filter_form_context
from colony.cage_lifecycle import (
    close_active_breedings_for_terminal_mouse,
    enrich_pending_breeding_cage,
    mark_cage_as_breeding,
    pending_breeding_cages_queryset,
    remove_terminal_mouse_from_current_cage,
    sync_breeding_member_cages,
    sync_cage_after_occupancy_change,
    sync_cage_status_from_mice,
)
from colony.strain_line_usage import strain_line_member_breeding_filter, strain_line_member_litter_filter
from colony.id_uniqueness import find_conflicting_mouse
from colony.models import Cage, CageMembership, Mouse, StrainLine
from colony.mouse_age import breeding_age_tier

from .cage_autocreate import colony_for_project_and_strain, create_auto_cage
from .forms import (
    EndBreedingForm,
    EndLitterForm,
    BreedingForm,
    LitterForm,
    LitterPupFormSet,
    LitterRecordForm,
    WeanLitterForm,
    WeanPupEntryForm,
    litter_has_weaned,
)
from .consistency import active_breeding_cage_mismatches, breeding_cage_mismatch_rows
from .dates import expected_birth_date_for
from .models import Breeding, BreedingExtraFemale, BreedingMember, Litter, LitterPup
from .analytics import breeding_litter_timing_alert, mendelian_single_locus_review_for_breeding
from core.audit import log_audit_event
from core.exporting import csv_response, xlsx_response
from core.list_sort import BREEDING_LIST_SORT, LITTER_LIST_SORT, apply_list_sort, build_list_sort_context
from core.history import audit_entries_for_object, merge_actor_labels
from core.models import AuditLog, Project, ProjectMembership, format_project_owner_label
from core.owner_filters import (
    breeding_project_owner_filter_q,
    litter_project_owner_filter_q,
    project_owner_filter_options,
    resolve_project_owner_filter,
)
from users.permissions import (
    authenticated_required,
    ensure_can_archive_or_change_terminal_status,
    ensure_can_edit_mice_projects,
    ensure_can_edit_project_data,
)

logger = logging.getLogger(__name__)

LIST_PAGE_SIZES = (25, 50, 100)
LIST_PAGE_DEFAULT = 25
LIST_ALL_RESULTS_MAX = 500


def _parse_positive_int(value: str) -> int | None:
    text = (value or "").strip()
    if not text or not text.isdigit():
        return None
    parsed = int(text)
    return parsed if parsed > 0 else None


def _mouse_age_days(mouse: Mouse | None, *, today=None) -> int | None:
    if mouse is None or not mouse.birth_date:
        return None
    local_today = today or timezone.localdate()
    return max((local_today - mouse.birth_date).days, 0)


def _age_days_display(age_days: int | None) -> str:
    if age_days is None:
        return ""
    age_weeks, remaining_days = divmod(age_days, 7)
    return f"{age_weeks}w {remaining_days}d"


def _mouse_age_weeks_display(mouse: Mouse | None, *, today=None) -> str:
    return _age_days_display(_mouse_age_days(mouse, today=today))


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
    raw_per = (request.GET.get("per_page") or "").strip().lower()

    if raw_per == "all":
        total = queryset.count()
        use_all = total <= LIST_ALL_RESULTS_MAX
        if total > LIST_ALL_RESULTS_MAX:
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
    total = paginator.count
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
        "male__project__owner",
        "male__project__owner__profile",
        "male__strain_line",
        "male__current_cage",
        "female_1",
        "female_1__project",
        "female_1__project__owner",
        "female_1__project__owner__profile",
        "female_1__strain_line",
        "female_1__current_cage",
        "female_2",
        "female_2__project",
        "female_2__project__owner",
        "female_2__project__owner__profile",
        "female_2__strain_line",
        "female_2__current_cage",
        "created_by",
        "created_by__profile",
        "updated_by",
        "updated_by__profile",
    ).prefetch_related(
        _breeding_member_prefetch(),
        _extra_female_prefetch(),
    )


def _breeding_member_queryset():
    return BreedingMember.objects.select_related(
        "mouse",
        "mouse__project",
        "mouse__project__owner",
        "mouse__project__owner__profile",
        "mouse__strain_line",
        "mouse__current_cage",
    ).order_by("role", "sort_order", "mouse__mouse_uid")


def _extra_female_queryset():
    return BreedingExtraFemale.objects.select_related(
        "mouse",
        "mouse__project",
        "mouse__project__owner",
        "mouse__project__owner__profile",
        "mouse__strain_line",
        "mouse__current_cage",
    ).order_by("mouse__mouse_uid")


def _breeding_member_prefetch(path: str = "breeding_members") -> Prefetch:
    return Prefetch(path, queryset=_breeding_member_queryset(), to_attr="prefetched_breeding_members")


def _extra_female_prefetch(path: str = "extra_female_links") -> Prefetch:
    return Prefetch(path, queryset=_extra_female_queryset(), to_attr="prefetched_extra_female_links")


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
        b.display_expected_birth_date = expected_birth_date_for(
            start_date=b.start_date,
            plug_date=b.plug_date,
            manual_date=b.expected_birth_date,
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
        b.display_sire_age_display = _age_days_display(b.display_sire_age_days)
        b.display_sire_genotype = _mouse_genotype_summary_for_list(b.display_sire)
        b.display_dam_rows = [
            {
                "mouse": dam,
                "age_days": _mouse_age_days(dam, today=today),
                "age_display": _mouse_age_weeks_display(dam, today=today),
                "genotype": _mouse_genotype_summary_for_list(dam),
            }
            for dam in (b.display_dams or [])
        ]
        b.setup_by_display = _breeding_setup_by_label(b, audit_map.get(b.pk, []))


def _breeding_member_mice(breeding: Breeding) -> list[Mouse]:
    prefetched_members = getattr(breeding, "prefetched_breeding_members", None)
    if prefetched_members is not None:
        members = [row.mouse for row in prefetched_members]
    else:
        try:
            members = [row.mouse for row in breeding.breeding_members.select_related("mouse").all()]
        except (ProgrammingError, OperationalError):
            members = []
    if members:
        return members
    prefetched_extra = getattr(breeding, "prefetched_extra_female_links", None)
    if prefetched_extra is not None:
        extra_mice = [row.mouse for row in prefetched_extra]
    else:
        extra_mice = [r.mouse for r in breeding.extra_female_links.select_related("mouse").all()]
    fallback = [breeding.male, breeding.female_1, breeding.female_2, *extra_mice]
    return [m for m in fallback if m is not None]


def _breeding_sire_and_dams(breeding: Breeding) -> tuple[Mouse | None, list[Mouse]]:
    sire: Mouse | None = None
    dams: list[Mouse] = []
    prefetched_members = getattr(breeding, "prefetched_breeding_members", None)
    if prefetched_members is not None:
        members = list(prefetched_members)
    else:
        try:
            members = list(_breeding_member_queryset().filter(breeding=breeding))
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
    prefetched_extra = getattr(breeding, "prefetched_extra_female_links", None)
    if prefetched_extra is not None:
        extra_links = prefetched_extra
    else:
        extra_links = _extra_female_queryset().filter(breeding=breeding)
    for row in extra_links:
        if row.mouse not in dams:
            dams.append(row.mouse)
    return sire, dams


def _litter_owner_rows(breeding: Breeding, sire_mouse: Mouse | None, dam_mices: list[Mouse]) -> list[dict]:
    seen: set[int] = set()
    rows: list[dict] = []
    candidates: list[Mouse] = []
    if sire_mouse:
        candidates.append(sire_mouse)
    candidates.extend(dam_mices)
    if not candidates:
        candidates = _breeding_member_mice(breeding)
    for mouse in candidates:
        if not mouse.project_id or not mouse.project.owner_id:
            continue
        if mouse.project.owner_id in seen:
            continue
        seen.add(mouse.project.owner_id)
        owner = mouse.project.owner
        rows.append(
            {
                "owner_id": owner.pk,
                "owner_display": (format_project_owner_label(owner) or owner.get_username() or str(owner.pk)).strip(),
            }
        )
    return rows


def _active_breeding_codes_for_mouse_ids(mouse_ids: list[int], *, exclude_breeding_id: int | None = None) -> dict[int, list[str]]:
    out: dict[int, set[str]] = {mid: set() for mid in mouse_ids}
    if not mouse_ids:
        return {}
    q = Breeding.objects.filter(active=True).filter(
        Q(male_id__in=mouse_ids)
        | Q(female_1_id__in=mouse_ids)
        | Q(female_2_id__in=mouse_ids)
        | Q(extra_female_links__mouse_id__in=mouse_ids)
        | Q(breeding_members__mouse_id__in=mouse_ids)
    )
    if exclude_breeding_id:
        q = q.exclude(pk=exclude_breeding_id)
    q = q.distinct().prefetch_related("extra_female_links__mouse", "breeding_members__mouse")
    for breeding in q:
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
                "age_tier": breeding_age_tier(m.birth_date, today),
                "active_breeding_codes": active_codes_map.get(m.pk, []),
            }
        )
    return payload


def _breeding_form_cage_context() -> dict:
    ctx = cage_filter_form_context()
    ctx["mouse_owner_options"] = ctx["cage_owner_options"]
    return ctx


def _litter_wean_orphan_pups(litter: Litter) -> list[LitterPup]:
    return list(litter.pups.filter(mouse_id__isnull=True).order_by("sort_order", "id"))


def _litter_wean_initial_sex_counts(litter: Litter) -> tuple[int, int, str]:
    """Return (male, female, source) where source is pups, litter, or manual."""
    orphan_pups = _litter_wean_orphan_pups(litter)
    if orphan_pups:
        male_count = 0
        female_count = 0
        for pup in orphan_pups:
            if pup.sex == Mouse.Sex.MALE:
                male_count += 1
            elif pup.sex == Mouse.Sex.FEMALE:
                female_count += 1
        return male_count, female_count, "pups"
    if litter.male_count is not None and litter.female_count is not None:
        male_count = max(litter.male_count, 0)
        female_count = max(litter.female_count, 0)
        if male_count + female_count > 0:
            return male_count, female_count, "litter"
    return 0, 0, "manual"


def _litter_wean_initial_pup_count(litter: Litter) -> int:
    male_count, female_count, _source = _litter_wean_initial_sex_counts(litter)
    total = male_count + female_count
    if total > 0:
        return total
    if litter.total_born:
        return max(1, litter.total_born)
    return 0


def _litter_wean_pup_initial_rows(litter: Litter) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for pup in _litter_wean_orphan_pups(litter):
        sex = pup.sex if pup.sex in {Mouse.Sex.MALE, Mouse.Sex.FEMALE} else Mouse.Sex.MALE
        rows.append(
            {
                "mouse_uid": "",
                "sex": sex,
                "ear_tag": pup.ear_tag or "",
                "coat_color": pup.coat_color or "",
                "notes": pup.notes or "",
            }
        )
    return rows


def _litter_wean_rows_from_sex_counts(litter: Litter) -> list[dict[str, str]]:
    if litter.male_count is None or litter.female_count is None:
        return []
    rows: list[dict[str, str]] = []
    blank = {"mouse_uid": "", "ear_tag": "", "coat_color": "", "notes": ""}
    for _ in range(max(litter.male_count, 0)):
        rows.append({**blank, "sex": Mouse.Sex.MALE})
    for _ in range(max(litter.female_count, 0)):
        rows.append({**blank, "sex": Mouse.Sex.FEMALE})
    return rows


def _litter_wean_prefill_rows(litter: Litter) -> list[dict[str, str]] | None:
    orphan_rows = _litter_wean_pup_initial_rows(litter)
    if orphan_rows:
        return orphan_rows
    count_rows = _litter_wean_rows_from_sex_counts(litter)
    return count_rows or None


def _litter_age_display(litter: Litter) -> str:
    if not litter.birth_date:
        return ""
    days = max((timezone.localdate() - litter.birth_date).days, 0)
    return _age_days_display(days)


def _count_pup_sexes_from_post(post_data, number_of_pups: int) -> tuple[int, int]:
    male_count = 0
    female_count = 0
    for index in range(number_of_pups):
        sex = (post_data.get(f"pups-{index}-sex") or "").strip()
        if sex == Mouse.Sex.MALE:
            male_count += 1
        elif sex == Mouse.Sex.FEMALE:
            female_count += 1
    return male_count, female_count


def _litter_wean_max_pup_count(litter: Litter) -> int | None:
    if litter.total_born:
        return max(1, litter.total_born)
    return None


def _parse_wean_pup_counts(post_data) -> tuple[int, int]:
    try:
        male_count = max(0, int(post_data.get("male_pup_count", "0")))
    except (TypeError, ValueError):
        male_count = 0
    try:
        female_count = max(0, int(post_data.get("female_pup_count", "0")))
    except (TypeError, ValueError):
        female_count = 0
    return male_count, female_count


def _expected_wean_pup_sex(index: int, male_count: int) -> str:
    return Mouse.Sex.MALE if index < male_count else Mouse.Sex.FEMALE


def _pup_row_from_post(post_data, index: int) -> dict[str, str]:
    return {
        "mouse_uid": (post_data.get(f"pups-{index}-mouse_uid") or "").strip(),
        "sex": post_data.get(f"pups-{index}-sex") or Mouse.Sex.UNKNOWN,
        "cage_slot": (post_data.get(f"pups-{index}-cage_slot") or "").strip(),
        "ear_tag": (post_data.get(f"pups-{index}-ear_tag") or "").strip(),
        "coat_color": (post_data.get(f"pups-{index}-coat_color") or "").strip(),
        "notes": (post_data.get(f"pups-{index}-notes") or "").strip(),
    }


def _build_wean_pup_forms(male_count, female_count, post_data=None, *, bind: bool = False, initial_rows=None):
    """Build pup forms from male/female counts (male rows first, then female)."""
    male_count = max(0, int(male_count or 0))
    female_count = max(0, int(female_count or 0))
    total = male_count + female_count
    forms = []
    for i in range(total):
        prefix = f"pups-{i}"
        expected_sex = _expected_wean_pup_sex(i, male_count)
        if bind and post_data is not None:
            forms.append(WeanPupEntryForm(post_data, prefix=prefix))
        elif post_data is not None:
            row = _pup_row_from_post(post_data, i)
            row["sex"] = expected_sex
            forms.append(WeanPupEntryForm(initial=row, prefix=prefix))
        elif initial_rows and i < len(initial_rows):
            row = dict(initial_rows[i])
            row["sex"] = expected_sex
            forms.append(WeanPupEntryForm(initial=row, prefix=prefix))
        else:
            forms.append(WeanPupEntryForm(initial={"sex": expected_sex}, prefix=prefix))
    return forms


def _pup_forms_are_valid(pup_forms) -> bool:
    return all(form.is_valid() for form in pup_forms)


def _litter_wean_page_context(
    *,
    litter: Litter,
    wean_form,
    pup_forms,
    offspring_template_loci,
    breeding_sire=None,
    breeding_dams=None,
    wean_primary_dam=None,
    parent_breeding_options=None,
    breeding_has_trio_dam: bool = False,
    wean_counts_source: str = "manual",
    cage_project_filter: str = "",
    cage_owner_filter: str = "",
) -> dict:
    cage_ctx = cage_filter_form_context()
    sire_strain = breeding_sire.strain_line if breeding_sire and breeding_sire.strain_line_id else None
    dam_strain = wean_primary_dam.strain_line if wean_primary_dam and wean_primary_dam.strain_line_id else None
    return {
        "litter": litter,
        "wean_form": wean_form,
        "pup_forms": pup_forms,
        "pup_max_count": _litter_wean_max_pup_count(litter),
        "offspring_template_loci": offspring_template_loci,
        "breeding_sire": breeding_sire,
        "breeding_dams": breeding_dams,
        "wean_primary_dam": wean_primary_dam,
        "parent_breeding_options": parent_breeding_options,
        "breeding_has_trio_dam": breeding_has_trio_dam,
        "wean_counts_source": wean_counts_source,
        "sire_strain_line": sire_strain,
        "dam_strain_line": dam_strain,
        "litter_age_display": _litter_age_display(litter),
        "target_cage_choices": [],
        "cage_picker_api_url": cage_ctx.get("cage_picker_api_url", "/cages/api/picker/"),
        "mouse_uid_check_api_url": cage_ctx.get("mouse_uid_check_api_url", "/mice/api/uid-check/"),
        "wean_project_options": cage_ctx["cage_project_options"],
        "wean_owner_options": cage_ctx["cage_owner_options"],
        "cage_strain_line_options": cage_ctx["cage_strain_line_options"],
        "cage_project_filter": cage_project_filter,
        "cage_owner_filter": cage_owner_filter,
        "litter_recorded_male_count": litter.male_count,
        "litter_recorded_female_count": litter.female_count,
    }


def _wean_parent_breeding_queryset(user, current_breeding: Breeding):
    return (
        _scoped_breedings(user)
        .filter(cage__isnull=False)
        .filter(
            Q(pk=current_breeding.pk)
            | Q(cage__purpose=Cage.Purpose.BREEDING)
            | Q(cage__cage_type=Cage.CageType.BREEDING)
        )
        .distinct()
        .order_by("cage__cage_id", "-active", "-start_date", "breeding_code")
    )


def _single_project_from_dams(dams: list[Mouse]) -> tuple[Project | None, str | None]:
    projects = []
    seen: set[int] = set()
    for dam in dams:
        if dam.project_id and dam.project_id not in seen:
            seen.add(dam.project_id)
            projects.append(dam.project)
    if len(projects) == 1:
        return projects[0], None
    if not projects:
        return None, "Selected possible dam(s) have no project assigned."
    names = ", ".join(project.name for project in projects)
    return None, f"Possible dams belong to different projects ({names}). Choose sire project or create a new project."


def _single_strain_from_dams(dams: list[Mouse]) -> tuple[StrainLine | None, str | None]:
    strains = []
    seen: set[int] = set()
    for dam in dams:
        if dam.strain_line_id and dam.strain_line_id not in seen:
            seen.add(dam.strain_line_id)
            strains.append(dam.strain_line)
    if len(strains) == 1:
        return strains[0], None
    if not strains:
        return None, "Selected possible dam(s) have no strain line assigned."
    names = ", ".join(strain.line_name for strain in strains)
    return None, f"Possible dams have different strain lines ({names}). Choose sire strain or create a new strain line."


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


def _mouse_genotype_summary_for_list(mouse: Mouse | None) -> str:
    if mouse is None:
        return "-"
    return mouse.genotype_summary or "-"


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


def resolve_wean_strain_line(
    *,
    mode: str,
    new_line_name: str,
    sire: Mouse | None,
    dam: Mouse | None,
    possible_dams: list[Mouse] | None = None,
    template_loci: list[str] | None = None,
    user,
    litter_display: str,
    breeding_code: str,
    project=None,
) -> tuple[StrainLine | None, str | None]:
    if mode == WeanLitterForm.StrainAssignmentMode.SIRE:
        if not sire or not sire.strain_line_id:
            return None, "Sire has no strain line assigned."
        return sire.strain_line, None
    if mode == WeanLitterForm.StrainAssignmentMode.DAM:
        if possible_dams is not None:
            return _single_strain_from_dams(possible_dams)
        if not dam or not dam.strain_line_id:
            return None, "Dam has no strain line assigned."
        return dam.strain_line, None
    name = (new_line_name or "").strip()
    if not name:
        return None, "Please enter a strain line name."
    if StrainLine.objects.filter(line_name__iexact=name).exists():
        return None, f'Strain line "{name}" already exists. Choose another name or follow sire/dam.'
    loci = [locus for locus in (template_loci or []) if (locus or "").strip()]
    line = StrainLine.objects.create(
        line_name=name,
        name=name,
        owner=user,
        expected_loci_template="\n".join(loci),
        expected_loci_config=[
            {
                "locus_name": locus,
                "locus_type": StrainLine.LocusType.OTHER_CUSTOM,
                "chromosome_type": StrainLine.ChromosomeType.AUTOSOMAL,
            }
            for locus in loci
        ],
        notes=f"Created during litter wean for {litter_display} ({breeding_code}).",
    )
    if project is not None:
        line.projects.add(project)
    return line, None


def _build_xlsx_response(filename: str, sheet_name: str, headers: list[str], rows: list[list]) -> HttpResponse:
    return xlsx_response(filename, sheet_name, headers, rows)


@authenticated_required
def breeding_list(request: HttpRequest) -> HttpResponse:
    q = (request.GET.get("q") or "").strip()
    strain_line_id = (request.GET.get("strain_line_id") or request.GET.get("strain_line") or "").strip()
    status = (request.GET.get("status") or "").strip()
    breeding_type = (request.GET.get("breeding_type") or "").strip()
    cage = (request.GET.get("cage") or "").strip()
    setup_by = (request.GET.get("setup_by") or "").strip()
    include_inactive = (request.GET.get("include_inactive") or "").strip()
    alert = (request.GET.get("alert") or "").strip()
    owner = resolve_project_owner_filter(request)
    export = (request.GET.get("export") or "").strip().lower()

    breedings = _scoped_breedings(request.user)
    if include_inactive != "yes":
        breedings = breedings.filter(active=True)
    if owner and not strain_line_id:
        breedings = breedings.filter(breeding_project_owner_filter_q(owner))
    setup_by_id = _parse_positive_int(setup_by)
    if setup_by and setup_by_id is None:
        breedings = breedings.none()
    elif setup_by_id is not None:
        audit_pks: list[int] = []
        for oid in AuditLog.objects.filter(
            object_type="Breeding",
            action=AuditLog.Action.CREATE,
            user_id=setup_by_id,
        ).values_list("object_id", flat=True):
            try:
                audit_pks.append(int(oid))
            except (TypeError, ValueError):
                continue
        breedings = breedings.filter(Q(created_by_id=setup_by_id) | Q(pk__in=audit_pks))
    if strain_line_id:
        try:
            breedings = breedings.filter(strain_line_member_breeding_filter(int(strain_line_id)))
        except (TypeError, ValueError):
            breedings = breedings.none()
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
    cage_id = _parse_positive_int(cage)
    if cage and cage_id is None:
        breedings = breedings.none()
    elif cage_id is not None:
        breedings = breedings.filter(cage_id=cage_id)

    breedings = breedings.distinct().annotate(
        litter_count=Count("litters", distinct=True),
        latest_litter_date=Max("litters__birth_date"),
    )
    today = timezone.localdate()
    if alert == "overdue":
        overdue_cutoff = today - timedelta(days=22)
        breedings = breedings.filter(Q(active=True) & ~Q(status=Breeding.Status.CLOSED)).filter(
            Q(litter_count=0, start_date__lte=overdue_cutoff)
            | Q(litter_count__gt=0, latest_litter_date__lte=overdue_cutoff)
        )
    elif alert == "cage_mismatch":
        mismatch_ids = [breeding.pk for breeding in active_breeding_cage_mismatches(breedings)]
        breedings = breedings.filter(pk__in=mismatch_ids) if mismatch_ids else breedings.none()
    breedings = apply_list_sort(breedings, request, BREEDING_LIST_SORT)

    if export in {"csv", "xlsx"}:
        breedings_export_list = list(breedings)
        _enrich_breedings_for_list(breedings_export_list, today=today)
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
            return csv_response("breedings_export.csv", headers, rows)
        return _build_xlsx_response("breedings_export.xlsx", "Breedings", headers, rows)

    pagination = _paginate_queryset_for_list(request, breedings, viewname="breeding:breeding_list")
    breedings_page_items = list(pagination["items"])
    _enrich_breedings_for_list(breedings_page_items, today=today)

    strain_line_filter_label = ""
    if strain_line_id:
        strain_line_filter_label = (
            StrainLine.objects.filter(pk=strain_line_id).values_list("line_name", flat=True).first() or ""
        )

    pending_breeding_cages: list[Cage] = []
    if include_inactive != "yes" and not export:
        pending_qs = pending_breeding_cages_queryset()
        if cage:
            pending_qs = pending_qs.filter(pk=cage)
        if q:
            pending_qs = pending_qs.filter(cage_id__icontains=q)
        if strain_line_id:
            try:
                from colony.strain_line_usage import strain_line_cage_ids

                pending_cage_ids = strain_line_cage_ids(
                    strain_line_id=int(strain_line_id),
                    active_only=True,
                )
                pending_qs = pending_qs.filter(pk__in=pending_cage_ids) if pending_cage_ids else pending_qs.none()
            except (TypeError, ValueError):
                pending_qs = pending_qs.none()
        elif owner:
            pending_qs = pending_qs.filter(current_mice__project__owner_id=owner).distinct()
        pending_breeding_cages = list(pending_qs)
        for pending_cage in pending_breeding_cages:
            enrich_pending_breeding_cage(pending_cage)

    context = {
        "breedings": breedings_page_items,
        "pending_breeding_cages": pending_breeding_cages,
        "q": q,
        "strain_line_id": strain_line_id,
        "strain_line_filter_label": strain_line_filter_label,
        "status": status,
        "breeding_type": breeding_type,
        "cage": cage,
        "setup_by": setup_by,
        "alert": alert,
        "owner": "" if strain_line_id else owner,
        "owner_options": project_owner_filter_options(),
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


def _breeding_initial_from_request(request: HttpRequest) -> dict:
    sire_id = _parse_positive_int(request.GET.get("sire") or "")
    dam_ids: list[int] = []
    for chunk in (request.GET.get("dams") or "").replace(",", " ").split():
        parsed = _parse_positive_int(chunk)
        if parsed and parsed not in dam_ids:
            dam_ids.append(parsed)
    initial: dict = {}
    if sire_id:
        initial["sire"] = sire_id
    if dam_ids:
        initial["dams"] = dam_ids
        if len(dam_ids) == 1:
            initial["breeding_type"] = Breeding.BreedingType.PAIR
        elif len(dam_ids) == 2:
            initial["breeding_type"] = Breeding.BreedingType.TRIO
        else:
            initial["breeding_type"] = Breeding.BreedingType.CUSTOM
    return initial


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
                if form.created_auto_cage is not None:
                    messages.info(request, f"Created cage {form.created_auto_cage.cage_id} for this breeding.")
                    log_audit_event(
                        user=request.user,
                        action=AuditLog.Action.CREATE,
                        obj=form.created_auto_cage,
                        message=f"Auto-created cage {form.created_auto_cage.cage_id} for breeding {breeding.breeding_code}.",
                    )
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
        except PermissionDenied:
            raise
        except Exception:
            logger.exception("Unexpected error during breeding create POST.")
            messages.error(
                request,
                "Failed to save breeding due to an unexpected server error. "
                "Please review member selection and try again.",
            )
    else:
        form = BreedingForm(initial=_breeding_initial_from_request(request))

    context = {
        "form": form,
        "page_title": "Create Breeding",
        "submit_label": "Save Breeding",
        "cancel_url": "breeding:breeding_list",
        "breeder_mouse_choices": [],
        **_breeding_form_cage_context(),
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
                if form.created_auto_cage is not None:
                    messages.info(request, f"Created cage {form.created_auto_cage.cage_id} for this breeding.")
                    log_audit_event(
                        user=request.user,
                        action=AuditLog.Action.CREATE,
                        obj=form.created_auto_cage,
                        message=f"Auto-created cage {form.created_auto_cage.cage_id} for breeding {breeding.breeding_code}.",
                    )
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
        except PermissionDenied:
            raise
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
        "breeder_mouse_choices": [],
        **_breeding_form_cage_context(),
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
    breeding_sire, breeding_dams = _breeding_sire_and_dams(breeding)
    offspring_filter = Q(source_breeding=breeding)
    if breeding_sire and breeding_dams:
        offspring_filter |= Q(sire=breeding_sire, dam__in=breeding_dams)
        offspring_filter |= Q(sire=breeding_sire, possible_dams__in=breeding_dams)
    offspring = list(
        Mouse.objects.filter(offspring_filter)
        .prefetch_related("genotype_components")
        .distinct()
        .order_by("mouse_uid")
    )
    mendelian_reviews = mendelian_single_locus_review_for_breeding(
        breeding,
        offspring,
        sire=breeding_sire,
        dams=breeding_dams,
    )
    mendelian_flag_count = sum(1 for r in mendelian_reviews if r["status"] == "review")
    expected_offspring_loci = _union_loci_from_strain_lines(
        breeding_sire.strain_line if breeding_sire else None,
        *[dam.strain_line for dam in breeding_dams if dam and dam.strain_line_id],
    )
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
    display_expected_birth_date = expected_birth_date_for(
        start_date=breeding.start_date,
        plug_date=breeding.plug_date,
        manual_date=breeding.expected_birth_date,
    )
    cage_mismatch_rows = breeding_cage_mismatch_rows(breeding)
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
            "cage_mismatch_rows": cage_mismatch_rows,
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
    members = _breeding_member_mice(breeding)
    ensure_can_edit_mice_projects(request.user, members)
    if breeding.status == Breeding.Status.CLOSED and not breeding.active:
        messages.info(request, f"Breeding {breeding.breeding_code} is already closed.")
        return redirect("breeding:breeding_detail", pk=breeding.pk)

    if request.method == "POST":
        form = EndBreedingForm(request.POST, breeding=breeding, members=members)
        if form.is_valid():
            for member in members:
                if form.action_map.get(member.pk) in EndBreedingForm.TERMINAL_ACTIONS:
                    ensure_can_archive_or_change_terminal_status(request.user, member.project)
            end_date = form.cleaned_data["end_date"]
            notes = (form.cleaned_data.get("notes") or "").strip()
            reason = f"Breeding ended: {breeding.breeding_code}"
            affected_cage_ids: set[int] = {breeding.cage_id} if breeding.cage_id else set()
            move_messages: list[str] = []
            closed_cage_ids: set[str] = set()

            with transaction.atomic():
                locked_breeding = Breeding.objects.select_for_update().get(pk=breeding.pk)
                locked_mice = {
                    mouse.pk: mouse
                    for mouse in Mouse.objects.select_for_update().filter(pk__in=[member.pk for member in members])
                }
                destinations = {
                    mouse_pk: (
                        Cage.objects.select_for_update().filter(pk=destination.pk).first()
                        if destination is not None
                        else None
                    )
                    for mouse_pk, destination in form.destination_map.items()
                }

                for member in members:
                    mouse = locked_mice[member.pk]
                    action = form.action_map.get(member.pk, EndBreedingForm.MemberAction.MOVE)
                    destination = destinations.get(member.pk)
                    origin_cage = Cage.objects.filter(pk=mouse.current_cage_id).first() if mouse.current_cage_id else None
                    if origin_cage:
                        affected_cage_ids.add(origin_cage.pk)
                    if destination:
                        affected_cage_ids.add(destination.pk)

                    if action in EndBreedingForm.TERMINAL_ACTIONS:
                        mouse.status = action
                        if action in {Mouse.Status.EUTHANIZED, Mouse.Status.CULLED}:
                            mouse.euthanasia_date = end_date
                            mouse.death_date = None
                        elif action == Mouse.Status.DEAD:
                            mouse.death_date = end_date
                            mouse.euthanasia_date = None
                        mouse.death_reason = notes or reason
                        mouse.save(update_fields=["status", "euthanasia_date", "death_date", "death_reason", "updated_at"])
                        terminal_cage_ids = remove_terminal_mouse_from_current_cage(
                            mouse,
                            exit_date=end_date,
                            reason=f"{reason}: {mouse.get_status_display()}",
                        )
                        closed_other_codes = close_active_breedings_for_terminal_mouse(
                            mouse,
                            end_date=end_date,
                            reason=reason,
                            exclude_breeding_id=locked_breeding.pk,
                        )
                        if terminal_cage_ids:
                            closed_cage_ids.update(terminal_cage_ids)
                        status_label = mouse.get_status_display()
                        move_messages.append(
                            f"{mouse.mouse_uid}: {origin_cage.cage_id if origin_cage else 'No cage'} -> {status_label}"
                        )
                        if closed_other_codes:
                            move_messages.append(
                                f"{mouse.mouse_uid}: also closed active breeding(s) {', '.join(closed_other_codes)}"
                            )
                        continue

                    if origin_cage and destination and origin_cage.pk == destination.pk:
                        move_messages.append(f"{mouse.mouse_uid}: kept in {destination.cage_id}")
                        continue

                    current_memberships = list(
                        CageMembership.objects.select_for_update().filter(mouse=mouse, is_current=True)
                    )
                    for membership in current_memberships:
                        membership_end_date = end_date
                        if membership.start_date and membership_end_date < membership.start_date:
                            membership_end_date = membership.start_date
                        membership.end_date = membership_end_date
                        membership.is_current = False
                        membership.reason = reason[:128]
                        membership.save(update_fields=["end_date", "is_current", "reason", "updated_at"])

                    mouse.current_cage = destination
                    mouse.save(update_fields=["current_cage", "updated_at"])
                    if destination is not None:
                        CageMembership.objects.create(
                            mouse=mouse,
                            cage=destination,
                            start_date=end_date,
                            end_date=None,
                            is_current=True,
                            reason=reason[:128],
                            notes=notes,
                        )
                        move_messages.append(
                            f"{mouse.mouse_uid}: {origin_cage.cage_id if origin_cage else 'No cage'} -> {destination.cage_id}"
                        )
                    else:
                        move_messages.append(
                            f"{mouse.mouse_uid}: {origin_cage.cage_id if origin_cage else 'No cage'} -> no current cage"
                        )

                locked_breeding.status = Breeding.Status.CLOSED
                locked_breeding.active = False
                if not locked_breeding.archived_at:
                    locked_breeding.archived_at = timezone.now()
                locked_breeding.save(update_fields=["status", "active", "archived_at"])

                if locked_breeding.cage_id:
                    breeding_cage = Cage.objects.select_for_update().filter(pk=locked_breeding.cage_id).first()
                    if breeding_cage is not None and not breeding_cage.current_mice.exists():
                        cage_updates: list[str] = []
                        if breeding_cage.purpose == Cage.Purpose.BREEDING:
                            breeding_cage.purpose = Cage.Purpose.HOLDING
                            cage_updates.append("purpose")
                        if breeding_cage.cage_type == Cage.CageType.BREEDING:
                            breeding_cage.cage_type = Cage.CageType.STANDARD
                            cage_updates.append("cage_type")
                        if cage_updates:
                            cage_updates.append("updated_at")
                            breeding_cage.save(update_fields=cage_updates)
                        if sync_cage_after_occupancy_change(breeding_cage):
                            closed_cage_ids.add(breeding_cage.cage_id)

                for cage in Cage.objects.filter(pk__in=affected_cage_ids):
                    if sync_cage_status_from_mice(cage) or sync_cage_after_occupancy_change(cage):
                        closed_cage_ids.add(cage.cage_id)

            log_audit_event(
                user=request.user,
                action=AuditLog.Action.UPDATE,
                obj=breeding,
                message=(
                    f"Ended breeding {breeding.breeding_code} on {end_date}. "
                    f"Breeder movements: {'; '.join(move_messages)}"
                )[:4000],
            )
            messages.success(request, f"Breeding {breeding.breeding_code} ended and breeder cages updated.")
            if closed_cage_ids:
                messages.info(request, f"Closed empty cage(s): {', '.join(sorted(closed_cage_ids))}.")
            return redirect("breeding:breeding_detail", pk=breeding.pk)
    else:
        form = EndBreedingForm(breeding=breeding, members=members)

    breeding_sire, breeding_dams = _breeding_sire_and_dams(breeding)
    return render(
        request,
        "breeding/breeding_end.html",
        {
            "breeding": breeding,
            "form": form,
            "member_rows": form.member_rows,
            "breeding_sire": breeding_sire,
            "breeding_dams": breeding_dams,
            **cage_filter_form_context(),
        },
    )


def _enrich_litters_for_list(litters: list[Litter], *, today, user) -> None:
    for litter in litters:
        sire_mouse, dam_mice = _breeding_sire_and_dams(litter.breeding)
        litter.sire_mouse = sire_mouse
        litter.dam_mice = dam_mice
        litter.owner_rows = _litter_owner_rows(litter.breeding, sire_mouse, dam_mice)
        litter.breeder_member_rows = []
        if sire_mouse:
            litter.breeder_member_rows.append(
                {
                    "role": "Sire",
                    "mouse": sire_mouse,
                    "age_display": _mouse_age_weeks_display(sire_mouse, today=today),
                    "genotype_summary": _mouse_genotype_summary_for_list(sire_mouse),
                    "cage": sire_mouse.current_cage,
                }
            )
        for dam in dam_mice:
            litter.breeder_member_rows.append(
                {
                    "role": "Dam",
                    "mouse": dam,
                    "age_display": _mouse_age_weeks_display(dam, today=today),
                    "genotype_summary": _mouse_genotype_summary_for_list(dam),
                    "cage": dam.current_cage,
                }
            )
        litter.primary_dam = dam_mice[0] if dam_mice else litter.breeding.female_1
        litter.sire_genotype_summary = _mouse_genotype_summary_for_list(litter.sire_mouse or litter.breeding.male)
        litter.dam_genotype_summary = _mouse_genotype_summary_for_list(litter.primary_dam)
        litter.user_can_edit = user_can_edit_litter(user, litter)
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
            litter.age_display = _age_days_display(litter.age_days)
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
            litter.age_display = ""
            litter.wean_due_date = None
            litter.wean_overdue_days = 0
            litter.weaning_status = "Unknown birth date"

        litter.tagging_status = "Completed" if litter.tail_tag_display else "Pending"
        litter.mice_created_status = (
            "Completed"
            if litter.pups_count_display is not None and litter.created_mice_count >= litter.pups_count_display
            else f"{litter.created_mice_count}/{litter.pups_count_display or '?'}"
        )
        litter.is_weaned = bool(litter.wean_date or litter.litter_status == Litter.LitterStatus.WEANED)
        if litter.is_weaned:
            litter.wean_state_label = "Weaned / closed"
            litter.wean_state_badge_class = "status-pill-weaned"
            litter.wean_section_bucket = "weaned"
            litter.wean_section_title = "Weaned / closed litters"
        else:
            litter.wean_state_label = "Not weaned"
            litter.wean_state_badge_class = "status-pill-active"
            litter.wean_section_bucket = "not-weaned"
            litter.wean_section_title = "Not weaned litters"

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


@authenticated_required
def litter_list(request: HttpRequest) -> HttpResponse:
    q = (request.GET.get("q") or "").strip()
    strain_line_id = (request.GET.get("strain_line_id") or request.GET.get("strain_line") or "").strip()
    weaned = (request.GET.get("weaned") or "").strip()
    breeding = (request.GET.get("breeding") or "").strip()
    birth_date_from = (request.GET.get("birth_date_from") or "").strip()
    birth_date_to = (request.GET.get("birth_date_to") or "").strip()
    include_inactive = (request.GET.get("include_inactive") or "").strip()
    litter_status = (request.GET.get("litter_status") or "").strip()
    weaning_due = (request.GET.get("weaning_due") or "").strip()
    owner = resolve_project_owner_filter(request)
    export = (request.GET.get("export") or "").strip().lower()

    litters = (
        Litter.objects.filter(breeding__in=_scoped_breedings(request.user))
        .select_related(
            "breeding",
            "breeding__male",
            "breeding__male__project",
            "breeding__male__project__owner",
            "breeding__male__project__owner__profile",
            "breeding__male__strain_line",
            "breeding__male__current_cage",
            "breeding__female_1",
            "breeding__female_1__project",
            "breeding__female_1__project__owner",
            "breeding__female_1__project__owner__profile",
            "breeding__female_1__strain_line",
            "breeding__female_1__current_cage",
            "breeding__female_2",
            "breeding__female_2__project",
            "breeding__female_2__project__owner",
            "breeding__female_2__project__owner__profile",
            "breeding__female_2__strain_line",
            "breeding__female_2__current_cage",
            "breeding__cage",
        )
        .prefetch_related(
            _breeding_member_prefetch("breeding__breeding_members"),
            _extra_female_prefetch("breeding__extra_female_links"),
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
    if owner and not strain_line_id:
        litters = litters.filter(litter_project_owner_filter_q(owner))
    if strain_line_id:
        try:
            litters = litters.filter(strain_line_member_litter_filter(int(strain_line_id)))
        except (TypeError, ValueError):
            litters = litters.none()
    if q:
        litters = litters.filter(
            Q(litter_code__icontains=q)
            | Q(breeding__breeding_code__icontains=q)
            | Q(breeding__male__mouse_uid__icontains=q)
            | Q(breeding__female_1__mouse_uid__icontains=q)
        )
    breeding_id = _parse_positive_int(breeding)
    if breeding and breeding_id is None:
        litters = litters.none()
    elif breeding_id is not None:
        litters = litters.filter(breeding_id=breeding_id)
    if birth_date_from:
        litters = litters.filter(birth_date__gte=birth_date_from)
    if birth_date_to:
        litters = litters.filter(birth_date__lte=birth_date_to)
    if litter_status:
        litters = litters.filter(litter_status=litter_status)
    today = timezone.localdate()
    if weaning_due == "soon":
        wean_due_end = today + timedelta(days=3)
        litters = litters.filter(
            birth_date__isnull=False,
            wean_date__isnull=True,
            birth_date__range=(today - timedelta(days=21), wean_due_end - timedelta(days=21)),
        )

    litters_for_wean_counts = litters
    not_weaned_count = (
        litters_for_wean_counts.filter(wean_date__isnull=True)
        .exclude(litter_status=Litter.LitterStatus.WEANED)
        .count()
    )
    weaned_count = litters_for_wean_counts.filter(
        Q(wean_date__isnull=False) | Q(litter_status=Litter.LitterStatus.WEANED)
    ).count()
    if weaned == "yes":
        litters = litters.filter(Q(wean_date__isnull=False) | Q(litter_status=Litter.LitterStatus.WEANED))
    elif weaned == "no":
        litters = litters.filter(wean_date__isnull=True).exclude(litter_status=Litter.LitterStatus.WEANED)

    litters_qs = apply_list_sort(litters, request, LITTER_LIST_SORT)

    if export in {"csv", "xlsx"}:
        litters = list(litters_qs)
        _enrich_litters_for_list(litters, today=today, user=request.user)
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
            return csv_response("litters_workflow_export.csv", headers, rows)
        return _build_xlsx_response("litters_workflow_export.xlsx", "Litters", headers, rows)

    selected_breeding = None
    if breeding:
        try:
            selected_breeding = _scoped_breedings(request.user).filter(pk=int(breeding)).first()
        except (TypeError, ValueError):
            selected_breeding = None

    strain_line_filter_label = ""
    if strain_line_id:
        strain_line_filter_label = (
            StrainLine.objects.filter(pk=strain_line_id).values_list("line_name", flat=True).first() or ""
        )

    pagination = _paginate_queryset_for_list(request, litters_qs, viewname="litters:litter_list")
    litters_page_items = list(pagination["items"])
    _enrich_litters_for_list(litters_page_items, today=today, user=request.user)
    litters_page_items = sorted(litters_page_items, key=lambda litter: 1 if litter.is_weaned else 0)
    previous_wean_bucket = None
    for litter in litters_page_items:
        litter.show_wean_section_header = litter.wean_section_bucket != previous_wean_bucket
        litter.wean_section_count = weaned_count if litter.is_weaned else not_weaned_count
        previous_wean_bucket = litter.wean_section_bucket

    context = {
        "litters": litters_page_items,
        "not_weaned_count": not_weaned_count,
        "weaned_count": weaned_count,
        "q": q,
        "weaned": weaned,
        "breeding": breeding,
        "birth_date_from": birth_date_from,
        "birth_date_to": birth_date_to,
        "include_inactive": include_inactive,
        "litter_status": litter_status,
        "weaning_due": weaning_due,
        "strain_line_id": strain_line_id,
        "strain_line_filter_label": strain_line_filter_label,
        "owner": "" if strain_line_id else owner,
        "owner_options": project_owner_filter_options(),
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
    q = (request.GET.get("q") or "").strip()
    project = (request.GET.get("project") or request.GET.get("project_id") or "").strip()
    owner = resolve_project_owner_filter(request)
    strain_line_id = (request.GET.get("strain_line_id") or request.GET.get("strain_line") or "").strip()
    status = (request.GET.get("status") or "").strip()
    include_closed = (request.GET.get("include_closed") or "").strip()
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

    breeding_options = _scoped_breedings(request.user)
    if include_closed != "yes":
        breeding_options = breeding_options.filter(active=True).exclude(status=Breeding.Status.CLOSED)
    if owner and not strain_line_id:
        breeding_options = breeding_options.filter(breeding_project_owner_filter_q(owner))
    if project:
        breeding_options = breeding_options.filter(
            Q(male__project_id=project)
            | Q(female_1__project_id=project)
            | Q(female_2__project_id=project)
            | Q(extra_female_links__mouse__project_id=project)
            | Q(breeding_members__mouse__project_id=project)
        )
    if strain_line_id:
        try:
            breeding_options = breeding_options.filter(strain_line_member_breeding_filter(int(strain_line_id)))
        except (TypeError, ValueError):
            breeding_options = breeding_options.none()
    if status:
        breeding_options = breeding_options.filter(status=status)
    if q:
        breeding_options = breeding_options.filter(
            Q(breeding_code__icontains=q)
            | Q(cage__cage_id__icontains=q)
            | Q(male__mouse_uid__icontains=q)
            | Q(female_1__mouse_uid__icontains=q)
            | Q(female_2__mouse_uid__icontains=q)
            | Q(extra_female_links__mouse__mouse_uid__icontains=q)
            | Q(breeding_members__mouse__mouse_uid__icontains=q)
        )
    breeding_options = (
        breeding_options.distinct()
        .annotate(
            litter_count=Count("litters", distinct=True),
            latest_litter_date=Max("litters__birth_date"),
        )
        .order_by("expected_birth_date", "-start_date", "breeding_code")
    )
    pagination = _paginate_queryset_for_list(
        request,
        breeding_options,
        viewname="litters:litter_create_from_breeding",
    )
    breeding_rows = list(pagination["items"])
    today = timezone.localdate()
    _enrich_breedings_for_list(breeding_rows, today=today)
    for breeding in breeding_rows:
        breeding.owner_rows = _litter_owner_rows(
            breeding,
            getattr(breeding, "display_sire", None),
            getattr(breeding, "display_dams", []) or [],
        )

    strain_line_filter_label = ""
    if strain_line_id:
        strain_line_filter_label = (
            StrainLine.objects.filter(pk=strain_line_id).values_list("line_name", flat=True).first() or ""
        )
    return render(
        request,
        "breeding/litter_create_from_breeding.html",
        {
            "breeding_options": breeding_rows,
            "selected_breeding_id": selected_breeding_id,
            "q": q,
            "project": project,
            "owner": "" if strain_line_id else owner,
            "strain_line_id": strain_line_id,
            "strain_line_filter_label": strain_line_filter_label,
            "status": status,
            "include_closed": include_closed,
            "project_options": Project.objects.filter(is_active=True).order_by("name"),
            "owner_options": project_owner_filter_options(),
            "strain_line_options": StrainLine.objects.filter(is_active=True).order_by("line_name"),
            "status_options": Breeding.Status.choices,
            "page_obj": pagination["page_obj"],
            "paginator": pagination["paginator"],
            "pagination_hrefs": pagination["pagination_hrefs"],
            "per_page": pagination["per_page"],
            "total_count": pagination["total_count"],
            "all_allowed": pagination["all_allowed"],
            "list_all_max": LIST_ALL_RESULTS_MAX,
        },
    )


@authenticated_required
def litter_create(request: HttpRequest, breeding_pk: int) -> HttpResponse:
    breeding = get_object_or_404(_scoped_breedings(request.user), pk=breeding_pk)
    ensure_can_edit_mice_projects(request.user, _breeding_member_mice(breeding))
    if request.method == "POST":
        form = LitterRecordForm(request.POST)
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
        form = LitterRecordForm()

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
                "age_display": _mouse_age_weeks_display(breeding_sire, today=today),
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
                "age_display": _mouse_age_weeks_display(dam, today=today),
                "genotype_summary": _mouse_genotype_summary(dam),
                "cage": dam.current_cage,
                "status": dam.get_status_display(),
            }
        )
    primary_dam = breeding_dams[0] if breeding_dams else litter.breeding.female_1
    registered_offspring = list(
        Mouse.objects.filter(
            birth_date=litter.birth_date,
        )
        .filter(
            Q(source_breeding=litter.breeding)
            | Q(
                dam_id=primary_dam.id if primary_dam else litter.breeding.female_1_id,
                sire_id=breeding_sire.id if breeding_sire else litter.breeding.male_id,
            )
            | Q(possible_dams__in=breeding_dams)
        )
        .select_related("strain_line", "current_cage", "project")
        .distinct()
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
    if litter.litter_status in (Litter.LitterStatus.ENDED, Litter.LitterStatus.ARCHIVED):
        messages.info(request, "This litter is already closed.")
        return redirect("litters:litter_detail", pk=litter.pk)

    has_weaned = litter_has_weaned(litter)
    registered_pup_count = litter.pups.filter(mouse__isnull=False).count()
    if request.method == "POST":
        form = EndLitterForm(request.POST, litter=litter)
        if not form.is_valid():
            messages.error(request, "Review the litter end confirmation before closing this litter.")
            return render(
                request,
                "breeding/litter_end.html",
                {
                    "litter": litter,
                    "form": form,
                    "has_weaned": has_weaned,
                    "registered_pup_count": registered_pup_count,
                },
            )

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

    form = EndLitterForm(litter=litter)
    return render(
        request,
        "breeding/litter_end.html",
        {
            "litter": litter,
            "form": form,
            "has_weaned": has_weaned,
            "registered_pup_count": registered_pup_count,
        },
    )


@authenticated_required
def litter_wean(request: HttpRequest, pk: int) -> HttpResponse:
    litter = get_object_or_404(
        Litter.objects.select_related(
            "breeding",
            "breeding__cage",
            "breeding__male",
            "breeding__female_1",
            "breeding__female_2",
        )
        .prefetch_related("pups")
        .filter(breeding__in=_scoped_breedings(request.user)),
        pk=pk,
    )
    breeding = litter.breeding
    breeding_sire, breeding_dams = _breeding_sire_and_dams(breeding)
    wean_sire = breeding_sire or breeding.male
    possible_dams = breeding_dams or [breeding.female_1]
    primary_dam = possible_dams[0] if possible_dams else None
    breeding_has_trio_dam = len(possible_dams) > 1
    parent_breeding_options = _wean_parent_breeding_queryset(request.user, breeding)
    dam_project, _dam_project_err = _single_project_from_dams(possible_dams)
    sire_project = wean_sire.project if wean_sire and wean_sire.project_id else breeding.male.project
    wean_form_kwargs = {
        "sire_project": sire_project,
        "dam_project": dam_project,
        "sire_strain": wean_sire.strain_line if wean_sire and wean_sire.strain_line_id else None,
        "dam_strain": primary_dam.strain_line if primary_dam and primary_dam.strain_line_id else None,
        "parent_breeding": breeding,
        "parent_breeding_queryset": parent_breeding_options,
        "parent_sire": wean_sire,
        "parent_dams": possible_dams,
    }
    offspring_template_loci = _union_loci_from_strain_lines(
        wean_sire.strain_line if wean_sire else None,
        *[dam.strain_line for dam in possible_dams if dam and dam.strain_line_id],
    )
    ensure_can_edit_mice_projects(request.user, _breeding_member_mice(breeding))
    wean_counts_source = "manual"
    if litter.litter_status in (Litter.LitterStatus.ENDED, Litter.LitterStatus.ARCHIVED):
        messages.error(request, "This litter is closed; you cannot wean additional pups.")
        return redirect("litters:litter_detail", pk=litter.pk)
    if request.method == "POST":
        male_pup_count, female_pup_count = _parse_wean_pup_counts(request.POST)
        number_of_pups = male_pup_count + female_pup_count
        wean_form = WeanLitterForm(
            request.POST,
            pup_male_count=male_pup_count,
            pup_female_count=female_pup_count,
            **wean_form_kwargs,
        )
        cage_project_filter = (request.POST.get("wean_cage_project_filter") or "").strip()
        cage_owner_filter = (request.POST.get("wean_cage_owner_filter") or "").strip()
        refresh_forms = "refresh_forms" in request.POST
        pup_forms = _build_wean_pup_forms(
            male_pup_count,
            female_pup_count,
            request.POST,
            bind=not refresh_forms,
        )

        if refresh_forms:
            return render(
                request,
                "breeding/litter_wean.html",
                _litter_wean_page_context(
                    litter=litter,
                    wean_form=wean_form,
                    pup_forms=pup_forms,
                    offspring_template_loci=offspring_template_loci,
                    breeding_sire=breeding_sire or breeding.male,
                    breeding_dams=breeding_dams,
                    wean_primary_dam=primary_dam,
                    parent_breeding_options=parent_breeding_options,
                    breeding_has_trio_dam=breeding_has_trio_dam,
                    wean_counts_source="manual",
                    cage_project_filter=cage_project_filter,
                    cage_owner_filter=cage_owner_filter,
                ),
            )

        if wean_form.is_valid():
            wean_date = wean_form.cleaned_data["wean_date"]
            max_pups = _litter_wean_max_pup_count(litter)
            if max_pups is not None and number_of_pups > max_pups:
                wean_form.add_error(
                    "female_pup_count",
                    f"Total pups cannot exceed total born ({max_pups}).",
                )

            if _pup_forms_are_valid(pup_forms) and not wean_form.errors:
                uid_list = [form.cleaned_data["mouse_uid"] for form in pup_forms]
                non_blank_uid_list = [uid for uid in uid_list if uid]
                duplicate_in_form = {
                    uid for uid in non_blank_uid_list if non_blank_uid_list.count(uid) > 1
                }
                if duplicate_in_form:
                    wean_form.add_error(
                        None,
                        f"Duplicate mouse_uid in form: {', '.join(sorted(duplicate_in_form))}.",
                    )

                duplicate_conflicts: list[str] = []
                for uid in uid_list:
                    conflict = find_conflicting_mouse(uid)
                    if conflict is not None:
                        duplicate_conflicts.append(
                            f"{uid} (used by #{conflict.pk}, {conflict.get_status_display()})"
                        )
                if duplicate_conflicts:
                    wean_form.add_error(
                        None,
                        "Mouse UID already exists and cannot be reused: "
                        + ", ".join(sorted(duplicate_conflicts))
                        + ".",
                    )

                if not wean_form.errors:
                    parent_source_breeding = wean_form.cleaned_data.get("resolved_parent_breeding") or breeding
                    selected_wean_sire = wean_form.cleaned_data.get("resolved_sire") or wean_sire
                    selected_possible_dams = list(
                        wean_form.cleaned_data.get("resolved_possible_dams") or possible_dams
                    )
                    selected_primary_dam = selected_possible_dams[0] if selected_possible_dams else None
                    dam_for_mouse = selected_primary_dam if len(selected_possible_dams) == 1 else None
                    offspring_template_loci_for_save = _union_loci_from_strain_lines(
                        selected_wean_sire.strain_line if selected_wean_sire else None,
                        *[
                            dam.strain_line
                            for dam in selected_possible_dams
                            if dam is not None and dam.strain_line_id
                        ],
                    )
                    ensure_can_edit_mice_projects(request.user, _breeding_member_mice(parent_source_breeding))
                    male_cage = wean_form.cleaned_data["male_cage"]
                    female_cage = wean_form.cleaned_data["female_cage"]
                    assignment_mode = wean_form.cleaned_data["project_assignment_mode"]
                    inherited_project = None
                    if assignment_mode == WeanLitterForm.ProjectAssignmentMode.SIRE:
                        inherited_project = (
                            selected_wean_sire.project
                            if selected_wean_sire is not None and selected_wean_sire.project_id
                            else None
                        )
                    elif assignment_mode == WeanLitterForm.ProjectAssignmentMode.DAM:
                        inherited_project, project_err = _single_project_from_dams(selected_possible_dams)
                        if project_err:
                            wean_form.add_error("project_assignment_mode", project_err)
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
                    if inherited_project is None and not wean_form.errors:
                        wean_form.add_error("project_assignment_mode", "Could not resolve a project for the pups.")
                if not wean_form.errors:
                    ensure_can_edit_project_data(request.user, inherited_project)
                    pup_strain_line, strain_err = resolve_wean_strain_line(
                        mode=wean_form.cleaned_data["strain_assignment_mode"],
                        new_line_name=wean_form.cleaned_data.get("new_strain_line_name", ""),
                        sire=selected_wean_sire,
                        dam=dam_for_mouse,
                        possible_dams=selected_possible_dams,
                        template_loci=offspring_template_loci_for_save,
                        user=request.user,
                        litter_display=litter.litter_id_display,
                        breeding_code=parent_source_breeding.breeding_code,
                        project=inherited_project,
                    )
                    if strain_err:
                        wean_form.add_error("strain_assignment_mode", strain_err)
                if not wean_form.errors:
                    created_uids: list[str] = []
                    created_auto_cages: list[Cage] = []
                    with transaction.atomic():
                        source_cage = parent_source_breeding.cage if parent_source_breeding.cage_id else None
                        pup_colony = colony_for_project_and_strain(inherited_project, pup_strain_line)
                        cage_by_slot: dict[str, Cage] = {}
                        if (
                            male_pup_count > 0
                            and wean_form.cleaned_data.get("male_cage_assignment_mode")
                            == WeanLitterForm.CageAssignmentMode.AUTO
                        ):
                            male_cage = create_auto_cage(
                                prefix="CAGE-WM",
                                requested_cage_id=wean_form.cleaned_data.get("male_auto_cage_id") or "",
                                cage_type=Cage.CageType.WEANING,
                                purpose=Cage.Purpose.HOLDING,
                                created_date=wean_date,
                                project=inherited_project,
                                colony=pup_colony,
                                source_cage=source_cage,
                                notes=f"Auto-created for male pups weaned from {litter.litter_id_display}.",
                            )
                            created_auto_cages.append(male_cage)
                        if male_pup_count > 0 and male_cage is not None:
                            cage_by_slot[WeanLitterForm.default_cage_slot_for_sex(Mouse.Sex.MALE)] = male_cage
                        if (
                            female_pup_count > 0
                            and wean_form.cleaned_data.get("female_cage_assignment_mode")
                            == WeanLitterForm.CageAssignmentMode.AUTO
                        ):
                            female_cage = create_auto_cage(
                                prefix="CAGE-WF",
                                requested_cage_id=wean_form.cleaned_data.get("female_auto_cage_id") or "",
                                cage_type=Cage.CageType.WEANING,
                                purpose=Cage.Purpose.HOLDING,
                                created_date=wean_date,
                                project=inherited_project,
                                colony=pup_colony,
                                source_cage=source_cage,
                                notes=f"Auto-created for female pups weaned from {litter.litter_id_display}.",
                            )
                            created_auto_cages.append(female_cage)
                        if female_pup_count > 0 and female_cage is not None:
                            cage_by_slot[WeanLitterForm.default_cage_slot_for_sex(Mouse.Sex.FEMALE)] = female_cage
                        for sex, prefix, label in (
                            (Mouse.Sex.MALE, "CAGE-WM", "male"),
                            (Mouse.Sex.FEMALE, "CAGE-WF", "female"),
                        ):
                            for request_row in wean_form.wean_extra_cage_requests.get(sex, []):
                                if request_row.get("mode") == WeanLitterForm.CageAssignmentMode.EXISTING:
                                    extra_cage = request_row.get("cage")
                                    if extra_cage is None:
                                        raise ValueError(
                                            f"No existing {label} cage resolved for extra cage {request_row.get('index')}."
                                        )
                                else:
                                    extra_cage = create_auto_cage(
                                        prefix=prefix,
                                        requested_cage_id=str(request_row.get("cage_id") or ""),
                                        cage_type=Cage.CageType.WEANING,
                                        purpose=Cage.Purpose.HOLDING,
                                        created_date=wean_date,
                                        project=inherited_project,
                                        colony=pup_colony,
                                        source_cage=source_cage,
                                        notes=(
                                            f"Auto-created as additional {label} cage "
                                            f"{request_row.get('index')} for pups weaned from "
                                            f"{litter.litter_id_display}."
                                        ),
                                    )
                                    created_auto_cages.append(extra_cage)
                                cage_by_slot[str(request_row["slot"])] = extra_cage
                        weaned_entries: list[tuple[Mouse, Cage]] = []
                        for index, form in enumerate(pup_forms):
                            pup_sex = form.cleaned_data["sex"]
                            target_slot = (
                                form.cleaned_data.get("cage_slot")
                                or WeanLitterForm.default_cage_slot_for_sex(pup_sex)
                            )
                            target_cage = cage_by_slot.get(target_slot)
                            if target_cage is None:
                                target_cage = cage_by_slot.get(WeanLitterForm.default_cage_slot_for_sex(pup_sex))
                            if target_cage is None:
                                raise ValueError(f"No weaning cage resolved for pup row {index + 1}.")
                            mouse = Mouse.objects.create(
                                mouse_uid=form.cleaned_data["mouse_uid"],
                                sex=form.cleaned_data["sex"],
                                birth_date=litter.birth_date,
                                status=Mouse.Status.ACTIVE,
                                strain_line=pup_strain_line,
                                current_cage=target_cage,
                                sire=selected_wean_sire,
                                dam=dam_for_mouse,
                                project=inherited_project,
                                source_breeding=parent_source_breeding,
                                ear_tag=form.cleaned_data["ear_tag"],
                                coat_color=form.cleaned_data["coat_color"],
                                notes=form.cleaned_data["notes"],
                            )
                            if len(selected_possible_dams) > 1:
                                mouse.possible_dams.set(selected_possible_dams)
                            # Genotype loci = sire ∪ dam only; pup strain line is separate from PCR template.
                            mouse.ensure_template_genotype_components(
                                extra_loci=offspring_template_loci_for_save,
                                include_strain_template=False,
                            )
                            weaned_entries.append((mouse, target_cage))
                            created_uids.append(mouse.mouse_uid)

                        CageMembership.objects.bulk_create(
                            [
                                CageMembership(
                                    mouse=mouse,
                                    cage=cage,
                                    start_date=wean_date,
                                    end_date=None,
                                    is_current=True,
                                    reason="Weaned from litter",
                                    notes="",
                                )
                                for mouse, cage in weaned_entries
                            ]
                        )
                        new_mice = [mouse for mouse, _cage in weaned_entries]

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

                        if parent_source_breeding.pk != litter.breeding_id:
                            litter.breeding = parent_source_breeding
                        litter.wean_date = wean_date
                        if litter.litter_status == Litter.LitterStatus.ACTIVE:
                            litter.litter_status = Litter.LitterStatus.WEANED
                        litter.save(update_fields=["breeding", "wean_date", "litter_status"])

                    messages.success(
                        request,
                        f"Weaned {len(created_uids)} pups: {', '.join(created_uids)}.",
                        )
                    if created_auto_cages:
                        cage_codes = ", ".join(cage.cage_id for cage in created_auto_cages)
                        messages.info(request, f"Created weaning cage(s): {cage_codes}.")
                        for cage in created_auto_cages:
                            log_audit_event(
                                user=request.user,
                                action=AuditLog.Action.CREATE,
                                obj=cage,
                                message=(
                                    f"Auto-created cage {cage.cage_id} during wean of "
                                    f"litter {litter.litter_code or litter.pk}."
                                ),
                            )
                    log_audit_event(
                        user=request.user,
                        action=AuditLog.Action.WEAN,
                        obj=litter,
                        message=(
                            f"Weaned {len(created_uids)} pups from litter {litter.litter_code or litter.pk} "
                            f"into cages "
                            f"{', '.join(sorted({cage.cage_id for _mouse, cage in weaned_entries}))} "
                            f"under project {inherited_project.name} "
                            f"and strain line {pup_strain_line.line_name if pup_strain_line else '—'}: "
                            f"{', '.join(created_uids)}. Parentage source: "
                            f"{parent_source_breeding.breeding_code}; possible dams: "
                            f"{', '.join(dam.mouse_uid for dam in selected_possible_dams) or '—'}."
                        ),
                    )
                    return redirect("litters:litter_detail", pk=litter.pk)
    else:
        initial_male, initial_female, wean_counts_source = _litter_wean_initial_sex_counts(litter)
        initial_rows = _litter_wean_prefill_rows(litter)
        wean_form = WeanLitterForm(
            initial={
                "wean_date": litter.wean_date,
                "male_pup_count": initial_male,
                "female_pup_count": initial_female,
            },
            **wean_form_kwargs,
        )
        pup_forms = _build_wean_pup_forms(
            initial_male,
            initial_female,
            initial_rows=initial_rows,
        )

    context = _litter_wean_page_context(
        litter=litter,
        wean_form=wean_form,
        pup_forms=pup_forms,
        offspring_template_loci=offspring_template_loci,
        breeding_sire=breeding_sire or breeding.male,
        breeding_dams=breeding_dams,
        wean_primary_dam=primary_dam,
        parent_breeding_options=parent_breeding_options,
        breeding_has_trio_dam=breeding_has_trio_dam,
        wean_counts_source=wean_counts_source,
    )
    return render(request, "breeding/litter_wean.html", context)
