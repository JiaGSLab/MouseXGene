import csv
from io import BytesIO

from django.http import HttpRequest, HttpResponse
from django.contrib import messages
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, F, Max, Q
from django.utils import timezone
from openpyxl import Workbook

from .forms import (
    CageForm,
    CageImportForm,
    MouseForm,
    MouseGenotypeComponentFormSet,
    MouseImportForm,
    MoveCageForm,
    StrainLineForm,
)
from .importers import (
    EXPECTED_COLUMNS,
    MOUSE_EXPECTED_COLUMNS,
    MouseImportOptions,
    parse_cage_import,
    parse_mouse_import,
)
from .models import Cage, CageMembership, Mouse, MouseGenotypeComponent, StrainLine
from breeding.models import Litter
from genotypes.models import MouseGenotype
from core.audit import log_audit_event
from core.history import actor_summary_for_audit_entries, audit_entries_for_object, summarize_modelform_changes
from core.models import AuditLog, ImportLog, Project, ProjectMembership
from users.forms import UserImportPrefixForm
from colony.mouse_age import mouse_list_age_band
from users.import_prefix import get_effective_import_prefix
from users.models import UserProfile
from users.permissions import (
    authenticated_required,
    can_import,
    ensure_can_archive_or_change_terminal_status,
    ensure_can_edit_cage,
    ensure_can_edit_project_data,
    ensure_cage_status_change,
    is_admin,
    role_required,
)

LIST_PAGE_SIZES = (25, 50, 100)
LIST_PAGE_DEFAULT = 25
LIST_ALL_RESULTS_MAX = 500


DEFAULT_MOUSE_IMPORT_OPTIONS = MouseImportOptions(
    auto_create_missing_strain_lines=True,
    auto_create_missing_projects=True,
    auto_create_missing_cages=True,
    resolve_pedigree_within_file=True,
)


class MouseImportExecutionError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("Mouse import failed.")
        self.errors = errors


def build_xlsx_response(filename: str, sheet_name: str, headers: list[str], rows: list[list]) -> HttpResponse:
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


def record_import_log(
    *,
    user,
    import_type: str,
    filename: str,
    success: bool,
    created_count: int = 0,
    errors: list[str] | None = None,
) -> None:
    summary = ""
    if errors:
        summary = "; ".join(errors[:8])
        if len(errors) > 8:
            summary = f"{summary}; ... ({len(errors)} total errors)"
    ImportLog.objects.create(
        user=user if getattr(user, "is_authenticated", False) else None,
        import_type=import_type,
        filename=filename[:255],
        success=success,
        created_count=created_count,
        error_summary=summary,
    )


def _scoped_mouse_queryset(user):
    """Lab-wide read: all mice for any authenticated user (edit is enforced per view)."""
    queryset = Mouse.objects.select_related("strain_line", "current_cage", "project")
    if not getattr(user, "is_authenticated", False):
        return queryset.none()
    return queryset


def _scoped_cage_queryset(user):
    if not getattr(user, "is_authenticated", False):
        return Cage.objects.none()
    return Cage.objects.all()


def _pagination_hrefs(request: HttpRequest, page_obj, viewname: str) -> dict[str, str | None]:
    def href(n: int) -> str:
        q = request.GET.copy()
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


def paginate_queryset_for_list(
    request: HttpRequest,
    queryset,
    *,
    viewname: str,
) -> dict:
    """Split queryset into pages or full list (all, only if count ≤ LIST_ALL_RESULTS_MAX)."""
    total = queryset.count()
    raw_per = (request.GET.get("per_page") or "").strip().lower()

    use_all = raw_per == "all" and total <= LIST_ALL_RESULTS_MAX
    if raw_per == "all" and total > LIST_ALL_RESULTS_MAX:
        messages.warning(
            request,
            (
                f"Cannot show all {total} rows at once (limit is {LIST_ALL_RESULTS_MAX}). "
                f"Using {LIST_PAGE_DEFAULT} per page — narrow filters or use export."
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
        last = max(1, paginator.num_pages)
        page_obj = paginator.page(last)
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


def cage_projects_from_mice(mice: list[Mouse]) -> list[dict]:
    """Distinct projects (with owner label and mouse count) for mice currently in a cage."""
    from collections import OrderedDict

    rows: OrderedDict[int, dict] = OrderedDict()
    for m in mice:
        if not getattr(m, "project_id", None):
            continue
        pid = m.project_id
        if pid not in rows:
            rows[pid] = {
                "project": m.project,
                "owner_display": m.project.owner_display,
                "n": 0,
            }
        rows[pid]["n"] += 1
    return list(rows.values())


def get_cages_export_rows(user) -> list[list]:
    return [
        [
            cage.cage_id,
            cage.created_date or "",
            cage.room,
            cage.rack,
            cage.position,
            cage.cage_type,
            cage.purpose,
            cage.status,
            cage.notes,
        ]
        for cage in _scoped_cage_queryset(user).order_by("cage_id")
    ]


def get_cage_inventory_rows(cage: Cage) -> list[list]:
    mice = Mouse.objects.filter(current_cage=cage).select_related("strain_line", "project").order_by("mouse_uid")
    return [
        [
            cage.cage_id,
            mouse.mouse_uid,
            mouse.sex,
            mouse.birth_date or "",
            mouse.status,
            mouse.strain_line.line_name if mouse.strain_line else "",
            mouse.project.name if mouse.project else "",
            mouse.ear_tag,
            mouse.coat_color,
        ]
        for mouse in mice
    ]


def get_mice_export_rows(user) -> list[list]:
    mice = _scoped_mouse_queryset(user).order_by("mouse_uid")
    return [
        [
            mouse.mouse_uid,
            mouse.sex,
            mouse.birth_date or "",
            mouse.death_date or "",
            mouse.status,
            mouse.strain_line.line_name if mouse.strain_line else "",
            mouse.current_cage.cage_id if mouse.current_cage else "",
            mouse.project.name if mouse.project else "",
            mouse.ear_tag,
            mouse.toe_tag,
            mouse.origin,
            mouse.coat_color,
            mouse.notes,
        ]
        for mouse in mice
    ]


def get_mouse_genotype_rows(mouse: Mouse) -> list[list]:
    genotype_records = MouseGenotype.objects.filter(mouse=mouse).order_by("-assay_date", "-created_at")
    return [
        [
            mouse.mouse_uid,
            gt.locus_name,
            gt.allele_1,
            gt.allele_2,
            gt.zygosity_display,
            gt.is_confirmed,
            gt.assay_date or "",
            gt.notes,
        ]
        for gt in genotype_records
    ]


def build_short_genotype_summary(mouse: Mouse) -> str:
    stored = (mouse.genotype_summary or "").strip()
    if stored and stored != "-":
        return mouse.genotype_summary
    genotype_records = list(mouse.genotype_components.select_related("strain_line").all())
    if genotype_records:
        mouse.rebuild_genotype_summary(save=True)
        return (mouse.genotype_summary or "").strip()
    # Backward compatibility fallback to legacy assay-oriented genotype rows.
    legacy_records = list(mouse.genotypes.all())
    parts = []
    for gt in legacy_records[:3]:
        locus = gt.gene.symbol if gt.gene else (gt.locus_name or "locus")
        genotype_part = gt.zygosity_display or "/".join([p for p in [gt.allele_1, gt.allele_2] if p])
        parts.append(f"{locus}:{genotype_part}" if genotype_part else locus)
    if not parts:
        return ""
    summary = ", ".join(parts)
    return f"{summary}..." if len(legacy_records) > 3 else summary


def build_mouse_relation_card(mouse: Mouse) -> dict:
    return {
        "mouse": mouse,
        "genotype_summary": build_short_genotype_summary(mouse),
    }


def build_cage_genotype_overview(mice: list[Mouse], *, sample_size: int = 3) -> str:
    if not mice:
        return "-"
    preview_parts: list[str] = []
    for mouse in mice[:sample_size]:
        g = build_short_genotype_summary(mouse)
        preview_parts.append(f"{mouse.mouse_uid}:{g if g else '—'}")
    if len(mice) > sample_size:
        preview_parts.append(f"... (+{len(mice) - sample_size} more)")
    return "\n".join(preview_parts)


def _normalize_name(value: str | None) -> str:
    return (value or "").strip()


def _build_strain_line_lookup() -> dict[str, StrainLine]:
    lookup: dict[str, StrainLine] = {}
    for line in StrainLine.objects.all():
        for key in {
            line.line_name,
            line.key_name,
            line.display_name,
            line.name,
            line.short_name,
        }:
            text = _normalize_name(key)
            if text and text not in lookup:
                lookup[text] = line
    return lookup


def _execute_two_pass_mouse_import(
    rows: list[dict],
    *,
    options: MouseImportOptions,
    import_date,
    acting_user,
) -> dict[str, int]:
    errors: list[str] = []

    referenced_strain_names = sorted(
        {
            _normalize_name(r.get("strain_line_name"))
            for r in rows
            if _normalize_name(r.get("strain_line_name"))
        }
    )
    referenced_project_names = sorted(
        {_normalize_name(r.get("project_name")) for r in rows if _normalize_name(r.get("project_name"))}
    )
    referenced_cage_ids = sorted(
        {_normalize_name(r.get("current_cage_id")) for r in rows if _normalize_name(r.get("current_cage_id"))}
    )
    referenced_pedigree_uids = sorted(
        {
            _normalize_name(uid)
            for r in rows
            for uid in (r.get("sire_uid"), r.get("dam_uid"))
            if _normalize_name(uid)
        }
    )

    strain_lookup = _build_strain_line_lookup()
    missing_strains = [name for name in referenced_strain_names if name not in strain_lookup]
    if missing_strains and not options.auto_create_missing_strain_lines:
        errors.extend([f"Missing strain_line '{name}' (auto-create disabled)." for name in missing_strains])
    if missing_strains and options.auto_create_missing_strain_lines:
        StrainLine.objects.bulk_create(
            [
                StrainLine(
                    line_name=name,
                    name=name,
                    short_name=name,
                    category=StrainLine.Category.OTHER,
                    notes="Auto-created during mouse import.",
                )
                for name in missing_strains
            ]
        )
        strain_lookup = _build_strain_line_lookup()

    project_lookup = {project.name: project for project in Project.objects.all()}
    missing_projects = [name for name in referenced_project_names if name not in project_lookup]
    if missing_projects and not options.auto_create_missing_projects:
        errors.extend([f"Missing project '{name}' (auto-create disabled)." for name in missing_projects])
    if missing_projects and options.auto_create_missing_projects:
        Project.objects.bulk_create(
            [
                Project(
                    name=name,
                    description="Auto-created during mouse import.",
                    is_active=True,
                    owner=acting_user,
                )
                for name in missing_projects
            ]
        )
        project_lookup = {project.name: project for project in Project.objects.all()}
        if getattr(acting_user, "is_authenticated", False):
            for name in missing_projects:
                project = project_lookup.get(name)
                if project is not None:
                    ProjectMembership.objects.get_or_create(
                        project=project,
                        user=acting_user,
                        defaults={"role": ProjectMembership.Role.MANAGER},
                    )

    for name in referenced_project_names:
        project = project_lookup.get(name)
        if project is None:
            continue
        if not can_edit_project_data(acting_user, project):
            errors.append(
                f"Project '{name}': you do not have permission to create or update mice in this project."
            )

    cage_lookup = {cage.cage_id: cage for cage in Cage.objects.all()}
    missing_cages = [cage_id for cage_id in referenced_cage_ids if cage_id not in cage_lookup]
    if missing_cages and not options.auto_create_missing_cages:
        errors.extend([f"Missing cage '{cage_id}' (auto-create disabled)." for cage_id in missing_cages])
    if missing_cages and options.auto_create_missing_cages:
        Cage.objects.bulk_create(
            [
                Cage(
                    cage_id=cage_id,
                    created_date=import_date,
                    cage_type=Cage.CageType.STANDARD,
                    purpose=Cage.Purpose.HOLDING,
                    status=Cage.Status.ACTIVE,
                    notes="Auto-created during mouse import.",
                )
                for cage_id in missing_cages
            ]
        )
        cage_lookup = {cage.cage_id: cage for cage in Cage.objects.all()}

    if errors:
        raise MouseImportExecutionError(errors)

    # Preserve currently-existing mice for optional pedigree resolution behavior.
    preexisting_mouse_lookup = {
        mouse.mouse_uid: mouse for mouse in Mouse.objects.filter(mouse_uid__in=referenced_pedigree_uids)
    }

    created_mice_by_uid: dict[str, Mouse] = {}
    mice_to_create: list[Mouse] = []
    for row in rows:
        row_number = row["row_number"]
        strain_name = _normalize_name(row.get("strain_line_name"))
        strain_line = strain_lookup.get(strain_name)
        if strain_line is None:
            errors.append(f"Row {row_number}: unresolved strain_line '{strain_name}'.")
            continue
        mice_to_create.append(
            Mouse(
                mouse_uid=row["mouse_uid"],
                sex=row["sex"],
                birth_date=row["birth_date"],
                status=row["status"],
                strain_line=strain_line,
            )
        )

    if errors:
        raise MouseImportExecutionError(errors)

    Mouse.objects.bulk_create(mice_to_create)
    created_mice = list(Mouse.objects.filter(mouse_uid__in=[row["mouse_uid"] for row in rows]))
    created_mice_by_uid = {mouse.mouse_uid: mouse for mouse in created_mice}

    pedigree_lookup = dict(preexisting_mouse_lookup)
    if options.resolve_pedigree_within_file:
        pedigree_lookup.update(created_mice_by_uid)

    mice_with_membership: list[Mouse] = []
    for row in rows:
        row_number = row["row_number"]
        mouse = created_mice_by_uid.get(row["mouse_uid"])
        if mouse is None:
            errors.append(f"Row {row_number}: failed to materialize mouse '{row['mouse_uid']}'.")
            continue

        current_cage = None
        cage_id = _normalize_name(row.get("current_cage_id"))
        if cage_id:
            current_cage = cage_lookup.get(cage_id)
            if current_cage is None:
                errors.append(f"Row {row_number}: unresolved current_cage '{cage_id}'.")

        project = None
        project_name = _normalize_name(row.get("project_name"))
        if project_name:
            project = project_lookup.get(project_name)
            if project is None:
                errors.append(f"Row {row_number}: unresolved project '{project_name}'.")
            elif not can_edit_project_data(acting_user, project):
                errors.append(f"Row {row_number}: project '{project_name}': you do not have edit permission.")
        else:
            errors.append(f"Row {row_number}: project is required for ownership control.")

        sire = None
        sire_uid = _normalize_name(row.get("sire_uid"))
        if sire_uid:
            sire = pedigree_lookup.get(sire_uid)
            if sire is None:
                errors.append(
                    f"Row {row_number}: unresolved sire '{sire_uid}'. "
                    "Enable resolve_pedigree_within_file or include an existing founder."
                )

        dam = None
        dam_uid = _normalize_name(row.get("dam_uid"))
        if dam_uid:
            dam = pedigree_lookup.get(dam_uid)
            if dam is None:
                errors.append(
                    f"Row {row_number}: unresolved dam '{dam_uid}'. "
                    "Enable resolve_pedigree_within_file or include an existing founder."
                )

        mouse.current_cage = current_cage
        mouse.project = project
        mouse.ear_tag = row.get("ear_tag", "")
        mouse.toe_tag = row.get("toe_tag", "")
        mouse.origin = row.get("origin", "")
        mouse.coat_color = row.get("coat_color", "")
        mouse.notes = row.get("notes", "")
        mouse.sire = sire
        mouse.dam = dam
        mouse.save()
        if current_cage:
            mice_with_membership.append(mouse)

    if errors:
        raise MouseImportExecutionError(errors)

    CageMembership.objects.bulk_create(
        [
            CageMembership(
                mouse=mouse,
                cage=mouse.current_cage,
                start_date=mouse.birth_date or import_date,
                end_date=None,
                is_current=True,
                reason="Imported with initial cage assignment",
                notes="",
            )
            for mouse in mice_with_membership
            if mouse.current_cage_id
        ]
    )

    # Pass 3: import genotype slots embedded in each mouse row.
    genotype_to_create: list[MouseGenotype] = []
    genotype_to_update: list[MouseGenotype] = []
    existing_by_mouse_locus = {
        (gt.mouse_id, gt.locus_name): gt
        for gt in MouseGenotype.objects.filter(mouse_id__in=[m.id for m in created_mice_by_uid.values()])
    }
    for row in rows:
        mouse = created_mice_by_uid.get(row["mouse_uid"])
        if mouse is None:
            continue
        for slot in row.get("genotype_slots", []):
            key = (mouse.id, slot["locus_name"])
            existing = existing_by_mouse_locus.get(key)
            if existing is None:
                obj = MouseGenotype(
                    mouse=mouse,
                    gene=None,
                    locus_name=slot["locus_name"],
                    allele_1=slot["allele_1"],
                    allele_2=slot["allele_2"],
                    zygosity_display=slot["zygosity_display"],
                    is_confirmed=slot["is_confirmed"],
                    assay_date=slot["assay_date"],
                    notes=slot["notes"],
                )
                genotype_to_create.append(obj)
            else:
                existing.allele_1 = slot["allele_1"]
                existing.allele_2 = slot["allele_2"]
                existing.zygosity_display = slot["zygosity_display"]
                existing.is_confirmed = slot["is_confirmed"]
                existing.assay_date = slot["assay_date"]
                existing.notes = slot["notes"]
                genotype_to_update.append(existing)

    if genotype_to_create:
        MouseGenotype.objects.bulk_create(genotype_to_create)
    if genotype_to_update:
        MouseGenotype.objects.bulk_update(
            genotype_to_update,
            ["allele_1", "allele_2", "zygosity_display", "is_confirmed", "assay_date", "notes"],
        )

    return {
        "created_mice": len(created_mice_by_uid),
        "auto_created_strain_lines": len(missing_strains),
        "auto_created_projects": len(missing_projects),
        "auto_created_cages": len(missing_cages),
        "genotype_rows_created": len(genotype_to_create),
        "genotype_rows_updated": len(genotype_to_update),
    }


@authenticated_required
def mouse_genotype_components_edit(request: HttpRequest, pk: int) -> HttpResponse:
    mouse = get_object_or_404(Mouse.objects.select_related("strain_line", "project"), pk=pk)
    ensure_can_edit_project_data(request.user, mouse.project)
    if request.method == "POST":
        formset = MouseGenotypeComponentFormSet(request.POST, instance=mouse)
        if formset.is_valid():
            formset.save()
            mouse.rebuild_genotype_summary(save=True)
            log_audit_event(
                user=request.user,
                action=AuditLog.Action.UPDATE,
                obj=mouse,
                message="Updated genotype components (structured slots).",
            )
            messages.success(request, "Genotype components updated.")
            return redirect("mice:mouse_detail", pk=mouse.pk)
    else:
        formset = MouseGenotypeComponentFormSet(instance=mouse)

    return render(
        request,
        "colony/mouse_genotype_components_form.html",
        {"mouse": mouse, "formset": formset},
    )


@authenticated_required
def cage_list(request: HttpRequest) -> HttpResponse:
    q = (request.GET.get("q") or "").strip()
    room = (request.GET.get("room") or "").strip()
    rack = (request.GET.get("rack") or "").strip()
    cage_type = (request.GET.get("cage_type") or "").strip()
    purpose = (request.GET.get("purpose") or "").strip()
    status = (request.GET.get("status") or "").strip()
    is_empty = (request.GET.get("is_empty") or "").strip()
    include_inactive = (request.GET.get("include_inactive") or "").strip()

    cages = _scoped_cage_queryset(request.user)
    if include_inactive != "yes":
        cages = cages.filter(status=Cage.Status.ACTIVE)
    if q:
        cages = cages.filter(cage_id__icontains=q)
    if room:
        cages = cages.filter(room=room)
    if rack:
        cages = cages.filter(rack=rack)
    if cage_type:
        cages = cages.filter(cage_type=cage_type)
    if purpose:
        cages = cages.filter(purpose=purpose)
    if status:
        cages = cages.filter(status=status)
    if is_empty == "yes":
        cages = cages.filter(current_mice__isnull=True)
    elif is_empty == "no":
        cages = cages.filter(current_mice__isnull=False)

    cages = (
        cages.distinct()
        .order_by("cage_id")
        .prefetch_related(
            "current_mice__strain_line",
            "current_mice__project",
            "current_mice__project__owner",
            "current_mice__project__owner__profile",
            "current_mice__genotype_components__strain_line",
            "current_mice__genotypes__gene",
        )
    )
    page_ctx = paginate_queryset_for_list(request, cages, viewname="colony:cage_list")
    cages_page = list(page_ctx.pop("items"))
    for cage in cages_page:
        cage_mice = list(cage.current_mice.all().order_by("mouse_uid"))
        cage.current_mouse_count = len(cage_mice)
        cage.genotype_overview = build_cage_genotype_overview(cage_mice)
        cage.project_rows = cage_projects_from_mice(cage_mice)
    context = {
        "cages": cages_page,
        "q": q,
        "room": room,
        "rack": rack,
        "cage_type": cage_type,
        "purpose": purpose,
        "status": status,
        "is_empty": is_empty,
        "include_inactive": include_inactive,
        "room_options": Cage.objects.exclude(room="").values_list("room", flat=True).distinct().order_by("room"),
        "rack_options": Cage.objects.exclude(rack="").values_list("rack", flat=True).distinct().order_by("rack"),
        "cage_type_options": Cage.CageType.choices,
        "purpose_options": Cage.Purpose.choices,
        "status_options": Cage.Status.choices,
        "list_all_max": LIST_ALL_RESULTS_MAX,
        **page_ctx,
    }
    return render(request, "colony/cage_list.html", context)


@authenticated_required
def strain_line_list(request: HttpRequest) -> HttpResponse:
    q = (request.GET.get("q") or "").strip()
    active = (request.GET.get("active") or "yes").strip()
    lines = StrainLine.objects.all()
    if q:
        lines = lines.filter(
            Q(name__icontains=q)
            | Q(short_name__icontains=q)
            | Q(gene_or_locus__icontains=q)
            | Q(category__icontains=q)
            | Q(line_name__icontains=q)
            | Q(display_name__icontains=q)
            | Q(key_name__icontains=q)
            | Q(notes__icontains=q)
        )
    if active == "yes":
        lines = lines.filter(is_active=True)
    elif active == "no":
        lines = lines.filter(is_active=False)
    lines = lines.annotate(
        active_mice_count=Count("mice", filter=Q(mice__status=Mouse.Status.ACTIVE), distinct=True),
        active_cages_count=Count(
            "mice__current_cage",
            filter=Q(mice__status=Mouse.Status.ACTIVE, mice__current_cage__isnull=False),
            distinct=True,
        ),
        active_breedings_count=Count(
            "mice__maternal_breedings_primary",
            filter=Q(mice__maternal_breedings_primary__active=True),
            distinct=True,
        )
        + Count(
            "mice__sired_breedings",
            filter=Q(mice__sired_breedings__active=True),
            distinct=True,
        ),
        active_litters_count=Count(
            "mice__maternal_breedings_primary__litters",
            filter=Q(
                mice__maternal_breedings_primary__litters__litter_status__in=[
                    Litter.LitterStatus.ACTIVE,
                    Litter.LitterStatus.WEANED,
                    Litter.LitterStatus.TAIL_TAGGED,
                ]
            ),
            distinct=True,
        ),
    )
    context = {
        "lines": lines.order_by("name", "line_name"),
        "q": q,
        "active": active,
    }
    return render(request, "colony/strain_line_list.html", context)


@authenticated_required
def strain_line_detail(request: HttpRequest, pk: int) -> HttpResponse:
    line = get_object_or_404(
        StrainLine.objects.annotate(
            active_mice_count=Count("mice", filter=Q(mice__status=Mouse.Status.ACTIVE), distinct=True),
            active_cages_count=Count(
                "mice__current_cage",
                filter=Q(mice__status=Mouse.Status.ACTIVE, mice__current_cage__isnull=False),
                distinct=True,
            ),
            active_breedings_count=Count(
                "mice__maternal_breedings_primary",
                filter=Q(mice__maternal_breedings_primary__active=True),
                distinct=True,
            )
            + Count(
                "mice__sired_breedings",
                filter=Q(mice__sired_breedings__active=True),
                distinct=True,
            ),
            active_litters_count=Count(
                "mice__maternal_breedings_primary__litters",
                filter=Q(
                    mice__maternal_breedings_primary__litters__litter_status__in=[
                        Litter.LitterStatus.ACTIVE,
                        Litter.LitterStatus.WEANED,
                        Litter.LitterStatus.TAIL_TAGGED,
                    ]
                ),
                distinct=True,
            ),
        ),
        pk=pk,
    )
    audit_entries = audit_entries_for_object("StrainLine", line.pk)
    actors = actor_summary_for_audit_entries(audit_entries)
    return render(
        request,
        "colony/strain_line_detail.html",
        {"line": line, "audit_entries": audit_entries, **actors},
    )


@authenticated_required
def strain_line_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = StrainLineForm(request.POST)
        if form.is_valid():
            line = form.save()
            log_audit_event(
                user=request.user,
                action=AuditLog.Action.CREATE,
                obj=line,
                message=f"Created strain line {line.line_name}.",
            )
            messages.success(request, "Strain line created.")
            return redirect("colony:strain_line_detail", pk=line.pk)
    else:
        form = StrainLineForm()
    return render(
        request,
        "colony/strain_line_form.html",
        {
            "form": form,
            "page_title": "Create Strain Line",
            "submit_label": "Save Strain Line",
        },
    )


@authenticated_required
def strain_line_edit(request: HttpRequest, pk: int) -> HttpResponse:
    line = get_object_or_404(StrainLine, pk=pk)
    previous_active = line.is_active
    if request.method == "POST":
        form = StrainLineForm(request.POST, instance=line)
        if form.is_valid():
            if form.cleaned_data.get("is_active") != previous_active and not can_import(request.user):
                raise PermissionDenied("Only managers or admins can archive/deactivate strain lines.")
            msg = summarize_modelform_changes(form)
            line = form.save()
            log_audit_event(
                user=request.user,
                action=AuditLog.Action.UPDATE,
                obj=line,
                message=msg[:4000],
            )
            messages.success(request, "Strain line updated.")
            return redirect("colony:strain_line_detail", pk=line.pk)
    else:
        form = StrainLineForm(instance=line)
    return render(
        request,
        "colony/strain_line_form.html",
        {
            "form": form,
            "page_title": f"Edit Strain Line {line.line_name}",
            "submit_label": "Save Changes",
        },
    )


@authenticated_required
def cage_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = CageForm(request.POST)
        if form.is_valid():
            cage = form.save()
            log_audit_event(
                user=request.user,
                action=AuditLog.Action.CREATE,
                obj=cage,
                message=f"Created cage {cage.cage_id}.",
            )
            return redirect("colony:cage_detail", pk=cage.pk)
    else:
        form = CageForm()

    context = {
        "form": form,
        "page_title": "Create Cage",
        "submit_label": "Save Cage",
        "cancel_url": "colony:cage_list",
    }
    return render(request, "colony/cage_form.html", context)


@authenticated_required
def cage_edit(request: HttpRequest, pk: int) -> HttpResponse:
    cage = get_object_or_404(_scoped_cage_queryset(request.user), pk=pk)
    ensure_can_edit_cage(request.user, cage)
    previous_status = cage.status
    if request.method == "POST":
        form = CageForm(request.POST, instance=cage)
        if form.is_valid():
            new_status = form.cleaned_data.get("status")
            if new_status != previous_status:
                ensure_cage_status_change(request.user, cage, previous_status, new_status)
            msg = summarize_modelform_changes(form)
            cage = form.save()
            log_audit_event(
                user=request.user,
                action=AuditLog.Action.UPDATE,
                obj=cage,
                message=msg[:4000],
            )
            return redirect("colony:cage_detail", pk=cage.pk)
    else:
        form = CageForm(instance=cage)

    context = {
        "form": form,
        "page_title": f"Edit Cage {cage.cage_id}",
        "submit_label": "Save Changes",
        "cancel_url": "colony:cage_detail",
        "cancel_kwargs": {"pk": cage.pk},
    }
    return render(request, "colony/cage_form.html", context)


@role_required(can_import)
def cage_import(request: HttpRequest) -> HttpResponse:
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    row_errors: list[str] = []
    prefix_form = UserImportPrefixForm(instance=profile)
    form = CageImportForm(user=request.user)

    if request.method == "POST" and request.POST.get("save_import_prefix"):
        prefix_form = UserImportPrefixForm(request.POST, instance=profile)
        if prefix_form.is_valid():
            prefix_form.save()
            messages.success(request, "Import ID prefix saved.")
            return redirect("colony:cage_import")
        form = CageImportForm(user=request.user)
    elif request.method == "POST":
        form = CageImportForm(request.POST, request.FILES, user=request.user)
        prefix_form = UserImportPrefixForm(instance=profile)
        if form.is_valid():
            uploaded_file = form.cleaned_data["data_file"]
            upload_name = uploaded_file.name or ""
            id_prefix = None
            if form.cleaned_data.get("apply_import_prefix"):
                id_prefix = get_effective_import_prefix(request.user)
            result = parse_cage_import(uploaded_file, id_prefix=id_prefix)
            if result.errors:
                row_errors = result.errors
                record_import_log(
                    user=request.user,
                    import_type=ImportLog.ImportType.CAGE,
                    filename=upload_name,
                    success=False,
                    created_count=0,
                    errors=result.errors,
                )
            else:
                with transaction.atomic():
                    Cage.objects.bulk_create([Cage(**row) for row in result.rows])
                log_audit_event(
                    user=request.user,
                    action=AuditLog.Action.IMPORT,
                    message=f"Imported {len(result.rows)} cages via file upload.",
                    object_type="Cage",
                    object_id=str(len(result.rows)),
                    object_repr="Bulk Cage Import",
                )
                record_import_log(
                    user=request.user,
                    import_type=ImportLog.ImportType.CAGE,
                    filename=upload_name,
                    success=True,
                    created_count=len(result.rows),
                    errors=[],
                )
                messages.success(request, f"Successfully imported {len(result.rows)} cages.")
                return redirect("colony:cage_list")

    context = {
        "form": form,
        "prefix_form": prefix_form,
        "row_errors": row_errors,
        "expected_columns": EXPECTED_COLUMNS,
        "import_prefix_hint": get_effective_import_prefix(request.user),
    }
    return render(request, "colony/cage_import.html", context)


@role_required(can_import)
def cage_import_template(request: HttpRequest) -> HttpResponse:
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="cage_import_template.csv"'
    writer = csv.writer(response)
    writer.writerow(EXPECTED_COLUMNS)
    writer.writerow(
        [
            "C001",
            "2026-04-10",
            "Room-A",
            "Rack-1",
            "A1",
            Cage.CageType.STANDARD,
            Cage.Purpose.HOLDING,
            Cage.Status.ACTIVE,
            "Example note",
        ]
    )
    return response


@role_required(can_import)
def cage_import_template_xlsx(request: HttpRequest) -> HttpResponse:
    rows = [
        [
            "C001",
            "2026-04-10",
            "Room-A",
            "Rack-1",
            "A1",
            Cage.CageType.STANDARD,
            Cage.Purpose.HOLDING,
            Cage.Status.ACTIVE,
            "Example note",
        ]
    ]
    return build_xlsx_response("cage_import_template.xlsx", "CageTemplate", EXPECTED_COLUMNS, rows)


@authenticated_required
def cage_detail(request: HttpRequest, pk: int) -> HttpResponse:
    cage = get_object_or_404(_scoped_cage_queryset(request.user), pk=pk)
    current_mice = list(
        _scoped_mouse_queryset(request.user)
        .filter(current_cage=cage)
        .select_related("project", "project__owner", "project__owner__profile")
        .prefetch_related("genotype_components__strain_line", "genotypes__gene")
        .order_by("mouse_uid")
    )
    current_mouse_rows = [
        {
            "mouse": mouse,
            "genotype_summary": build_short_genotype_summary(mouse),
        }
        for mouse in current_mice
    ]
    active_litters = (
        Litter.objects.select_related("breeding")
        .filter(
            Q(breeding__cage=cage)
            | Q(breeding__female_1__current_cage=cage)
            | Q(breeding__male__current_cage=cage),
            litter_status__in=[
                Litter.LitterStatus.ACTIVE,
                Litter.LitterStatus.WEANED,
                Litter.LitterStatus.TAIL_TAGGED,
            ],
        )
        .distinct()
        .order_by("-birth_date")[:8]
    )
    latest_setup = (
        CageMembership.objects.filter(cage=cage, is_current=True).aggregate(setup=Max("start_date")).get("setup")
    )
    cage_project_rows = cage_projects_from_mice(current_mice)
    audit_entries = audit_entries_for_object("Cage", cage.pk)
    actors = actor_summary_for_audit_entries(audit_entries)
    context = {
        "cage": cage,
        "current_mice": current_mice,
        "current_mouse_rows": current_mouse_rows,
        "cage_genotype_overview": build_cage_genotype_overview(current_mice),
        "active_litters": active_litters,
        "current_mouse_count": len(current_mice),
        "cage_setup_date": latest_setup or cage.created_date,
        "cage_project_rows": cage_project_rows,
        "audit_entries": audit_entries,
        **actors,
    }
    return render(request, "colony/cage_detail.html", context)


@authenticated_required
def cage_history(request: HttpRequest, pk: int) -> HttpResponse:
    cage = get_object_or_404(_scoped_cage_queryset(request.user), pk=pk)
    memberships = (
        CageMembership.objects.filter(cage=cage)
        .select_related("mouse")
        .order_by("-start_date", "-created_at")
    )
    context = {
        "cage": cage,
        "memberships": memberships,
    }
    return render(request, "colony/cage_history.html", context)


@authenticated_required
def cage_print(request: HttpRequest, pk: int) -> HttpResponse:
    cage = get_object_or_404(_scoped_cage_queryset(request.user), pk=pk)
    current_mice = (
        _scoped_mouse_queryset(request.user).filter(current_cage=cage)
        .select_related("strain_line")
        .prefetch_related("genotypes__gene")
        .order_by("mouse_uid")
    )
    mice_rows = [
        {
            "mouse": mouse,
            "genotype_summary": build_short_genotype_summary(mouse),
        }
        for mouse in current_mice
    ]
    context = {
        "cage": cage,
        "mice_rows": mice_rows,
    }
    return render(request, "colony/cage_print.html", context)


@authenticated_required
def cages_export(request: HttpRequest) -> HttpResponse:
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="cages_export.csv"'
    writer = csv.writer(response)
    writer.writerow(
        [
            "cage_id",
            "created_date",
            "room",
            "rack",
            "position",
            "cage_type",
            "purpose",
            "status",
            "notes",
        ]
    )
    for row in get_cages_export_rows(request.user):
        writer.writerow(row)
    return response


@authenticated_required
def cage_inventory_export(request: HttpRequest, pk: int) -> HttpResponse:
    cage = get_object_or_404(_scoped_cage_queryset(request.user), pk=pk)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="cage_{cage.cage_id}_inventory.csv"'
    writer = csv.writer(response)
    writer.writerow(
        [
            "cage_id",
            "mouse_uid",
            "sex",
            "birth_date",
            "status",
            "strain_line",
            "project",
            "ear_tag",
            "coat_color",
        ]
    )
    for row in get_cage_inventory_rows(cage):
        writer.writerow(row)
    return response


@authenticated_required
def cages_export_xlsx(request: HttpRequest) -> HttpResponse:
    headers = ["cage_id", "created_date", "room", "rack", "position", "cage_type", "purpose", "status", "notes"]
    rows = get_cages_export_rows(request.user)
    return build_xlsx_response("cages.xlsx", "Cages", headers, rows)


@authenticated_required
def cage_inventory_export_xlsx(request: HttpRequest, pk: int) -> HttpResponse:
    cage = get_object_or_404(_scoped_cage_queryset(request.user), pk=pk)
    headers = [
        "cage_id",
        "mouse_uid",
        "sex",
        "birth_date",
        "status",
        "strain_line",
        "project",
        "ear_tag",
        "coat_color",
    ]
    rows = get_cage_inventory_rows(cage)
    return build_xlsx_response(f"cage_{cage.cage_id}_inventory.xlsx", "CageInventory", headers, rows)


def _mouse_list_age_sort_querystring(request: HttpRequest, new_age_sort: str | None) -> str:
    q = request.GET.copy()
    if new_age_sort in ("old", "young"):
        q["age_sort"] = new_age_sort
    else:
        q.pop("age_sort", None)
    return q.urlencode()


@authenticated_required
def mouse_list(request: HttpRequest) -> HttpResponse:
    query = (request.GET.get("q") or "").strip()
    sex = (request.GET.get("sex") or "").strip()
    status = (request.GET.get("status") or "").strip()
    strain_line = (request.GET.get("strain_line") or request.GET.get("strain_line_id") or "").strip()
    current_cage = (request.GET.get("current_cage") or request.GET.get("cage_id") or "").strip()
    project = (request.GET.get("project") or request.GET.get("project_id") or "").strip()
    include_inactive = (request.GET.get("include_inactive") or "").strip()
    age_sort = (request.GET.get("age_sort") or "").strip()
    if age_sort not in ("", "old", "young"):
        age_sort = ""

    mice = _scoped_mouse_queryset(request.user).select_related(
        "project__owner",
        "project__owner__profile",
    )
    if include_inactive != "yes":
        mice = mice.filter(status=Mouse.Status.ACTIVE)

    if query:
        mice = mice.filter(
            Q(mouse_uid__icontains=query)
            | Q(genotype_summary__icontains=query)
            | Q(ear_tag__icontains=query)
            | Q(toe_tag__icontains=query)
            | Q(origin__icontains=query)
        )
    if sex:
        mice = mice.filter(sex=sex)
    if status:
        mice = mice.filter(status=status)
    if strain_line:
        mice = mice.filter(strain_line_id=strain_line)
    if current_cage:
        mice = mice.filter(current_cage_id=current_cage)
    if project:
        mice = mice.filter(project_id=project)

    if age_sort == "old":
        mice = mice.order_by(F("birth_date").asc(nulls_last=True), "mouse_uid")
    elif age_sort == "young":
        mice = mice.order_by(F("birth_date").desc(nulls_last=True), "mouse_uid")
    else:
        mice = mice.order_by("-birth_date", "mouse_uid")

    mice = mice.prefetch_related(
        "genotype_components__strain_line",
        "genotypes__gene",
    )

    if age_sort == "":
        next_age_sort: str | None = "old"
    elif age_sort == "old":
        next_age_sort = "young"
    else:
        next_age_sort = None
    age_sort_qs = _mouse_list_age_sort_querystring(request, next_age_sort)
    age_sort_href = reverse("mice:mouse_list")
    if age_sort_qs:
        age_sort_href = f"{age_sort_href}?{age_sort_qs}"

    current_cage_options = Cage.objects.filter(current_mice__in=mice).distinct().order_by("cage_id")
    project_options = (
        Project.objects.filter(id__in=mice.values_list("project_id", flat=True)).distinct().order_by("name")
    )

    page_ctx = paginate_queryset_for_list(request, mice, viewname="mice:mouse_list")
    mice_page = list(page_ctx.pop("items"))

    context = {
        "mice": mice_page,
        "query": query,
        "sex": sex,
        "status": status,
        "strain_line": strain_line,
        "current_cage": current_cage,
        "project": project,
        "include_inactive": include_inactive,
        "age_sort": age_sort,
        "age_sort_href": age_sort_href,
        "sex_options": Mouse.Sex.choices,
        "status_options": Mouse.Status.choices,
        "strain_line_options": Mouse._meta.get_field("strain_line").related_model.objects.order_by("line_name"),
        "current_cage_options": current_cage_options,
        "project_options": project_options,
        "list_all_max": LIST_ALL_RESULTS_MAX,
        **page_ctx,
    }
    today = timezone.localdate()
    for m in mice_page:
        m.genotype_summary = build_short_genotype_summary(m)
        m.list_age_band = mouse_list_age_band(m.birth_date, today)
        if m.birth_date:
            age_days = (today - m.birth_date).days
            if age_days >= 0:
                age_weeks, remaining_days = divmod(age_days, 7)
                m.age_display = f"{age_weeks}w {remaining_days}d"
            else:
                m.age_display = "-"
        else:
            m.age_display = "-"
    return render(request, "colony/mouse_list.html", context)


@role_required(can_import)
def mouse_import(request: HttpRequest) -> HttpResponse:
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    row_errors: list[str] = []
    prefix_form = UserImportPrefixForm(instance=profile)
    form = MouseImportForm(user=request.user)

    if request.method == "POST" and request.POST.get("save_import_prefix"):
        prefix_form = UserImportPrefixForm(request.POST, instance=profile)
        if prefix_form.is_valid():
            prefix_form.save()
            messages.success(request, "Import ID prefix saved.")
            return redirect("mice:mouse_import")
        form = MouseImportForm(user=request.user)
    elif request.method == "POST":
        form = MouseImportForm(request.POST, request.FILES, user=request.user)
        prefix_form = UserImportPrefixForm(instance=profile)
        if form.is_valid():
            uploaded_file = form.cleaned_data["data_file"]
            upload_name = uploaded_file.name or ""
            import_options = MouseImportOptions(
                auto_create_missing_strain_lines=form.cleaned_data["auto_create_missing_strain_lines"],
                auto_create_missing_projects=form.cleaned_data["auto_create_missing_projects"],
                auto_create_missing_cages=form.cleaned_data["auto_create_missing_cages"],
                resolve_pedigree_within_file=form.cleaned_data["resolve_pedigree_within_file"],
            )
            id_prefix = None
            if form.cleaned_data.get("apply_import_prefix"):
                id_prefix = get_effective_import_prefix(request.user)
            result = parse_mouse_import(uploaded_file, id_prefix=id_prefix)
            if result.errors:
                row_errors = result.errors
                record_import_log(
                    user=request.user,
                    import_type=ImportLog.ImportType.MOUSE,
                    filename=upload_name,
                    success=False,
                    created_count=0,
                    errors=result.errors,
                )
            else:
                try:
                    with transaction.atomic():
                        stats = _execute_two_pass_mouse_import(
                            result.rows,
                            options=import_options,
                            import_date=timezone.localdate(),
                            acting_user=request.user,
                        )
                except MouseImportExecutionError as exc:
                    row_errors = exc.errors
                    record_import_log(
                        user=request.user,
                        import_type=ImportLog.ImportType.MOUSE,
                        filename=upload_name,
                        success=False,
                        created_count=0,
                        errors=row_errors,
                    )
                else:
                    log_audit_event(
                        user=request.user,
                        action=AuditLog.Action.IMPORT,
                        message=(
                            f"Imported {stats['created_mice']} mice via file upload "
                            f"(auto-created: {stats['auto_created_strain_lines']} strain lines, "
                            f"{stats['auto_created_projects']} projects, "
                            f"{stats['auto_created_cages']} cages; "
                            f"genotypes: +{stats['genotype_rows_created']} / ~{stats['genotype_rows_updated']})."
                        ),
                        object_type="Mouse",
                        object_id=str(stats["created_mice"]),
                        object_repr="Bulk Mouse Import",
                    )
                    record_import_log(
                        user=request.user,
                        import_type=ImportLog.ImportType.MOUSE,
                        filename=upload_name,
                        success=True,
                        created_count=stats["created_mice"],
                        errors=[],
                    )
                    messages.success(
                        request,
                        (
                            f"Successfully imported {stats['created_mice']} mice "
                            f"(auto-created: {stats['auto_created_strain_lines']} strain lines, "
                            f"{stats['auto_created_projects']} projects, "
                            f"{stats['auto_created_cages']} cages; "
                            f"genotypes created {stats['genotype_rows_created']}, "
                            f"updated {stats['genotype_rows_updated']})."
                        ),
                    )
                    return redirect("mice:mouse_list")

    context = {
        "form": form,
        "prefix_form": prefix_form,
        "row_errors": row_errors,
        "expected_columns": MOUSE_EXPECTED_COLUMNS,
        "import_prefix_hint": get_effective_import_prefix(request.user),
    }
    return render(request, "colony/mouse_import.html", context)


@role_required(can_import)
def mouse_import_template(request: HttpRequest) -> HttpResponse:
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="mouse_import_template.csv"'
    writer = csv.writer(response)
    writer.writerow(MOUSE_EXPECTED_COLUMNS)
    writer.writerow(
        [
            "M001",
            Mouse.Sex.FEMALE,
            "2026-01-15",
            Mouse.Status.ACTIVE,
            "Tet2 flox",
            "C001",
            "Inflammation Study",
            "ET-001",
            "TT-001",
            "In-house breeding",
            "black",
            "Example imported mouse",
            "",
            "",
            "Tet2",
            "fl",
            "fl",
            "fl/fl",
            "yes",
            "2026-04-14",
            "Validated by PCR",
            "Lyz2-CreERT2",
            "Cre",
            "+",
            "Cre/+",
            "no",
            "",
            "Pending confirmation",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        ]
    )
    return response


@role_required(can_import)
def mouse_import_template_xlsx(request: HttpRequest) -> HttpResponse:
    rows = [
        [
            "M001",
            Mouse.Sex.FEMALE,
            "2026-01-15",
            Mouse.Status.ACTIVE,
            "Tet2 flox",
            "C001",
            "Inflammation Study",
            "ET-001",
            "TT-001",
            "In-house breeding",
            "black",
            "Example imported mouse",
            "",
            "",
            "Tet2",
            "fl",
            "fl",
            "fl/fl",
            "yes",
            "2026-04-14",
            "Validated by PCR",
            "Lyz2-CreERT2",
            "Cre",
            "+",
            "Cre/+",
            "no",
            "",
            "Pending confirmation",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        ]
    ]
    return build_xlsx_response("mouse_import_template.xlsx", "MouseTemplate", MOUSE_EXPECTED_COLUMNS, rows)


@authenticated_required
def mice_export(request: HttpRequest) -> HttpResponse:
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="mice_export.csv"'
    writer = csv.writer(response)
    writer.writerow(
        [
            "mouse_uid",
            "sex",
            "birth_date",
            "death_date",
            "status",
            "strain_line",
            "current_cage",
            "project",
            "ear_tag",
            "toe_tag",
            "origin",
            "coat_color",
            "notes",
        ]
    )
    for row in get_mice_export_rows(request.user):
        writer.writerow(row)
    return response


@authenticated_required
def mice_export_xlsx(request: HttpRequest) -> HttpResponse:
    headers = [
        "mouse_uid",
        "sex",
        "birth_date",
        "death_date",
        "status",
        "strain_line",
        "current_cage",
        "project",
        "ear_tag",
        "toe_tag",
        "origin",
        "coat_color",
        "notes",
    ]
    rows = get_mice_export_rows(request.user)
    return build_xlsx_response("mice.xlsx", "Mice", headers, rows)


@authenticated_required
def mouse_detail(request: HttpRequest, pk: int) -> HttpResponse:
    mouse = get_object_or_404(
        _scoped_mouse_queryset(request.user).select_related(
            "sire",
            "dam",
            "project",
            "project__owner",
            "project__owner__profile",
        ),
        pk=pk,
    )
    genotype_records = MouseGenotype.objects.select_related("gene").filter(mouse=mouse)
    genotype_components = MouseGenotypeComponent.objects.select_related("strain_line").filter(mouse=mouse)
    cage_history = mouse.cage_memberships.select_related("cage").all()
    offspring = (
        _scoped_mouse_queryset(request.user).filter(Q(sire=mouse) | Q(dam=mouse))
        .select_related("current_cage")
        .prefetch_related("genotypes__gene")
        .distinct()
        .order_by("mouse_uid")
    )
    littermates = Mouse.objects.none()
    if mouse.sire_id and mouse.dam_id:
        littermates = (
            _scoped_mouse_queryset(request.user).filter(sire_id=mouse.sire_id, dam_id=mouse.dam_id)
            .exclude(pk=mouse.pk)
            .select_related("current_cage")
            .prefetch_related("genotypes__gene")
            .order_by("mouse_uid")
        )

    mouse_audit_entries = audit_entries_for_object("Mouse", mouse.pk)
    actors = actor_summary_for_audit_entries(mouse_audit_entries)
    context = {
        "mouse": mouse,
        "genotype_records": genotype_records,
        "genotype_components": genotype_components,
        "genotype_summary": build_short_genotype_summary(mouse),
        "cage_history": cage_history,
        "family_offspring": [build_mouse_relation_card(m) for m in offspring],
        "family_littermates": [build_mouse_relation_card(m) for m in littermates],
        "family_sire": build_mouse_relation_card(mouse.sire) if mouse.sire else None,
        "family_dam": build_mouse_relation_card(mouse.dam) if mouse.dam else None,
        "audit_entries": mouse_audit_entries,
        **actors,
    }
    return render(request, "colony/mouse_detail.html", context)


@authenticated_required
def mouse_pedigree(request: HttpRequest, pk: int) -> HttpResponse:
    mouse = get_object_or_404(
        _scoped_mouse_queryset(request.user).select_related("sire", "dam"),
        pk=pk,
    )
    offspring = (
        _scoped_mouse_queryset(request.user).filter(Q(sire=mouse) | Q(dam=mouse))
        .select_related("current_cage")
        .prefetch_related("genotypes__gene")
        .distinct()
        .order_by("mouse_uid")
    )
    littermates = Mouse.objects.none()
    if mouse.sire_id and mouse.dam_id:
        littermates = (
            _scoped_mouse_queryset(request.user).filter(sire_id=mouse.sire_id, dam_id=mouse.dam_id)
            .exclude(pk=mouse.pk)
            .select_related("current_cage")
            .prefetch_related("genotypes__gene")
            .order_by("mouse_uid")
        )

    context = {
        "mouse": mouse,
        "sire": build_mouse_relation_card(mouse.sire) if mouse.sire else None,
        "dam": build_mouse_relation_card(mouse.dam) if mouse.dam else None,
        "offspring": [build_mouse_relation_card(m) for m in offspring],
        "littermates": [build_mouse_relation_card(m) for m in littermates],
        "focal_summary": build_short_genotype_summary(mouse),
    }
    return render(request, "colony/mouse_pedigree.html", context)


@authenticated_required
def family_tree(request: HttpRequest) -> HttpResponse:
    q = (request.GET.get("q") or "").strip()
    mice = _scoped_mouse_queryset(request.user).select_related("sire", "dam", "current_cage", "strain_line")
    if q:
        mice = mice.filter(Q(mouse_uid__icontains=q) | Q(ear_tag__icontains=q) | Q(toe_tag__icontains=q))
    mice = mice.order_by("-birth_date", "mouse_uid")[:80]
    return render(
        request,
        "colony/family_tree.html",
        {
            "mice": mice,
            "q": q,
        },
    )


@authenticated_required
def mouse_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = MouseForm(request.POST, user=request.user)
        if form.is_valid():
            ensure_can_edit_project_data(request.user, form.cleaned_data.get("project"))
            mouse = form.save()
            log_audit_event(
                user=request.user,
                action=AuditLog.Action.CREATE,
                obj=mouse,
                message=f"Created mouse {mouse.mouse_uid}.",
            )
            return redirect("mice:mouse_detail", pk=mouse.pk)
    else:
        form = MouseForm(user=request.user)

    context = {
        "form": form,
        "page_title": "Create Mouse",
        "submit_label": "Save Mouse",
        "cancel_url": "mice:mouse_list",
    }
    return render(request, "colony/mouse_form.html", context)


@authenticated_required
def mouse_edit(request: HttpRequest, pk: int) -> HttpResponse:
    mouse = get_object_or_404(_scoped_mouse_queryset(request.user), pk=pk)
    ensure_can_edit_project_data(request.user, mouse.project)
    previous_status = mouse.status
    if request.method == "POST":
        form = MouseForm(request.POST, instance=mouse, user=request.user)
        if form.is_valid():
            new_project = form.cleaned_data.get("project")
            old_project = mouse.project
            if new_project != old_project:
                ensure_can_edit_project_data(request.user, old_project)
                ensure_can_edit_project_data(request.user, new_project)
            else:
                ensure_can_edit_project_data(request.user, old_project)
            target_status = form.cleaned_data.get("status")
            if target_status != previous_status:
                terminal = {
                    Mouse.Status.ARCHIVED,
                    Mouse.Status.DEAD,
                    Mouse.Status.CULLED,
                    Mouse.Status.TRANSFERRED,
                    Mouse.Status.EUTHANIZED,
                }
                if target_status in terminal or previous_status in terminal:
                    ensure_can_archive_or_change_terminal_status(request.user, new_project)
            msg = summarize_modelform_changes(form)
            mouse = form.save()
            log_audit_event(
                user=request.user,
                action=AuditLog.Action.UPDATE,
                obj=mouse,
                message=msg[:4000],
            )
            return redirect("mice:mouse_detail", pk=mouse.pk)
    else:
        form = MouseForm(instance=mouse, user=request.user)

    context = {
        "form": form,
        "page_title": f"Edit Mouse {mouse.mouse_uid}",
        "submit_label": "Save Changes",
        "cancel_url": "mice:mouse_detail",
        "cancel_kwargs": {"pk": mouse.pk},
    }
    return render(request, "colony/mouse_form.html", context)


@authenticated_required
def mouse_move(request: HttpRequest, pk: int) -> HttpResponse:
    mouse = get_object_or_404(
        _scoped_mouse_queryset(request.user),
        pk=pk,
    )
    ensure_can_edit_project_data(request.user, mouse.project)

    if request.method == "POST":
        form = MoveCageForm(request.POST, mouse=mouse)
        if form.is_valid():
            destination_cage = form.cleaned_data["destination_cage"]
            move_date = form.cleaned_data["move_date"]
            reason = form.cleaned_data["reason"]
            notes = form.cleaned_data["notes"]

            with transaction.atomic():
                mouse_locked = Mouse.objects.select_for_update().get(pk=mouse.pk)
                origin_cage = mouse_locked.current_cage

                current_memberships = CageMembership.objects.select_for_update().filter(
                    mouse=mouse_locked,
                    is_current=True,
                )
                if current_memberships.exists():
                    current_memberships.update(end_date=move_date, is_current=False)

                mouse_locked.current_cage = destination_cage
                mouse_locked.save(update_fields=["current_cage", "updated_at"])

                CageMembership.objects.create(
                    mouse=mouse_locked,
                    cage=destination_cage,
                    start_date=move_date,
                    end_date=None,
                    is_current=True,
                    reason=reason,
                    notes=notes,
                )

            source_label = origin_cage.cage_id if origin_cage else "None"
            log_audit_event(
                user=request.user,
                action=AuditLog.Action.MOVE_CAGE,
                obj=mouse_locked,
                message=(
                    f"Moved mouse {mouse_locked.mouse_uid} from cage {source_label} "
                    f"to {destination_cage.cage_id} on {move_date}."
                ),
            )
            return redirect("mice:mouse_detail", pk=mouse.pk)
    else:
        form = MoveCageForm(mouse=mouse)

    context = {
        "mouse": mouse,
        "form": form,
    }
    return render(request, "colony/mouse_move.html", context)


@authenticated_required
def mouse_end(request: HttpRequest, pk: int) -> HttpResponse:
    mouse = get_object_or_404(_scoped_mouse_queryset(request.user), pk=pk)
    ensure_can_edit_project_data(request.user, mouse.project)
    ensure_can_archive_or_change_terminal_status(request.user, mouse.project)
    if request.method != "POST":
        raise PermissionDenied("Use POST to end/euthanize a mouse.")

    previous_status = mouse.status
    today = timezone.localdate()
    mouse.status = Mouse.Status.EUTHANIZED
    if not mouse.euthanasia_date:
        mouse.euthanasia_date = today
    if not mouse.death_date:
        mouse.death_date = today
    if not mouse.death_reason:
        mouse.death_reason = "Marked as ended via workflow action."
    mouse.save(update_fields=["status", "euthanasia_date", "death_date", "death_reason", "updated_at"])

    log_audit_event(
        user=request.user,
        action=AuditLog.Action.UPDATE,
        obj=mouse,
        message=f"Changed mouse {mouse.mouse_uid} status from {previous_status} to {mouse.status}.",
    )
    messages.success(request, f"Mouse {mouse.mouse_uid} marked as euthanized.")
    return redirect("mice:mouse_detail", pk=mouse.pk)


@authenticated_required
def mouse_genotypes_export(request: HttpRequest, pk: int) -> HttpResponse:
    mouse = get_object_or_404(_scoped_mouse_queryset(request.user), pk=pk)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="mouse_{mouse.mouse_uid}_genotypes.csv"'
    writer = csv.writer(response)
    writer.writerow(
        [
            "mouse_uid",
            "locus_name",
            "allele_1",
            "allele_2",
            "zygosity_display",
            "is_confirmed",
            "assay_date",
            "notes",
        ]
    )
    for row in get_mouse_genotype_rows(mouse):
        writer.writerow(row)
    return response


@authenticated_required
def mouse_genotypes_export_xlsx(request: HttpRequest, pk: int) -> HttpResponse:
    mouse = get_object_or_404(_scoped_mouse_queryset(request.user), pk=pk)
    headers = [
        "mouse_uid",
        "locus_name",
        "allele_1",
        "allele_2",
        "zygosity_display",
        "is_confirmed",
        "assay_date",
        "notes",
    ]
    rows = get_mouse_genotype_rows(mouse)
    return build_xlsx_response(f"mouse_{mouse.mouse_uid}_genotypes.xlsx", "Genotypes", headers, rows)
