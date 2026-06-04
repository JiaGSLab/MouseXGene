import csv
import json
import logging
from io import BytesIO

from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.contrib import messages
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.core.exceptions import PermissionDenied, ValidationError
from django.views.decorators.http import require_POST
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
    GENOTYPE_SLOT_COUNT,
    MOUSE_EXPECTED_COLUMNS,
    MOUSE_IMPORT_TEMPLATE_COLUMNS,
    MouseImportOptions,
    parse_cage_import,
    parse_mouse_import,
)
from .models import Cage, CageMembership, Mouse, MouseGenotypeComponent, StrainLine, StrainLineDocument
from .strain_pdf import MAX_STRAIN_LINE_PDF_COUNT, validate_strain_line_pdf_file
from breeding.models import Breeding, Litter
from genotypes.models import MouseGenotype
from core.audit import log_audit_event
from core.history import audit_entries_for_object, merge_actor_labels, summarize_modelform_changes
from core.models import AuditLog, ImportLog, Project, ProjectMembership
from users.forms import UserImportPrefixForm
from users.import_prefix import get_effective_import_prefix
from users.models import UserProfile
from colony.mouse_age import mouse_list_age_band
from colony.cage_lifecycle import (
    breeding_setup_message,
    sync_cage_breeding_workflow,
    sync_cage_status_from_mice,
)
from colony.import_staging import (
    ImportStagingError,
    clear_staged_cage_import,
    clear_staged_mouse_import,
    decode_staged_file,
    file_bytes_to_upload,
    pop_staged_cage_import,
    pop_staged_mouse_import,
    stage_cage_import,
    stage_mouse_import,
)
from core.list_sort import (
    CAGE_LIST_SORT,
    FAMILY_TREE_SORT,
    MICE_LIST_SORT,
    STRAIN_LINE_LIST_SORT,
    apply_list_sort,
    build_list_sort_context,
)
from users.permissions import (
    authenticated_required,
    can_edit_project_data,
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

logger = logging.getLogger(__name__)


DEFAULT_MOUSE_IMPORT_OPTIONS = MouseImportOptions(
    auto_create_missing_strain_lines=True,
    auto_create_missing_projects=True,
    auto_create_missing_cages=True,
    resolve_pedigree_within_file=True,
)

CAGE_IMPORT_UPDATE_FIELDS = (
    "created_date",
    "room",
    "rack",
    "position",
    "cage_type",
    "purpose",
    "status",
    "notes",
)
IMPORT_OVERWRITE_ID_PREVIEW_LIMIT = 30


def _partition_import_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    create_rows = [row for row in rows if not row.get("_update")]
    update_rows = [row for row in rows if row.get("_update")]
    return create_rows, update_rows


def _build_import_overwrite_context(
    *,
    rows: list[dict],
    id_key: str,
    record_label: str,
    staged_filename: str,
) -> dict | None:
    create_rows, update_rows = _partition_import_rows(rows)
    if not update_rows:
        return None
    ids = [row[id_key] for row in update_rows]
    truncated = 0
    if len(ids) > IMPORT_OVERWRITE_ID_PREVIEW_LIMIT:
        truncated = len(ids) - IMPORT_OVERWRITE_ID_PREVIEW_LIMIT
        ids = ids[:IMPORT_OVERWRITE_ID_PREVIEW_LIMIT]
    return {
        "overwrite_warning": True,
        "overwrite_update_count": len(update_rows),
        "overwrite_create_count": len(create_rows),
        "overwrite_ids": ids,
        "overwrite_ids_truncated": truncated,
        "record_label": record_label,
        "staged_filename": staged_filename,
    }


def _apply_cage_import_rows(rows: list[dict], *, acting_user) -> tuple[int, int]:
    create_rows, update_rows = _partition_import_rows(rows)
    with transaction.atomic():
        if create_rows:
            Cage.objects.bulk_create(
                [Cage(**{k: v for k, v in row.items() if k != "_update"}) for row in create_rows]
            )
            Cage.objects.filter(cage_id__in=[row["cage_id"] for row in create_rows]).update(
                created_by_id=acting_user.pk,
                updated_by_id=acting_user.pk,
            )
        for row in update_rows:
            cage = Cage.objects.get(cage_id=row["cage_id"])
            for field in CAGE_IMPORT_UPDATE_FIELDS:
                setattr(cage, field, row[field])
            cage.updated_by_id = acting_user.pk
            cage.save(update_fields=[*CAGE_IMPORT_UPDATE_FIELDS, "updated_by_id", "updated_at"])
    for row in create_rows + update_rows:
        cage = Cage.objects.get(cage_id=row["cage_id"])
        sync_cage_breeding_workflow(cage)
        sync_cage_status_from_mice(cage)
    return len(create_rows), len(update_rows)


def _mouse_import_options_from_dict(data: dict[str, bool]) -> MouseImportOptions:
    return MouseImportOptions(
        auto_create_missing_strain_lines=data["auto_create_missing_strain_lines"],
        auto_create_missing_projects=data["auto_create_missing_projects"],
        auto_create_missing_cages=data["auto_create_missing_cages"],
        resolve_pedigree_within_file=data["resolve_pedigree_within_file"],
    )


def _complete_mouse_import(request, stats: dict[str, int], upload_name: str) -> HttpResponse:
    log_audit_event(
        user=request.user,
        action=AuditLog.Action.IMPORT,
        message=(
            f"Imported mice via file upload "
            f"({stats['created_mice']} created, {stats['updated_mice']} updated; "
            f"auto-created: {stats['auto_created_strain_lines']} strain lines, "
            f"{stats['auto_created_projects']} projects, "
            f"{stats['auto_created_cages']} cages; "
            f"genotypes: +{stats['genotype_rows_created']} / ~{stats['genotype_rows_updated']})."
        ),
        object_type="Mouse",
        object_id=str(stats["created_mice"] + stats["updated_mice"]),
        object_repr="Bulk Mouse Import",
    )
    record_import_log(
        user=request.user,
        import_type=ImportLog.ImportType.MOUSE,
        filename=upload_name,
        success=True,
        created_count=stats["created_mice"] + stats["updated_mice"],
        errors=[],
    )
    messages.success(
        request,
        (
            f"Import complete: {stats['created_mice']} mouse(s) created, "
            f"{stats['updated_mice']} updated "
            f"(auto-created: {stats['auto_created_strain_lines']} strain lines, "
            f"{stats['auto_created_projects']} projects, "
            f"{stats['auto_created_cages']} cages; "
            f"genotypes created {stats['genotype_rows_created']}, "
            f"updated {stats['genotype_rows_updated']})."
        ),
    )
    return redirect("mice:mouse_list")


def _run_mouse_import_execution(
    request,
    rows: list[dict],
    *,
    import_options: MouseImportOptions,
    upload_name: str,
) -> tuple[HttpResponse | None, list[str]]:
    try:
        with transaction.atomic():
            stats = _execute_two_pass_mouse_import(
                rows,
                options=import_options,
                import_date=timezone.localdate(),
                acting_user=request.user,
            )
    except MouseImportExecutionError as exc:
        record_import_log(
            user=request.user,
            import_type=ImportLog.ImportType.MOUSE,
            filename=upload_name,
            success=False,
            created_count=0,
            errors=exc.errors,
        )
        return None, exc.errors
    except Exception:
        logger.exception("Unexpected error during mouse import.")
        errors = [
            (
                "Import failed due to an unexpected server error. "
                "Please retry once; if it still fails, check your row values and contact admin."
            )
        ]
        record_import_log(
            user=request.user,
            import_type=ImportLog.ImportType.MOUSE,
            filename=upload_name,
            success=False,
            created_count=0,
            errors=errors,
        )
        return None, errors
    return _complete_mouse_import(request, stats, upload_name), []


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


def _filtered_cages_queryset(request: HttpRequest):
    """Apply the same GET filters as the cage list page."""
    q = (request.GET.get("q") or "").strip()
    room = (request.GET.get("room") or "").strip()
    rack = (request.GET.get("rack") or "").strip()
    cage_type = (request.GET.get("cage_type") or "").strip()
    purpose = (request.GET.get("purpose") or "").strip()
    status = (request.GET.get("status") or "").strip()
    is_empty = (request.GET.get("is_empty") or "").strip()
    include_inactive = (request.GET.get("include_inactive") or "").strip()
    strain_line = (request.GET.get("strain_line") or request.GET.get("strain_line_id") or "").strip()

    cages = _scoped_cage_queryset(request.user)
    if include_inactive != "yes":
        cages = cages.filter(status=Cage.Status.ACTIVE)
    if strain_line:
        cages = cages.filter(current_mice__strain_line_id=strain_line)
        if include_inactive != "yes":
            cages = cages.filter(current_mice__status=Mouse.Status.ACTIVE)
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

    return (
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


CAGE_EXPORT_EXTRA_COLUMNS = [
    "projects",
    "owners",
    "current_mouse_count",
    "genotype_overview",
    "created_at",
    "updated_at",
]


def _cages_export_headers() -> list[str]:
    return EXPECTED_COLUMNS + CAGE_EXPORT_EXTRA_COLUMNS


def _cages_export_rows_from_queryset(cages) -> list[list]:
    rows: list[list] = []
    for cage in cages:
        cage_mice = list(cage.current_mice.all().order_by("mouse_uid"))
        project_rows = cage_projects_from_mice(cage_mice)
        projects_text = "; ".join(pr["project"].name for pr in project_rows)
        owners_text = "; ".join(pr["owner_display"] for pr in project_rows)
        rows.append(
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
                projects_text,
                owners_text,
                len(cage_mice),
                build_cage_genotype_overview(cage_mice),
                cage.created_at.isoformat(timespec="seconds"),
                cage.updated_at.isoformat(timespec="seconds"),
            ]
        )
    return rows


def get_cages_export_rows(request: HttpRequest) -> list[list]:
    return _cages_export_rows_from_queryset(_filtered_cages_queryset(request))


def _cages_export_http_response(request: HttpRequest, export_fmt: str) -> HttpResponse:
    cages = apply_list_sort(_filtered_cages_queryset(request), request, CAGE_LIST_SORT)
    rows = _cages_export_rows_from_queryset(cages)
    headers = _cages_export_headers()
    if export_fmt == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="cages_export.csv"'
        writer = csv.writer(response)
        writer.writerow(headers)
        writer.writerows(rows)
    else:
        response = build_xlsx_response("cages.xlsx", "Cages", headers, rows)
    response["Cache-Control"] = "no-store"
    return response


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


def _filtered_mice_queryset(request: HttpRequest):
    """Apply the same GET filters as the mouse list page."""
    query = (request.GET.get("q") or "").strip()
    sex = (request.GET.get("sex") or "").strip()
    status = (request.GET.get("status") or "").strip()
    strain_line = (request.GET.get("strain_line") or request.GET.get("strain_line_id") or "").strip()
    current_cage = (request.GET.get("current_cage") or request.GET.get("cage_id") or "").strip()
    project = (request.GET.get("project") or request.GET.get("project_id") or "").strip()
    include_inactive = (request.GET.get("include_inactive") or "").strip()

    mice = _scoped_mouse_queryset(request.user)
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

    return mice


def get_mice_export_rows(request: HttpRequest) -> list[list]:
    mice = apply_list_sort(
        _filtered_mice_queryset(request)
        .select_related("project", "project__owner", "project__owner__profile", "sire", "dam")
        .prefetch_related("genotypes__gene"),
        request,
        MICE_LIST_SORT,
    )
    rows: list[list] = []
    for mouse in mice:
        row_map: dict[str, str] = {col: "" for col in MOUSE_EXPECTED_COLUMNS}
        row_map["mouse_uid"] = mouse.mouse_uid
        row_map["sex"] = mouse.sex
        row_map["birth_date"] = str(mouse.birth_date) if mouse.birth_date else ""
        row_map["status"] = mouse.status
        row_map["strain_line"] = mouse.strain_line.label if mouse.strain_line else ""
        row_map["current_cage"] = mouse.current_cage.cage_id if mouse.current_cage else ""
        row_map["project"] = mouse.project.name if mouse.project else ""
        row_map["ear_tag"] = mouse.ear_tag
        row_map["toe_tag"] = mouse.toe_tag
        row_map["origin"] = mouse.origin
        row_map["coat_color"] = mouse.coat_color
        row_map["notes"] = mouse.notes
        row_map["sire"] = mouse.sire.mouse_uid if mouse.sire else ""
        row_map["dam"] = mouse.dam.mouse_uid if mouse.dam else ""

        gt_records = list(mouse.genotypes.all().order_by("-assay_date", "-created_at"))
        for idx, gt in enumerate(gt_records[:GENOTYPE_SLOT_COUNT], start=1):
            row_map[f"genotype_{idx}_locus"] = gt.gene.symbol if gt.gene else (gt.locus_name or "")
            row_map[f"genotype_{idx}_allele_1"] = gt.allele_1 or ""
            row_map[f"genotype_{idx}_allele_2"] = gt.allele_2 or ""
            row_map[f"genotype_{idx}_zygosity"] = gt.zygosity_display or ""
            if gt.is_confirmed is True:
                row_map[f"genotype_{idx}_is_confirmed"] = "yes"
            elif gt.is_confirmed is False:
                row_map[f"genotype_{idx}_is_confirmed"] = "no"
            else:
                row_map[f"genotype_{idx}_is_confirmed"] = ""
            row_map[f"genotype_{idx}_assay_date"] = str(gt.assay_date) if gt.assay_date else ""
            row_map[f"genotype_{idx}_notes"] = gt.notes or ""

        base_cols = [row_map[col] for col in MOUSE_EXPECTED_COLUMNS]
        extras = [
            mouse.project.owner_display if mouse.project else "",
            build_short_genotype_summary(mouse),
            str(mouse.death_date) if mouse.death_date else "",
            str(mouse.euthanasia_date) if mouse.euthanasia_date else "",
            mouse.death_reason or "",
            mouse.created_at.isoformat(timespec="seconds"),
            mouse.updated_at.isoformat(timespec="seconds"),
        ]
        rows.append(base_cols + extras)
    return rows


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
    has_template = bool(
        mouse.strain_line_id and mouse.strain_line.expected_loci_entries()
    )
    if mouse.genotype_components.exists() or has_template:
        fresh = mouse.compute_genotype_summary()
        stored = (mouse.genotype_summary or "").strip()
        if stored != fresh:
            mouse.genotype_summary = fresh
            mouse.save(update_fields=["genotype_summary", "updated_at"])
        return fresh
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


def _active_breeding_badges_for_mouse_ids(mouse_ids: list[int]) -> dict[int, list[dict[str, str]]]:
    out: dict[int, list[dict[str, str]]] = {mid: [] for mid in mouse_ids}
    if not mouse_ids:
        return out
    breedings = (
        Breeding.objects.filter(active=True)
        .filter(
            Q(male_id__in=mouse_ids)
            | Q(female_1_id__in=mouse_ids)
            | Q(female_2_id__in=mouse_ids)
            | Q(extra_female_links__mouse_id__in=mouse_ids)
        )
        .prefetch_related("extra_female_links")
        .distinct()
    )
    for breeding in breedings:
        if breeding.male_id in out:
            out[breeding.male_id].append({"role": "Sire", "code": breeding.breeding_code})
        if breeding.female_1_id in out:
            out[breeding.female_1_id].append({"role": "Dam", "code": breeding.breeding_code})
        if breeding.female_2_id in out:
            out[breeding.female_2_id].append({"role": "Dam", "code": breeding.breeding_code})
        for row in breeding.extra_female_links.all():
            if row.mouse_id in out:
                out[row.mouse_id].append({"role": "Dam", "code": breeding.breeding_code})
    return out


def _normalize_name(value: str | None) -> str:
    return (value or "").strip()


def _strain_template_loci_map() -> dict[str, list[dict[str, str]]]:
    out: dict[str, list[dict[str, str]]] = {}
    for line in StrainLine.objects.filter(is_active=True).order_by("line_name"):
        out[str(line.pk)] = line.expected_loci_entries()
    return out


def _strain_default_project_map() -> dict[str, str]:
    out: dict[str, str] = {
        str(line.pk): str(line.default_project_id)
        for line in StrainLine.objects.filter(is_active=True, default_project_id__isnull=False)
    }
    single_project_lines = (
        StrainLine.objects.filter(is_active=True, default_project_id__isnull=True)
        .annotate(project_count=Count("mice__project", distinct=True))
        .filter(project_count=1)
    )
    for line in single_project_lines:
        project_id = (
            Mouse.objects.filter(strain_line=line)
            .values_list("project_id", flat=True)
            .first()
        )
        if project_id:
            out[str(line.pk)] = str(project_id)
    return out


def _effective_default_project_id(line: StrainLine) -> int | None:
    if line.default_project_id:
        return line.default_project_id
    project_ids = list(
        Mouse.objects.filter(strain_line=line)
        .values_list("project_id", flat=True)
        .distinct()
    )
    if len(project_ids) == 1:
        return project_ids[0]
    return None


def _extract_mouse_genotype_rows_from_post(request: HttpRequest) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    try:
        total = int(request.POST.get("genotype_row_count", "0"))
    except ValueError:
        total = 0
    total = max(0, min(total, 200))
    for idx in range(total):
        locus = (request.POST.get(f"genotype_locus_{idx}") or "").strip()
        genotype = (request.POST.get(f"genotype_display_{idx}") or "").strip()
        if not locus:
            continue
        rows.append({"locus": locus, "genotype": genotype})
    return rows


def _template_loci_union_for_mouse_relations(
    *,
    strain_line: StrainLine | None = None,
    sire: Mouse | None = None,
    dam: Mouse | None = None,
) -> list[dict[str, str]]:
    ordered: list[dict[str, str]] = []
    seen: set[str] = set()

    def add_many(values: list[dict[str, str]]) -> None:
        for entry in values:
            text = (entry.get("locus_name") or "").strip()
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            locus_type = entry.get("locus_type") or StrainLine.LocusType.CUSTOM
            chromosome_type = entry.get("chromosome_type") or StrainLine.ChromosomeType.AUTOSOMAL
            ordered.append(
                {
                    "locus_name": text,
                    "locus_type": locus_type,
                    "chromosome_type": chromosome_type,
                }
            )

    if strain_line is not None:
        add_many(strain_line.expected_loci_entries())
    if sire is not None and sire.strain_line_id:
        add_many(sire.strain_line.expected_loci_entries())
    if dam is not None and dam.strain_line_id:
        add_many(dam.strain_line.expected_loci_entries())
    return ordered


def _editable_template_loci_for_mouse(mouse: Mouse) -> tuple[list[str], bool]:
    """
    Return editable loci template + whether parent-union source is used.

    Priority:
    1) If mouse has both sire and dam, use union of sire+dam strain-line templates.
    2) Else fallback to mouse strain-line template.
    3) Else no loci.
    """
    if mouse.sire_id and mouse.dam_id:
        parent_rows = _template_loci_union_for_mouse_relations(sire=mouse.sire, dam=mouse.dam)
        parent_loci = [row["locus_name"] for row in parent_rows if (row.get("locus_name") or "").strip()]
        if parent_loci:
            return parent_loci, True
    if mouse.strain_line_id:
        return mouse.strain_line.expected_loci_list(), False
    return [], False


def _resolved_template_loci_for_context(
    *,
    strain_line: StrainLine | None,
    sire: Mouse | None,
    dam: Mouse | None,
) -> list[str]:
    """Resolve logical template loci with parental-union priority."""
    if sire is not None and dam is not None:
        parent_rows = _template_loci_union_for_mouse_relations(sire=sire, dam=dam)
        parent_loci = [row["locus_name"] for row in parent_rows if (row.get("locus_name") or "").strip()]
        if parent_loci:
            return parent_loci
    if strain_line is not None:
        return strain_line.expected_loci_list()
    return []


def _component_has_meaningful_truth(component: MouseGenotypeComponent) -> bool:
    allele_1 = (component.allele_display_1 or "").strip()
    allele_2 = (component.allele_display_2 or "").strip()
    zygosity = (component.zygosity or "").strip()
    if zygosity == "-":
        zygosity = ""
    if allele_1 == "-":
        allele_1 = ""
    if allele_2 == "-":
        allele_2 = ""
    return bool((allele_1 and allele_2) or zygosity)


def _mouse_has_meaningful_genotype_truth(mouse: Mouse) -> bool:
    return any(_component_has_meaningful_truth(c) for c in mouse.genotype_components.all())


def _mouse_component_loci_set(mouse: Mouse) -> set[str]:
    loci: set[str] = set()
    for c in mouse.genotype_components.all():
        locus = StrainLine.normalize_locus_name((c.locus_name or "").strip())
        if locus:
            loci.add(locus.casefold())
    return loci


def _apply_locus_renames_on_mice(
    line: StrainLine,
    before_entries: list[dict[str, str]],
    after_entries: list[dict[str, str]],
) -> int:
    """When loci are renamed in-place (same row count), update mouse component locus names."""
    if len(before_entries) != len(after_entries):
        return 0
    renamed = 0
    for old, new in zip(before_entries, after_entries, strict=True):
        old_name = StrainLine.normalize_locus_name(str(old.get("locus_name", "")).strip())
        new_name = StrainLine.normalize_locus_name(str(new.get("locus_name", "")).strip())
        if not old_name or not new_name or old_name.casefold() == new_name.casefold():
            continue
        renamed += MouseGenotypeComponent.objects.filter(
            mouse__strain_line=line,
            locus_name__iexact=old_name,
        ).update(locus_name=new_name)
    return renamed


def _propagate_strain_line_template_to_mice(
    line: StrainLine,
    *,
    before_entries: list[dict[str, str]] | None = None,
) -> tuple[int, int, int]:
    """Sync template loci to mice on this strain line and refresh genotype summaries."""
    entries = line.expected_loci_entries()
    entry_by_key = {e["locus_name"].casefold(): e for e in entries}
    if before_entries:
        _apply_locus_renames_on_mice(line, before_entries, entries)
    mice_count = 0
    components_added = 0
    components_removed = 0
    for mouse in Mouse.objects.filter(strain_line=line).iterator(chunk_size=100):
        components_added += mouse.ensure_template_genotype_components(include_strain_template=True)
        for comp in list(mouse.genotype_components.all()):
            locus = StrainLine.normalize_locus_name((comp.locus_name or "").strip())
            if not locus:
                continue
            locus_key = locus.casefold()
            entry = entry_by_key.get(locus_key)
            if entry is None:
                comp.delete()
                components_removed += 1
                continue
            chromosome_type = entry.get("chromosome_type", "")
            if (
                chromosome_type in MouseGenotypeComponent.ChromosomeType.values
                and comp.chromosome_type != chromosome_type
            ):
                comp.chromosome_type = chromosome_type
                comp.save(update_fields=["chromosome_type", "updated_at"])
        mouse.rebuild_genotype_summary(save=True)
        mice_count += 1
    return mice_count, components_added, components_removed


def _apply_strain_template_resolution(mouse: Mouse, *, mode: str, target_loci: list[str]) -> None:
    target_keys = {StrainLine.normalize_locus_name(l).casefold() for l in target_loci if StrainLine.normalize_locus_name(l)}
    if mode == "replace":
        mouse.genotype_components.all().delete()
        mouse.ensure_template_genotype_components(extra_loci=list(target_loci), include_strain_template=False)
        mouse.rebuild_genotype_summary(save=True)
        return
    # overlap-safe mode: keep only loci that overlap with target template
    for c in mouse.genotype_components.all():
        locus = StrainLine.normalize_locus_name((c.locus_name or "").strip())
        if not locus or locus.casefold() not in target_keys:
            c.delete()
    mouse.rebuild_genotype_summary(save=True)


def _mouse_to_strain_line_map() -> dict[str, str]:
    return {str(pk): str(strain_id) for pk, strain_id in Mouse.objects.values_list("pk", "strain_line_id")}


def _infer_chromosome_type_for_mouse_genotype(allele_2: str) -> str:
    if (allele_2 or "").upper() == "Y":
        return MouseGenotypeComponent.ChromosomeType.X_LINKED
    return MouseGenotypeComponent.ChromosomeType.UNKNOWN


def _infer_zygosity_class_for_mouse_genotype(allele_1: str, allele_2: str) -> str:
    a1 = (allele_1 or "").strip()
    a2 = (allele_2 or "").strip()
    if not (a1 and a2):
        return MouseGenotypeComponent.ZygosityClass.UNKNOWN
    if a2.upper() == "Y":
        return MouseGenotypeComponent.ZygosityClass.HEMIZYGOUS
    if a1 == a2:
        if a1 in {"+", "wt", "WT"}:
            return MouseGenotypeComponent.ZygosityClass.WT
        return MouseGenotypeComponent.ZygosityClass.HOM
    return MouseGenotypeComponent.ZygosityClass.HET


def _apply_mouse_genotype_rows(mouse: Mouse, rows: list[dict[str, str]]) -> int:
    updated = 0
    for i, row in enumerate(rows):
        locus = StrainLine.normalize_locus_name(row["locus"])
        if not locus:
            continue
        display = row["genotype"]
        if not display:
            continue
        cleaned = display.replace(" ", "")
        if "/" not in cleaned:
            continue
        allele_1, allele_2 = [part.strip() for part in cleaned.split("/", 1)]
        if not (allele_1 and allele_2):
            continue
        obj, _ = MouseGenotypeComponent.objects.get_or_create(
            mouse=mouse,
            locus_name=locus,
            defaults={
                "strain_line": mouse.strain_line,
                "sort_order": i + 1,
            },
        )
        obj.strain_line = mouse.strain_line
        obj.zygosity = f"{allele_1}/{allele_2}"
        obj.allele_display_1 = allele_1
        obj.allele_display_2 = allele_2
        obj.chromosome_type = _infer_chromosome_type_for_mouse_genotype(allele_2)
        obj.zygosity_class = _infer_zygosity_class_for_mouse_genotype(allele_1, allele_2)
        obj.save()
        updated += 1
    if updated:
        mouse.rebuild_genotype_summary(save=True)
    return updated


def _apply_mouse_genotype_rows_to_template(mouse: Mouse, rows: list[dict[str, str]], template_rows: list[dict[str, str]]) -> int:
    row_map: dict[str, str] = {}
    for row in rows:
        locus = StrainLine.normalize_locus_name(row.get("locus") or "")
        if not locus:
            continue
        row_map[locus.casefold()] = (row.get("genotype") or "").strip()
    components = {
        StrainLine.normalize_locus_name((c.locus_name or "").strip()).casefold(): c
        for c in mouse.genotype_components.all()
        if StrainLine.normalize_locus_name((c.locus_name or "").strip())
    }
    updated = 0
    for template in template_rows:
        locus = StrainLine.normalize_locus_name(template.get("locus_name") or "")
        if not locus:
            continue
        key = locus.casefold()
        component = components.get(key)
        if component is None:
            continue
        raw = (row_map.get(key) or "").strip()
        if raw == "-":
            raw = ""
        before = (
            component.zygosity or "",
            component.allele_display_1 or "",
            component.allele_display_2 or "",
            component.chromosome_type or "",
            component.zygosity_class or "",
        )
        if not raw:
            component.zygosity = ""
            component.allele_display_1 = ""
            component.allele_display_2 = ""
            component.chromosome_type = template.get("chromosome_type") or MouseGenotypeComponent.ChromosomeType.UNKNOWN
            component.zygosity_class = MouseGenotypeComponent.ZygosityClass.UNKNOWN
        else:
            cleaned = raw.replace(" ", "")
            if "/" in cleaned:
                allele_1, allele_2 = [part.strip() for part in cleaned.split("/", 1)]
                component.zygosity = f"{allele_1}/{allele_2}" if allele_1 and allele_2 else cleaned
                component.allele_display_1 = allele_1
                component.allele_display_2 = allele_2
                component.chromosome_type = _infer_chromosome_type_for_mouse_genotype(allele_2)
                component.zygosity_class = _infer_zygosity_class_for_mouse_genotype(allele_1, allele_2)
            else:
                component.zygosity = cleaned
                component.allele_display_1 = ""
                component.allele_display_2 = ""
                component.chromosome_type = template.get("chromosome_type") or MouseGenotypeComponent.ChromosomeType.UNKNOWN
                component.zygosity_class = MouseGenotypeComponent.ZygosityClass.UNKNOWN
        after = (
            component.zygosity or "",
            component.allele_display_1 or "",
            component.allele_display_2 or "",
            component.chromosome_type or "",
            component.zygosity_class or "",
        )
        if after != before:
            component.save()
            updated += 1
    mouse.rebuild_genotype_summary(save=True)
    return updated


def _genotype_components_signature(mouse: Mouse) -> list[tuple]:
    """Stable snapshot for change detection before/after edits."""
    components = (
        mouse.genotype_components.select_related("strain_line")
        .order_by("sort_order", "id")
        .values_list(
            "sort_order",
            "locus_name",
            "chromosome_type",
            "zygosity_class",
            "zygosity",
            "allele_display_1",
            "allele_display_2",
            "notes",
            "strain_line_id",
        )
    )
    return list(components)


def _signature_component_key(row: tuple) -> str:
    sort_order, locus_name, _chromosome_type, _zygosity_class, _zygosity, _a1, _a2, _notes, strain_line_id = row
    locus = (locus_name or "").strip()
    if locus:
        return locus.casefold()
    return f"component:{sort_order}:{strain_line_id or 'na'}"


def _signature_component_display(row: tuple) -> tuple[str, str]:
    sort_order, locus_name, _chromosome_type, _zygosity_class, zygosity, allele_1, allele_2, _notes, _strain_line_id = row
    label = (locus_name or "").strip() or f"Component {sort_order}"
    genotype = (zygosity or "").strip()
    if not genotype:
        parts = [p for p in [(allele_1 or "").strip(), (allele_2 or "").strip()] if p]
        genotype = "/".join(parts)
    if genotype == "-":
        genotype = ""
    return label, (genotype or "")


def _build_specific_genotype_history_lines(before: list[tuple], after: list[tuple]) -> list[str]:
    before_map = {_signature_component_key(row): row for row in before}
    after_map = {_signature_component_key(row): row for row in after}
    keys = sorted(set(before_map.keys()) | set(after_map.keys()))
    lines: list[str] = []
    for key in keys:
        prev = before_map.get(key)
        curr = after_map.get(key)
        if prev is None and curr is not None:
            label, now_text = _signature_component_display(curr)
            if now_text:
                lines.append(f"Added {label}: {now_text}")
            continue
        if prev is not None and curr is None:
            label, _old_text = _signature_component_display(prev)
            prev_label, prev_text = _signature_component_display(prev)
            if prev_text:
                lines.append(f"Removed {prev_label}")
            continue
        if prev is not None and curr is not None:
            prev_label, prev_text = _signature_component_display(prev)
            curr_label, curr_text = _signature_component_display(curr)
            label = curr_label or prev_label
            if prev_text != curr_text:
                if not prev_text and curr_text:
                    lines.append(f"Added {label}: {curr_text}")
                    continue
                if prev_text and not curr_text:
                    lines.append(f"Removed {label}")
                    continue
                lines.append(f"Updated {label}: {prev_text} -> {curr_text}")
    return lines


def _log_specific_genotype_changes(
    *,
    user,
    mouse: Mouse,
    before_signature: list[tuple],
    after_signature: list[tuple],
    source_label: str,
) -> None:
    if after_signature == before_signature:
        return
    lines = _build_specific_genotype_history_lines(before_signature, after_signature)
    if not lines:
        return
    message = f"Genotype changes ({source_label}):\n" + "\n".join(f"- {line}" for line in lines)
    log_audit_event(
        user=user,
        action=AuditLog.Action.UPDATE,
        obj=mouse,
        message=message[:4000],
    )


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
                    category=StrainLine.Category.COMPOUND_STRAIN,
                    notes="Auto-created during mouse import.",
                )
                for name in missing_strains
            ]
        )
        if getattr(acting_user, "is_authenticated", False):
            StrainLine.objects.filter(line_name__in=missing_strains).update(
                created_by_id=acting_user.pk,
                updated_by_id=acting_user.pk,
                owner_id=acting_user.pk,
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
        if getattr(acting_user, "is_authenticated", False):
            Project.objects.filter(name__in=missing_projects).update(
                created_by_id=acting_user.pk, updated_by_id=acting_user.pk
            )
        project_lookup = {project.name: project for project in Project.objects.all()}

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
        if getattr(acting_user, "is_authenticated", False):
            Cage.objects.filter(cage_id__in=missing_cages).update(
                created_by_id=acting_user.pk, updated_by_id=acting_user.pk
            )
        cage_lookup = {cage.cage_id: cage for cage in Cage.objects.all()}

    if errors:
        raise MouseImportExecutionError(errors)

    # Preserve currently-existing mice for optional pedigree resolution behavior.
    uids_in_file = [row["mouse_uid"] for row in rows]
    preexisting_mouse_lookup = {mouse.mouse_uid: mouse for mouse in Mouse.objects.filter(mouse_uid__in=uids_in_file)}

    mice_by_uid: dict[str, Mouse] = {}
    mice_to_create: list[Mouse] = []
    updated_mouse_count = 0
    for row in rows:
        row_number = row["row_number"]
        strain_name = _normalize_name(row.get("strain_line_name"))
        strain_line = strain_lookup.get(strain_name)
        if strain_line is None:
            errors.append(f"Row {row_number}: unresolved strain_line '{strain_name}'.")
            continue
        project_name = _normalize_name(row.get("project_name"))
        if not project_name:
            errors.append(f"Row {row_number}: project is required for ownership control.")
            continue
        project = project_lookup.get(project_name)
        if project is None:
            errors.append(f"Row {row_number}: unresolved project '{project_name}'.")
            continue
        if not can_edit_project_data(acting_user, project):
            errors.append(f"Row {row_number}: project '{project_name}': you do not have edit permission.")
            continue

        existing = preexisting_mouse_lookup.get(row["mouse_uid"])
        if existing is not None:
            if existing.project_id and existing.project_id != project.pk:
                if not can_edit_project_data(acting_user, existing.project):
                    errors.append(
                        f"Row {row_number}: mouse '{row['mouse_uid']}' belongs to project "
                        f"'{existing.project.name}' which you cannot edit."
                    )
                    continue
            existing.sex = row["sex"]
            existing.birth_date = row["birth_date"]
            existing.status = row["status"]
            existing.strain_line = strain_line
            existing.project = project
            mice_by_uid[row["mouse_uid"]] = existing
            updated_mouse_count += 1
            continue

        mice_to_create.append(
            Mouse(
                mouse_uid=row["mouse_uid"],
                sex=row["sex"],
                birth_date=row["birth_date"],
                status=row["status"],
                strain_line=strain_line,
                project=project,
            )
        )

    if errors:
        raise MouseImportExecutionError(errors)

    if mice_to_create:
        Mouse.objects.bulk_create(mice_to_create)
        if getattr(acting_user, "is_authenticated", False):
            Mouse.objects.filter(mouse_uid__in=[m.mouse_uid for m in mice_to_create]).update(
                created_by_id=acting_user.pk,
                updated_by_id=acting_user.pk,
            )
    mice_by_uid.update({mouse.mouse_uid: mouse for mouse in Mouse.objects.filter(mouse_uid__in=uids_in_file)})

    pedigree_lookup = {
        mouse.mouse_uid: mouse
        for mouse in Mouse.objects.filter(mouse_uid__in=referenced_pedigree_uids)
    }
    if options.resolve_pedigree_within_file:
        pedigree_lookup.update(mice_by_uid)

    mice_with_membership: list[tuple[Mouse, int | None]] = []
    for row in rows:
        row_number = row["row_number"]
        mouse = mice_by_uid.get(row["mouse_uid"])
        if mouse is None:
            errors.append(f"Row {row_number}: failed to materialize mouse '{row['mouse_uid']}'.")
            continue

        project_name = _normalize_name(row.get("project_name"))
        project = project_lookup.get(project_name)
        if project is None:
            errors.append(f"Row {row_number}: unresolved project '{project_name}'.")
            continue

        previous_cage_id = mouse.current_cage_id

        current_cage = None
        cage_id = _normalize_name(row.get("current_cage_id"))
        if cage_id:
            current_cage = cage_lookup.get(cage_id)
            if current_cage is None:
                errors.append(f"Row {row_number}: unresolved current_cage '{cage_id}'.")

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
        if getattr(acting_user, "is_authenticated", False):
            mouse.updated_by_id = acting_user.pk
        mouse.save()
        if current_cage and current_cage.id != previous_cage_id:
            mice_with_membership.append((mouse, previous_cage_id))

    if errors:
        raise MouseImportExecutionError(errors)

    for mouse, previous_cage_id in mice_with_membership:
        if previous_cage_id:
            CageMembership.objects.filter(mouse=mouse, is_current=True).update(
                is_current=False,
                end_date=import_date,
            )
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
            for mouse, _previous_cage_id in mice_with_membership
            if mouse.current_cage_id
        ]
    )

    # Pass 3: import structured genotype components from per-locus columns / legacy slots.
    genotype_to_create: list[MouseGenotypeComponent] = []
    genotype_to_update: list[MouseGenotypeComponent] = []
    existing_by_mouse_locus = {
        (gt.mouse_id, (gt.locus_name or "").casefold()): gt
        for gt in MouseGenotypeComponent.objects.filter(mouse_id__in=[m.id for m in mice_by_uid.values()])
    }
    for row in rows:
        mouse = mice_by_uid.get(row["mouse_uid"])
        if mouse is None:
            continue
        for slot in row.get("genotype_components", row.get("genotype_slots", [])):
            locus_name = (slot.get("locus_name") or "").strip()
            if not locus_name:
                continue
            key = (mouse.id, locus_name.casefold())
            existing = existing_by_mouse_locus.get(key)
            if existing is None:
                obj = MouseGenotypeComponent(
                    mouse=mouse,
                    strain_line=mouse.strain_line,
                    locus_name=locus_name,
                    chromosome_type=slot.get("chromosome_type") or MouseGenotypeComponent.ChromosomeType.UNKNOWN,
                    zygosity_class=slot.get("zygosity_class") or MouseGenotypeComponent.ZygosityClass.UNKNOWN,
                    zygosity=slot.get("zygosity_display", ""),
                    allele_display_1=slot.get("allele_1", ""),
                    allele_display_2=slot.get("allele_2", ""),
                    sort_order=slot.get("slot", 0) or 0,
                    notes=slot["notes"],
                )
                genotype_to_create.append(obj)
            else:
                existing.strain_line = mouse.strain_line
                existing.chromosome_type = slot.get("chromosome_type") or existing.chromosome_type
                existing.zygosity_class = slot.get("zygosity_class") or existing.zygosity_class
                existing.zygosity = slot.get("zygosity_display", "")
                existing.allele_display_1 = slot.get("allele_1", "")
                existing.allele_display_2 = slot.get("allele_2", "")
                existing.notes = slot["notes"]
                genotype_to_update.append(existing)

    if genotype_to_create:
        MouseGenotypeComponent.objects.bulk_create(genotype_to_create)
    if genotype_to_update:
        MouseGenotypeComponent.objects.bulk_update(
            genotype_to_update,
            [
                "strain_line",
                "chromosome_type",
                "zygosity_class",
                "zygosity",
                "allele_display_1",
                "allele_display_2",
                "notes",
            ],
        )
    if genotype_to_create or genotype_to_update:
        for mouse in mice_by_uid.values():
            mouse.rebuild_genotype_summary(save=True)

    created_mouse_count = len(mice_to_create)
    return {
        "created_mice": created_mouse_count,
        "updated_mice": updated_mouse_count,
        "auto_created_strain_lines": len(missing_strains),
        "auto_created_projects": len(missing_projects),
        "auto_created_cages": len(missing_cages),
        "genotype_rows_created": len(genotype_to_create),
        "genotype_rows_updated": len(genotype_to_update),
    }


@authenticated_required
def mouse_genotype_components_edit(request: HttpRequest, pk: int) -> HttpResponse:
    mouse = get_object_or_404(
        Mouse.objects.select_related(
            "project",
            "strain_line",
            "sire",
            "sire__strain_line",
            "dam",
            "dam__strain_line",
        ),
        pk=pk,
    )
    ensure_can_edit_project_data(request.user, mouse.project)
    template_rows = _template_loci_union_for_mouse_relations(
        sire=mouse.sire if mouse.sire_id else None,
        dam=mouse.dam if mouse.dam_id else None,
    )
    used_parent_union = bool(template_rows)
    if not template_rows and mouse.strain_line_id:
        template_rows = mouse.strain_line.expected_loci_entries()
    template_loci = [row.get("locus_name", "") for row in template_rows if (row.get("locus_name") or "").strip()]
    mouse.ensure_template_genotype_components(
        extra_loci=template_loci,
        include_strain_template=False,
    )
    before_signature = _genotype_components_signature(mouse)
    posted_genotype_rows: list[dict[str, str]] = []
    if request.method == "POST":
        posted_genotype_rows = _extract_mouse_genotype_rows_from_post(request)
        _apply_strain_template_resolution(mouse, mode="replace", target_loci=template_loci)
        _apply_mouse_genotype_rows_to_template(mouse, posted_genotype_rows, template_rows)
        mouse.refresh_from_db()
        after_signature = _genotype_components_signature(mouse)
        _log_specific_genotype_changes(
            user=request.user,
            mouse=mouse,
            before_signature=before_signature,
            after_signature=after_signature,
            source_label="Edit Genotype",
        )
        messages.success(request, "Genotype components updated.")
        return redirect("mice:mouse_detail", pk=mouse.pk)

    template_source_label = (
        "Parent union template (sire + dam)"
        if used_parent_union
        else ("Strain-line template" if mouse.strain_line_id else "No template loci available")
    )
    existing_genotype_map = {}
    for component in mouse.genotype_components.all():
        locus = StrainLine.normalize_locus_name((component.locus_name or "").strip())
        if not locus:
            continue
        value = (component.zygosity or "").strip()
        if value:
            existing_genotype_map[locus] = value
    return render(
        request,
        "colony/mouse_genotype_components_form.html",
        {
            "mouse": mouse,
            "template_source_label": template_source_label,
            "template_rows": template_rows,
            "existing_genotype_map": existing_genotype_map,
            "posted_genotype_rows": posted_genotype_rows,
        },
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
    strain_line = (request.GET.get("strain_line") or request.GET.get("strain_line_id") or "").strip()
    export = (request.GET.get("export") or "").strip().lower()

    cages = _filtered_cages_queryset(request)
    if export in {"csv", "xlsx"}:
        return _cages_export_http_response(request, export)

    cages = apply_list_sort(cages, request, CAGE_LIST_SORT)

    strain_line_filter_label = ""
    if strain_line:
        strain_line_filter_label = (
            StrainLine.objects.filter(pk=strain_line).values_list("line_name", flat=True).first() or ""
        )

    page_ctx = paginate_queryset_for_list(request, cages, viewname="colony:cage_list")
    cages_page = list(page_ctx.pop("items"))
    for cage in cages_page:
        cage_mice = list(cage.current_mice.all().order_by("mouse_uid"))
        cage.current_mouse_count = len(cage_mice)
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
        "strain_line": strain_line,
        "strain_line_filter_label": strain_line_filter_label,
        "room_options": Cage.objects.exclude(room="").values_list("room", flat=True).distinct().order_by("room"),
        "rack_options": Cage.objects.exclude(rack="").values_list("rack", flat=True).distinct().order_by("rack"),
        "cage_type_options": Cage.CageType.choices,
        "purpose_options": Cage.Purpose.choices,
        "status_options": Cage.Status.choices,
        "list_all_max": LIST_ALL_RESULTS_MAX,
        **build_list_sort_context(request, "colony:cage_list", CAGE_LIST_SORT),
        **page_ctx,
    }
    return render(request, "colony/cage_list.html", context)


def _strain_line_usage_annotations() -> dict:
    active_litter_statuses = [
        Litter.LitterStatus.ACTIVE,
        Litter.LitterStatus.WEANED,
        Litter.LitterStatus.TAIL_TAGGED,
    ]
    return {
        "active_mice_count": Count("mice", filter=Q(mice__status=Mouse.Status.ACTIVE), distinct=True),
        "total_mice_count": Count("mice", distinct=True),
        "active_cages_count": Count(
            "mice__current_cage",
            filter=Q(mice__status=Mouse.Status.ACTIVE, mice__current_cage__isnull=False),
            distinct=True,
        ),
        "total_cages_count": Count(
            "mice__current_cage",
            filter=Q(mice__current_cage__isnull=False),
            distinct=True,
        ),
        "active_breedings_count": Count(
            "mice__maternal_breedings_primary",
            filter=Q(mice__maternal_breedings_primary__active=True),
            distinct=True,
        )
        + Count(
            "mice__sired_breedings",
            filter=Q(mice__sired_breedings__active=True),
            distinct=True,
        ),
        "total_breedings_count": Count("mice__maternal_breedings_primary", distinct=True)
        + Count("mice__sired_breedings", distinct=True),
        "active_litters_count": Count(
            "mice__maternal_breedings_primary__litters",
            filter=Q(mice__maternal_breedings_primary__litters__litter_status__in=active_litter_statuses),
            distinct=True,
        ),
        "total_litters_count": Count("mice__maternal_breedings_primary__litters", distinct=True),
    }


@authenticated_required
def strain_line_list(request: HttpRequest) -> HttpResponse:
    q = (request.GET.get("q") or "").strip()
    active = (request.GET.get("active") or "yes").strip()
    lines = StrainLine.objects.select_related("owner", "owner__profile", "created_by", "created_by__profile").annotate(
        pdf_count=Count("documents")
    )
    if q:
        lines = lines.filter(
            Q(name__icontains=q)
            | Q(line_name__icontains=q)
            | Q(display_name__icontains=q)
            | Q(key_name__icontains=q)
            | Q(expected_loci_template__icontains=q)
            | Q(notes__icontains=q)
            | Q(owner__username__icontains=q)
            | Q(owner__first_name__icontains=q)
            | Q(owner__last_name__icontains=q)
            | Q(owner__profile__display_name__icontains=q)
        )
    if active == "yes":
        lines = lines.filter(is_active=True)
    elif active == "no":
        lines = lines.filter(is_active=False)
    lines = lines.annotate(**_strain_line_usage_annotations())
    lines = apply_list_sort(lines, request, STRAIN_LINE_LIST_SORT)
    context = {
        "lines": lines,
        "q": q,
        "active": active,
        **build_list_sort_context(request, "colony:strain_line_list", STRAIN_LINE_LIST_SORT),
    }
    return render(request, "colony/strain_line_list.html", context)


@authenticated_required
def strain_line_detail(request: HttpRequest, pk: int) -> HttpResponse:
    line = get_object_or_404(
        StrainLine.objects.annotate(**_strain_line_usage_annotations())
        .select_related(
            "owner",
            "owner__profile",
            "default_project",
            "default_project__owner",
            "default_project__owner__profile",
            "created_by",
            "created_by__profile",
            "updated_by",
            "updated_by__profile",
        ),
        pk=pk,
    )
    documents = list(
        line.documents.select_related("uploaded_by", "uploaded_by__profile").order_by("created_at", "id")
    )
    audit_entries = audit_entries_for_object("StrainLine", line.pk)
    actors = merge_actor_labels(line, audit_entries)
    related_mice = list(
        Mouse.objects.filter(strain_line=line)
        .select_related("current_cage", "project")
        .order_by("status", "mouse_uid")
    )
    related_cages = list(
        Cage.objects.filter(current_mice__strain_line=line)
        .distinct()
        .prefetch_related("current_mice__strain_line")
        .order_by("status", "cage_id")
    )
    for cage in related_cages:
        strain_mice = [mouse for mouse in cage.current_mice.all() if mouse.strain_line_id == line.pk]
        cage.strain_active_mouse_count = sum(1 for mouse in strain_mice if mouse.status == Mouse.Status.ACTIVE)
        cage.strain_total_mouse_count = len(strain_mice)
    related_projects = list(
        Project.objects.filter(mice__strain_line=line)
        .select_related("owner", "owner__profile")
        .annotate(
            strain_active_mice_count=Count(
                "mice",
                filter=Q(mice__strain_line=line, mice__status=Mouse.Status.ACTIVE),
                distinct=True,
            ),
            strain_total_mice_count=Count("mice", filter=Q(mice__strain_line=line), distinct=True),
        )
        .distinct()
        .order_by("name")
    )
    return render(
        request,
        "colony/strain_line_detail.html",
        {
            "line": line,
            "related_mice": related_mice,
            "related_cages": related_cages,
            "related_projects": related_projects,
            "documents": documents,
            "pdf_count": len(documents),
            "pdf_slots_remaining": max(0, MAX_STRAIN_LINE_PDF_COUNT - len(documents)),
            "audit_entries": audit_entries,
            **actors,
        },
    )


@authenticated_required
def strain_line_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = StrainLineForm(request.POST, user=request.user)
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
        form = StrainLineForm(user=request.user, initial={"owner": request.user})
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
@require_POST
def strain_line_upload_documents(request: HttpRequest, pk: int) -> HttpResponse:
    line = get_object_or_404(StrainLine, pk=pk)
    next_url = (request.POST.get("next") or "").strip()
    if not next_url:
        next_url = reverse("colony:strain_line_detail", kwargs={"pk": line.pk})

    uploads = request.FILES.getlist("pdf_files")
    if not uploads:
        messages.error(request, "No PDF files selected.")
        return redirect(next_url)

    existing = line.documents.count()
    if existing + len(uploads) > MAX_STRAIN_LINE_PDF_COUNT:
        messages.error(
            request,
            f"This strain line already has {existing} PDF(s). "
            f"You can attach at most {MAX_STRAIN_LINE_PDF_COUNT} in total.",
        )
        return redirect(next_url)

    created = 0
    for uploaded in uploads:
        try:
            validate_strain_line_pdf_file(uploaded)
        except ValidationError as exc:
            messages.error(request, exc.messages[0] if getattr(exc, "messages", None) else str(exc))
            continue
        StrainLineDocument.objects.create(
            strain_line=line,
            file=uploaded,
            uploaded_by=request.user,
        )
        created += 1

    if created:
        messages.success(request, f"Uploaded {created} PDF file(s).")
    return redirect(next_url)


@authenticated_required
def strain_line_document_download(request: HttpRequest, pk: int, doc_pk: int) -> HttpResponse:
    doc = get_object_or_404(StrainLineDocument.objects.select_related("strain_line"), pk=doc_pk, strain_line_id=pk)
    if not doc.file:
        raise Http404("File not found.")
    try:
        handle = doc.file.open("rb")
    except FileNotFoundError as exc:
        raise Http404("File not found on disk.") from exc
    response = FileResponse(handle, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{doc.display_name}"'
    return response


@authenticated_required
@require_POST
def strain_line_document_delete(request: HttpRequest, pk: int, doc_pk: int) -> HttpResponse:
    doc = get_object_or_404(StrainLineDocument, pk=doc_pk, strain_line_id=pk)
    next_url = (request.POST.get("next") or "").strip()
    if not next_url:
        next_url = reverse("colony:strain_line_detail", kwargs={"pk": pk})
    label = doc.display_name
    if doc.file:
        doc.file.delete(save=False)
    doc.delete()
    messages.success(request, f"Removed PDF “{label}”.")
    return redirect(next_url)


@authenticated_required
def strain_line_edit(request: HttpRequest, pk: int) -> HttpResponse:
    line = get_object_or_404(StrainLine, pk=pk)
    documents = list(
        line.documents.select_related("uploaded_by", "uploaded_by__profile").order_by("created_at", "id")
    )
    previous_active = line.is_active
    if request.method == "POST":
        form = StrainLineForm(request.POST, instance=line, user=request.user)
        if form.is_valid():
            if form.cleaned_data.get("is_active") != previous_active and not can_import(request.user):
                raise PermissionDenied("Only managers or admins can archive/deactivate strain lines.")
            before_entries = line.expected_loci_entries()
            before_template = json.dumps(before_entries, sort_keys=True)
            before_name = (line.name or line.line_name or "").strip()
            msg = summarize_modelform_changes(form)
            line = form.save()
            after_entries = line.expected_loci_entries()
            after_template = json.dumps(after_entries, sort_keys=True)
            after_name = (line.name or line.line_name or "").strip()
            log_audit_event(
                user=request.user,
                action=AuditLog.Action.UPDATE,
                obj=line,
                message=msg[:4000],
            )
            messages.success(request, "Strain line updated.")
            if before_template != after_template or before_name != after_name:
                mice_updated, rows_added, rows_removed = _propagate_strain_line_template_to_mice(
                    line,
                    before_entries=before_entries if before_template != after_template else None,
                )
                if mice_updated:
                    detail_parts = [f"synced {mice_updated} mouse(s)"]
                    if rows_added:
                        detail_parts.append(f"{rows_added} locus row(s) added")
                    if rows_removed:
                        detail_parts.append(f"{rows_removed} locus row(s) removed from mice")
                    messages.info(
                        request,
                        "Definition changes applied: " + ", ".join(detail_parts) + "; genotype summaries refreshed.",
                    )
            return redirect("colony:strain_line_detail", pk=line.pk)
    else:
        form = StrainLineForm(instance=line, user=request.user)
    return render(
        request,
        "colony/strain_line_form.html",
        {
            "form": form,
            "line": line,
            "documents": documents,
            "pdf_count": len(documents),
            "pdf_slots_remaining": max(0, MAX_STRAIN_LINE_PDF_COUNT - len(documents)),
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
            breeding = sync_cage_breeding_workflow(cage)
            sync_cage_status_from_mice(cage)
            if cage.purpose == Cage.Purpose.BREEDING:
                if breeding:
                    messages.success(
                        request,
                        f"Breeding {breeding.breeding_code} created for cage {cage.cage_id}.",
                    )
                else:
                    setup_msg = breeding_setup_message(cage)
                    if setup_msg:
                        messages.warning(request, setup_msg)
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
            breeding = sync_cage_breeding_workflow(cage)
            sync_cage_status_from_mice(cage)
            if cage.purpose == Cage.Purpose.BREEDING:
                if breeding:
                    messages.success(
                        request,
                        f"Breeding {breeding.breeding_code} linked to cage {cage.cage_id}.",
                    )
                else:
                    setup_msg = breeding_setup_message(cage)
                    if setup_msg:
                        messages.warning(request, setup_msg)
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
    overwrite_context: dict = {}

    if request.method == "POST" and request.POST.get("save_import_prefix"):
        prefix_form = UserImportPrefixForm(request.POST, instance=profile)
        if prefix_form.is_valid():
            prefix_form.save()
            messages.success(request, "Import ID prefix saved.")
            return redirect("colony:cage_import")
        form = CageImportForm(user=request.user)
    elif request.method == "POST" and request.POST.get("cancel_overwrite") == "1":
        clear_staged_cage_import(request)
        messages.info(request, "Import cancelled.")
        return redirect("colony:cage_import")
    elif request.method == "POST" and request.POST.get("confirm_overwrite") == "1":
        staged = pop_staged_cage_import(request)
        if not staged:
            messages.error(request, "Import confirmation expired. Please upload the file again.")
            return redirect("colony:cage_import")
        try:
            handle = file_bytes_to_upload(decode_staged_file(staged["content_b64"]), staged["filename"])
            result = parse_cage_import(
                handle,
                id_prefix=staged["id_prefix"],
                update_existing=staged["update_existing"],
            )
        except ImportStagingError as exc:
            messages.error(request, str(exc))
            return redirect("colony:cage_import")
        upload_name = staged["filename"] or ""
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
            created_count, updated_count = _apply_cage_import_rows(result.rows, acting_user=request.user)
            total = created_count + updated_count
            log_audit_event(
                user=request.user,
                action=AuditLog.Action.IMPORT,
                message=(
                    f"Imported {total} cages via file upload "
                    f"({created_count} created, {updated_count} updated)."
                ),
                object_type="Cage",
                object_id=str(total),
                object_repr="Bulk Cage Import",
            )
            record_import_log(
                user=request.user,
                import_type=ImportLog.ImportType.CAGE,
                filename=upload_name,
                success=True,
                created_count=total,
                errors=[],
            )
            messages.success(
                request,
                f"Import complete: {created_count} cage(s) created, {updated_count} updated.",
            )
            return redirect("colony:cage_list")
    elif request.method == "POST":
        form = CageImportForm(request.POST, request.FILES, user=request.user)
        prefix_form = UserImportPrefixForm(instance=profile)
        if form.is_valid():
            uploaded_file = form.cleaned_data["data_file"]
            upload_name = uploaded_file.name or ""
            id_prefix = None
            if form.cleaned_data.get("apply_import_prefix"):
                id_prefix = get_effective_import_prefix(request.user)
            update_existing = form.cleaned_data.get("update_existing", True)
            result = parse_cage_import(
                uploaded_file,
                id_prefix=id_prefix,
                update_existing=update_existing,
            )
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
                overwrite_context = (
                    _build_import_overwrite_context(
                        rows=result.rows,
                        id_key="cage_id",
                        record_label="cage",
                        staged_filename=upload_name,
                    )
                    or {}
                )
                if overwrite_context and update_existing:
                    try:
                        uploaded_file.seek(0)
                        stage_cage_import(
                            request,
                            filename=upload_name,
                            content=uploaded_file.read(),
                            id_prefix=id_prefix,
                            update_existing=update_existing,
                        )
                    except ImportStagingError as exc:
                        row_errors = [str(exc)]
                        overwrite_context = {}
                else:
                    created_count, updated_count = _apply_cage_import_rows(
                        result.rows,
                        acting_user=request.user,
                    )
                    total = created_count + updated_count
                    log_audit_event(
                        user=request.user,
                        action=AuditLog.Action.IMPORT,
                        message=(
                            f"Imported {total} cages via file upload "
                            f"({created_count} created, {updated_count} updated)."
                        ),
                        object_type="Cage",
                        object_id=str(total),
                        object_repr="Bulk Cage Import",
                    )
                    record_import_log(
                        user=request.user,
                        import_type=ImportLog.ImportType.CAGE,
                        filename=upload_name,
                        success=True,
                        created_count=total,
                        errors=[],
                    )
                    messages.success(
                        request,
                        f"Import complete: {created_count} cage(s) created, {updated_count} updated.",
                    )
                    return redirect("colony:cage_list")

    context = {
        "form": form,
        "prefix_form": prefix_form,
        "row_errors": row_errors,
        "expected_columns": EXPECTED_COLUMNS,
        "import_prefix_hint": get_effective_import_prefix(request.user),
        **overwrite_context,
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
    cage = get_object_or_404(
        _scoped_cage_queryset(request.user).select_related(
            "created_by", "created_by__profile", "updated_by", "updated_by__profile"
        ),
        pk=pk,
    )
    current_mice = list(
        _scoped_mouse_queryset(request.user)
        .filter(current_cage=cage)
        .select_related("project", "project__owner", "project__owner__profile")
        .prefetch_related("genotype_components__strain_line", "genotypes__gene")
        .order_by("mouse_uid")
    )
    breeding_badges_map = _active_breeding_badges_for_mouse_ids([m.pk for m in current_mice])
    current_mouse_rows = [
        {
            "mouse": mouse,
            "genotype_summary": build_short_genotype_summary(mouse),
            "active_breeding_badges": breeding_badges_map.get(mouse.pk, []),
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
    actors = merge_actor_labels(cage, audit_entries)
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
    q = request.GET.copy()
    q["export"] = "csv"
    qs = q.urlencode()
    url = reverse("colony:cage_list")
    return redirect(f"{url}?{qs}" if qs else url)


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
    q = request.GET.copy()
    q["export"] = "xlsx"
    qs = q.urlencode()
    url = reverse("colony:cage_list")
    return redirect(f"{url}?{qs}" if qs else url)


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


MICE_EXPORT_EXTRA_COLUMNS = [
    "owner",
    "genotype_summary",
    "death_date",
    "euthanasia_date",
    "death_reason",
    "created_at",
    "updated_at",
]


def _mice_export_headers() -> list[str]:
    return MOUSE_EXPECTED_COLUMNS + MICE_EXPORT_EXTRA_COLUMNS


def _mice_export_http_response(request: HttpRequest, export_fmt: str) -> HttpResponse:
    headers = _mice_export_headers()
    rows = get_mice_export_rows(request)
    if export_fmt == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="mice_export.csv"'
        writer = csv.writer(response)
        writer.writerow(headers)
        writer.writerows(rows)
    else:
        response = build_xlsx_response("mice.xlsx", "Mice", headers, rows)
    response["Cache-Control"] = "no-store"
    return response


@authenticated_required
def mouse_list(request: HttpRequest) -> HttpResponse:
    query = (request.GET.get("q") or "").strip()
    sex = (request.GET.get("sex") or "").strip()
    status = (request.GET.get("status") or "").strip()
    strain_line = (request.GET.get("strain_line") or request.GET.get("strain_line_id") or "").strip()
    current_cage = (request.GET.get("current_cage") or request.GET.get("cage_id") or "").strip()
    project = (request.GET.get("project") or request.GET.get("project_id") or "").strip()
    include_inactive = (request.GET.get("include_inactive") or "").strip()
    export = (request.GET.get("export") or "").strip().lower()
    age_sort = (request.GET.get("age_sort") or "").strip()
    if age_sort in ("old", "young") and not (request.GET.get("sort") or "").strip():
        q = request.GET.copy()
        q["sort"] = "age"
        q["dir"] = "desc" if age_sort == "old" else "asc"
        q.pop("age_sort", None)
        qs = q.urlencode()
        url = reverse("mice:mouse_list")
        return redirect(f"{url}?{qs}" if qs else url)

    mice = _filtered_mice_queryset(request).select_related(
        "project__owner",
        "project__owner__profile",
    )
    mice = mice.prefetch_related(
        "genotype_components__strain_line",
        "genotypes__gene",
    )

    mice = apply_list_sort(mice, request, MICE_LIST_SORT)

    if export in {"csv", "xlsx"}:
        return _mice_export_http_response(request, export)

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
        "sex_options": Mouse.Sex.choices,
        **build_list_sort_context(request, "mice:mouse_list", MICE_LIST_SORT),
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
    breeding_badges_map = _active_breeding_badges_for_mouse_ids([m.pk for m in mice_page])
    for m in mice_page:
        m.active_breeding_badges = breeding_badges_map.get(m.pk, [])
    return render(request, "colony/mouse_list.html", context)


@role_required(can_import)
def mouse_import(request: HttpRequest) -> HttpResponse:
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    row_errors: list[str] = []
    prefix_form = UserImportPrefixForm(instance=profile)
    form = MouseImportForm(user=request.user)
    overwrite_context: dict = {}

    if request.method == "POST" and request.POST.get("save_import_prefix"):
        prefix_form = UserImportPrefixForm(request.POST, instance=profile)
        if prefix_form.is_valid():
            prefix_form.save()
            messages.success(request, "Import ID prefix saved.")
            return redirect("mice:mouse_import")
        form = MouseImportForm(user=request.user)
    elif request.method == "POST" and request.POST.get("cancel_overwrite") == "1":
        clear_staged_mouse_import(request)
        messages.info(request, "Import cancelled.")
        return redirect("mice:mouse_import")
    elif request.method == "POST" and request.POST.get("confirm_overwrite") == "1":
        staged = pop_staged_mouse_import(request)
        if not staged:
            messages.error(request, "Import confirmation expired. Please upload the file again.")
            return redirect("mice:mouse_import")
        try:
            handle = file_bytes_to_upload(decode_staged_file(staged["content_b64"]), staged["filename"])
            result = parse_mouse_import(
                handle,
                id_prefix=staged["id_prefix"],
                update_existing=staged["update_existing"],
            )
        except ImportStagingError as exc:
            messages.error(request, str(exc))
            return redirect("mice:mouse_import")
        upload_name = staged["filename"] or ""
        import_options = _mouse_import_options_from_dict(staged["import_options"])
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
            response, exec_errors = _run_mouse_import_execution(
                request,
                result.rows,
                import_options=import_options,
                upload_name=upload_name,
            )
            if response is not None:
                return response
            row_errors = exec_errors
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
            update_existing = form.cleaned_data.get("update_existing", True)
            result = parse_mouse_import(
                uploaded_file,
                id_prefix=id_prefix,
                update_existing=update_existing,
            )
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
                overwrite_context = (
                    _build_import_overwrite_context(
                        rows=result.rows,
                        id_key="mouse_uid",
                        record_label="mouse",
                        staged_filename=upload_name,
                    )
                    or {}
                )
                if overwrite_context and update_existing:
                    try:
                        uploaded_file.seek(0)
                        stage_mouse_import(
                            request,
                            filename=upload_name,
                            content=uploaded_file.read(),
                            id_prefix=id_prefix,
                            update_existing=update_existing,
                            import_options={
                                "auto_create_missing_strain_lines": import_options.auto_create_missing_strain_lines,
                                "auto_create_missing_projects": import_options.auto_create_missing_projects,
                                "auto_create_missing_cages": import_options.auto_create_missing_cages,
                                "resolve_pedigree_within_file": import_options.resolve_pedigree_within_file,
                            },
                        )
                    except ImportStagingError as exc:
                        row_errors = [str(exc)]
                        overwrite_context = {}
                else:
                    response, exec_errors = _run_mouse_import_execution(
                        request,
                        result.rows,
                        import_options=import_options,
                        upload_name=upload_name,
                    )
                    if response is not None:
                        return response
                    row_errors = exec_errors

    context = {
        "form": form,
        "prefix_form": prefix_form,
        "row_errors": row_errors,
        "expected_columns": MOUSE_IMPORT_TEMPLATE_COLUMNS,
        "import_prefix_hint": get_effective_import_prefix(request.user),
        **overwrite_context,
    }
    return render(request, "colony/mouse_import.html", context)


@role_required(can_import)
def mouse_import_template(request: HttpRequest) -> HttpResponse:
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="mouse_import_template.csv"'
    writer = csv.writer(response)
    writer.writerow(MOUSE_IMPORT_TEMPLATE_COLUMNS)
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
            "Cre/+",
            "fl/fl",
            "+/-",
            "+/+",
            "+/+",
            "KI/+",
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
            "Cre/+",
            "fl/fl",
            "+/-",
            "+/+",
            "+/+",
            "KI/+",
        ]
    ]
    return build_xlsx_response("mouse_import_template.xlsx", "MouseTemplate", MOUSE_IMPORT_TEMPLATE_COLUMNS, rows)


@authenticated_required
def mice_export(request: HttpRequest) -> HttpResponse:
    q = request.GET.copy()
    q["export"] = "csv"
    qs = q.urlencode()
    url = reverse("mice:mouse_list")
    return redirect(f"{url}?{qs}" if qs else url)


@authenticated_required
def mice_export_xlsx(request: HttpRequest) -> HttpResponse:
    q = request.GET.copy()
    q["export"] = "xlsx"
    qs = q.urlencode()
    url = reverse("mice:mouse_list")
    return redirect(f"{url}?{qs}" if qs else url)


@authenticated_required
def mouse_detail(request: HttpRequest, pk: int) -> HttpResponse:
    mouse = get_object_or_404(
        _scoped_mouse_queryset(request.user).select_related(
            "sire",
            "dam",
            "project",
            "project__owner",
            "project__owner__profile",
            "created_by",
            "created_by__profile",
            "updated_by",
            "updated_by__profile",
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
    genotype_history_entries = [
        entry for entry in mouse_audit_entries if "genotype" in (entry.message or "").casefold()
    ]
    actors = merge_actor_labels(mouse, mouse_audit_entries)
    active_breeding_badges = _active_breeding_badges_for_mouse_ids([mouse.pk]).get(mouse.pk, [])
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
        "genotype_history_entries": genotype_history_entries,
        "active_breeding_badges": active_breeding_badges,
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
    sex = (request.GET.get("sex") or "").strip()
    strain_line = (request.GET.get("strain_line") or request.GET.get("strain_line_id") or "").strip()
    project = (request.GET.get("project") or request.GET.get("project_id") or "").strip()
    include_inactive = (request.GET.get("include_inactive") or "").strip()
    parent = (request.GET.get("parent") or "").strip()

    mice = (
        _scoped_mouse_queryset(request.user)
        .select_related(
            "sire",
            "dam",
            "current_cage",
            "strain_line",
            "project",
            "project__owner",
            "project__owner__profile",
        )
        .prefetch_related("genotype_components__strain_line", "genotypes__gene")
    )
    if include_inactive != "yes":
        mice = mice.filter(status=Mouse.Status.ACTIVE)
    if q:
        mice = mice.filter(Q(mouse_uid__icontains=q) | Q(ear_tag__icontains=q) | Q(toe_tag__icontains=q))
    if sex:
        mice = mice.filter(sex=sex)
    if strain_line:
        mice = mice.filter(strain_line_id=strain_line)
    if project:
        mice = mice.filter(project_id=project)
    if parent == "sire":
        mice = mice.filter(sire__isnull=False)
    elif parent == "dam":
        mice = mice.filter(dam__isnull=False)
    elif parent == "both":
        mice = mice.filter(sire__isnull=False, dam__isnull=False)
    elif parent == "either":
        mice = mice.filter(Q(sire__isnull=False) | Q(dam__isnull=False))
    elif parent == "none":
        mice = mice.filter(sire__isnull=True, dam__isnull=True)

    mice = list(apply_list_sort(mice, request, FAMILY_TREE_SORT)[:80])
    for m in mice:
        m.family_genotype_summary = build_short_genotype_summary(m)
    strain_line_model = Mouse._meta.get_field("strain_line").related_model
    return render(
        request,
        "colony/family_tree.html",
        {
            "mice": mice,
            "q": q,
            "sex": sex,
            "strain_line": strain_line,
            "project": project,
            "include_inactive": include_inactive,
            "parent": parent,
            "sex_options": Mouse.Sex.choices,
            "strain_line_options": strain_line_model.objects.order_by("line_name"),
            "project_options": Project.objects.order_by("name"),
            "parent_options": [
                ("", "All parent records"),
                ("either", "Has sire or dam"),
                ("both", "Has sire and dam"),
                ("sire", "Has sire only"),
                ("dam", "Has dam only"),
                ("none", "No parents on record"),
            ],
            **build_list_sort_context(request, "mice:family_tree", FAMILY_TREE_SORT),
        },
    )


@authenticated_required
def mouse_create(request: HttpRequest) -> HttpResponse:
    posted_genotype_rows: list[dict[str, str]] = []
    if request.method == "POST":
        form = MouseForm(request.POST, user=request.user)
        posted_genotype_rows = _extract_mouse_genotype_rows_from_post(request)
        if form.is_valid():
            ensure_can_edit_project_data(request.user, form.cleaned_data.get("project"))
            mouse = form.save()
            before_signature = _genotype_components_signature(mouse)
            template_loci = _resolved_template_loci_for_context(
                strain_line=mouse.strain_line,
                sire=mouse.sire,
                dam=mouse.dam,
            )
            prefilled = mouse.ensure_template_genotype_components(
                extra_loci=template_loci,
                include_strain_template=False,
            )
            filled = _apply_mouse_genotype_rows(mouse, posted_genotype_rows)
            log_audit_event(
                user=request.user,
                action=AuditLog.Action.CREATE,
                obj=mouse,
                message=f"Created mouse {mouse.mouse_uid}.",
            )
            after_signature = _genotype_components_signature(mouse)
            _log_specific_genotype_changes(
                user=request.user,
                mouse=mouse,
                before_signature=before_signature,
                after_signature=after_signature,
                source_label="New Mouse form",
            )
            if prefilled:
                messages.info(
                    request,
                    f"Pre-populated {prefilled} genotype template row(s) from strain line '{mouse.strain_line}'.",
                )
            if filled:
                messages.info(request, f"Applied {filled} genotype row(s) from New Mouse form.")
            return redirect("mice:mouse_detail", pk=mouse.pk)
    else:
        initial: dict = {}
        strain_id = (request.GET.get("strain_line_id") or "").strip()
        if strain_id.isdigit():
            line = (
                StrainLine.objects.filter(pk=int(strain_id))
                .select_related("default_project")
                .first()
            )
            if line:
                initial["strain_line"] = line.pk
                default_project_id = _effective_default_project_id(line)
                if default_project_id:
                    initial["project"] = default_project_id
        form = MouseForm(user=request.user, initial=initial)

    context = {
        "form": form,
        "page_title": "Create Mouse",
        "submit_label": "Save Mouse",
        "cancel_url": "mice:mouse_list",
        "strain_template_loci_map": _strain_template_loci_map(),
        "mouse_strain_line_map": _mouse_to_strain_line_map(),
        "strain_default_project_map": _strain_default_project_map(),
        "apply_strain_default_project": True,
        "existing_genotype_map": {},
        "posted_genotype_rows": posted_genotype_rows,
    }
    return render(request, "colony/mouse_form.html", context)


@authenticated_required
def mouse_edit(request: HttpRequest, pk: int) -> HttpResponse:
    mouse = get_object_or_404(_scoped_mouse_queryset(request.user), pk=pk)
    ensure_can_edit_project_data(request.user, mouse.project)
    previous_status = mouse.status
    original_strain = mouse.strain_line
    original_strain_id = mouse.strain_line_id
    strain_change_action = (request.POST.get("strain_change_action") or "").strip().lower() if request.method == "POST" else ""
    posted_genotype_rows: list[dict[str, str]] = []
    if request.method == "POST":
        form = MouseForm(request.POST, instance=mouse, user=request.user)
        posted_genotype_rows = _extract_mouse_genotype_rows_from_post(request)
        if form.is_valid():
            before_signature = _genotype_components_signature(mouse)
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
            new_strain = form.cleaned_data.get("strain_line")
            strain_changed = bool(original_strain_id and new_strain and original_strain_id != new_strain.id)
            has_truth = _mouse_has_meaningful_genotype_truth(mouse)
            if strain_changed and has_truth and strain_change_action not in {"replace", "overlap", "cancel"}:
                old_loci = _resolved_template_loci_for_context(
                    strain_line=original_strain,
                    sire=mouse.sire,
                    dam=mouse.dam,
                )
                new_loci = _resolved_template_loci_for_context(
                    strain_line=new_strain,
                    sire=mouse.sire,
                    dam=mouse.dam,
                )
                old_keys = {StrainLine.normalize_locus_name(x).casefold() for x in old_loci if StrainLine.normalize_locus_name(x)}
                new_keys = {StrainLine.normalize_locus_name(x).casefold() for x in new_loci if StrainLine.normalize_locus_name(x)}
                overlap = sorted([l for l in new_loci if StrainLine.normalize_locus_name(l).casefold() in old_keys])
                to_add = sorted([l for l in new_loci if StrainLine.normalize_locus_name(l).casefold() not in old_keys])
                to_remove = sorted([l for l in old_loci if StrainLine.normalize_locus_name(l).casefold() not in new_keys])
                post_payload: list[tuple[str, str]] = []
                for key, values in request.POST.lists():
                    if key in {"csrfmiddlewaretoken", "strain_change_action"}:
                        continue
                    for value in values:
                        post_payload.append((key, value))
                return render(
                    request,
                    "colony/mouse_strain_change_confirm.html",
                    {
                        "mouse": mouse,
                        "old_strain": original_strain,
                        "new_strain": new_strain,
                        "overlap_loci": overlap,
                        "add_loci": to_add,
                        "remove_loci": to_remove,
                        "post_payload": post_payload,
                    },
                )
            if strain_change_action == "cancel":
                messages.info(request, "Strain-line change was cancelled.")
                return redirect("mice:mouse_detail", pk=mouse.pk)
            msg = summarize_modelform_changes(form)
            mouse = form.save()
            template_loci = _resolved_template_loci_for_context(
                strain_line=mouse.strain_line,
                sire=mouse.sire,
                dam=mouse.dam,
            )
            prefilled = 0
            filled = 0
            if strain_changed:
                if has_truth and strain_change_action in {"replace", "overlap"}:
                    _apply_strain_template_resolution(mouse, mode=strain_change_action, target_loci=template_loci)
                    if strain_change_action == "replace":
                        messages.info(request, "Strain changed: replaced genotype loci with new template.")
                    else:
                        messages.info(request, "Strain changed: kept overlapping loci only.")
                else:
                    # No meaningful genotype truth yet: safely replace template rows.
                    _apply_strain_template_resolution(mouse, mode="replace", target_loci=template_loci)
                    messages.info(request, "Strain changed: replaced empty template loci with new template.")
            else:
                prefilled = mouse.ensure_template_genotype_components(
                    extra_loci=template_loci,
                    include_strain_template=False,
                )
                filled = _apply_mouse_genotype_rows(mouse, posted_genotype_rows)
            log_audit_event(
                user=request.user,
                action=AuditLog.Action.UPDATE,
                obj=mouse,
                message=msg[:4000],
            )
            after_signature = _genotype_components_signature(mouse)
            _log_specific_genotype_changes(
                user=request.user,
                mouse=mouse,
                before_signature=before_signature,
                after_signature=after_signature,
                source_label="Edit Mouse form",
            )
            if prefilled:
                messages.info(
                    request,
                    f"Added {prefilled} missing genotype template row(s) from strain line '{mouse.strain_line}'.",
                )
            if filled:
                messages.info(request, f"Applied {filled} genotype row(s) from mouse form.")
            return redirect("mice:mouse_detail", pk=mouse.pk)
    else:
        form = MouseForm(instance=mouse, user=request.user)

    context = {
        "form": form,
        "page_title": f"Edit Mouse {mouse.mouse_uid}",
        "submit_label": "Save Changes",
        "cancel_url": "mice:mouse_detail",
        "cancel_kwargs": {"pk": mouse.pk},
        "strain_template_loci_map": _strain_template_loci_map(),
        "mouse_strain_line_map": _mouse_to_strain_line_map(),
        "apply_strain_default_project": False,
        "existing_genotype_map": {
            (c.locus_name or "").strip(): (c.zygosity or "")
            for c in mouse.genotype_components.all()
            if (c.locus_name or "").strip()
        },
        "posted_genotype_rows": posted_genotype_rows,
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
