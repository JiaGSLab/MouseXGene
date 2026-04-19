import csv
from io import BytesIO

from django import forms
from django.contrib import messages
from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponse
from django.db.models import Q
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from openpyxl import Workbook

from colony.models import Mouse
from .forms import GeneForm, GenotypeImportForm, MouseGenotypeForm
from .importers import GENOTYPE_EXPECTED_COLUMNS, parse_genotype_import
from .models import Gene, MouseGenotype
from colony.models import StrainLine
from core.audit import log_audit_event
from core.models import AuditLog, ImportLog, Project
from users.permissions import (
    authenticated_required,
    can_edit_project_data,
    can_import,
    ensure_can_edit_project_data,
    role_required,
)


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
        import_type=ImportLog.ImportType.GENOTYPE,
        filename=filename[:255],
        success=success,
        created_count=created_count,
        error_summary=summary,
    )


@role_required(can_import)
def genotype_import(request: HttpRequest) -> HttpResponse:
    row_errors: list[str] = []
    if request.method == "POST":
        form = GenotypeImportForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded_file = form.cleaned_data["data_file"]
            upload_name = uploaded_file.name or ""
            result = parse_genotype_import(uploaded_file)
            if result.errors:
                row_errors = result.errors
                record_import_log(
                    user=request.user,
                    filename=upload_name,
                    success=False,
                    created_count=0,
                    errors=result.errors,
                )
            else:
                permission_errors: list[str] = []
                for row in result.rows:
                    mouse_obj = row["mouse"]
                    if not can_edit_project_data(request.user, mouse_obj.project):
                        permission_errors.append(
                            f"No permission to edit genotypes for mouse {mouse_obj.mouse_uid} "
                            f"(project {mouse_obj.project.name})."
                        )
                if permission_errors:
                    row_errors = permission_errors
                    record_import_log(
                        user=request.user,
                        filename=upload_name,
                        success=False,
                        created_count=0,
                        errors=row_errors,
                    )
                else:
                    with transaction.atomic():
                        MouseGenotype.objects.bulk_create([MouseGenotype(**row) for row in result.rows])
                    log_audit_event(
                        user=request.user,
                        action=AuditLog.Action.IMPORT,
                        message=f"Imported {len(result.rows)} genotype records via file upload.",
                        object_type="MouseGenotype",
                        object_id=str(len(result.rows)),
                        object_repr="Bulk Genotype Import",
                    )
                    record_import_log(
                        user=request.user,
                        filename=upload_name,
                        success=True,
                        created_count=len(result.rows),
                        errors=[],
                    )
                    messages.success(request, f"Successfully imported {len(result.rows)} genotype records.")
                    return redirect("mice:mouse_list")
    else:
        form = GenotypeImportForm()

    context = {
        "form": form,
        "row_errors": row_errors,
        "expected_columns": GENOTYPE_EXPECTED_COLUMNS,
    }
    return render(request, "genotypes/genotype_import.html", context)


@authenticated_required
def gene_list(request: HttpRequest) -> HttpResponse:
    q = (request.GET.get("q") or "").strip()
    active = (request.GET.get("active") or "yes").strip()
    genes = Gene.objects.all()
    if q:
        genes = genes.filter(
            Q(symbol__icontains=q)
            | Q(display_name__icontains=q)
            | Q(key_name__icontains=q)
            | Q(full_name__icontains=q)
        )
    if active == "yes":
        genes = genes.filter(is_active=True)
    elif active == "no":
        genes = genes.filter(is_active=False)
    return render(
        request,
        "genotypes/gene_list.html",
        {
            "genes": genes.order_by("symbol"),
            "q": q,
            "active": active,
        },
    )


@authenticated_required
def genotype_record_list(request: HttpRequest) -> HttpResponse:
    mouse_uid = (request.GET.get("mouse_uid") or "").strip()
    locus_name = (request.GET.get("locus_name") or "").strip()
    project = (request.GET.get("project") or "").strip()
    is_confirmed = (request.GET.get("is_confirmed") or "").strip()
    assay_date = (request.GET.get("assay_date") or "").strip()
    strain_line = (request.GET.get("strain_line") or "").strip()

    records = MouseGenotype.objects.select_related(
        "mouse",
        "mouse__project",
        "mouse__current_cage",
        "mouse__strain_line",
    )
    if mouse_uid:
        records = records.filter(mouse__mouse_uid__icontains=mouse_uid)
    if locus_name:
        records = records.filter(locus_name__icontains=locus_name)
    if project:
        records = records.filter(mouse__project_id=project)
    if is_confirmed == "yes":
        records = records.filter(is_confirmed=True)
    elif is_confirmed == "no":
        records = records.filter(is_confirmed=False)
    if assay_date:
        records = records.filter(assay_date=assay_date)
    if strain_line:
        records = records.filter(mouse__strain_line_id=strain_line)

    context = {
        "records": records.order_by("-assay_date", "mouse__mouse_uid", "locus_name"),
        "mouse_uid": mouse_uid,
        "locus_name": locus_name,
        "project": project,
        "is_confirmed": is_confirmed,
        "assay_date": assay_date,
        "strain_line": strain_line,
        "project_options": Project.objects.order_by("name"),
        "strain_line_options": StrainLine.objects.order_by("line_name"),
    }
    return render(request, "genotypes/genotype_record_list.html", context)


@authenticated_required
def gene_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = GeneForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Genotype definition created.")
            return redirect("genotypes:gene_list")
    else:
        form = GeneForm()
    return render(
        request,
        "genotypes/gene_form.html",
        {"form": form, "page_title": "Create Genotype Definition", "submit_label": "Save"},
    )


@authenticated_required
def gene_edit(request: HttpRequest, pk: int) -> HttpResponse:
    gene = get_object_or_404(Gene, pk=pk)
    previous_active = gene.is_active
    if request.method == "POST":
        form = GeneForm(request.POST, instance=gene)
        if form.is_valid():
            if form.cleaned_data.get("is_active") != previous_active and not can_import(request.user):
                raise PermissionDenied("Only managers or admins can archive/deactivate genotype definitions.")
            form.save()
            messages.success(request, "Genotype definition updated.")
            return redirect("genotypes:gene_list")
    else:
        form = GeneForm(instance=gene)
    return render(
        request,
        "genotypes/gene_form.html",
        {"form": form, "page_title": f"Edit Genotype {gene.symbol}", "submit_label": "Save Changes"},
    )


@authenticated_required
def mouse_genotype_create(request: HttpRequest) -> HttpResponse:
    mouse_fixed: Mouse | None = None
    if request.method == "GET" and request.GET.get("mouse"):
        mouse_fixed = get_object_or_404(Mouse.objects.select_related("project"), pk=request.GET["mouse"])
        ensure_can_edit_project_data(request.user, mouse_fixed.project)
    elif request.method == "POST" and request.POST.get("mouse"):
        mouse_fixed = get_object_or_404(Mouse.objects.select_related("project"), pk=request.POST["mouse"])

    if request.method == "POST":
        form = MouseGenotypeForm(request.POST, user=request.user)
        if mouse_fixed:
            form.fields["mouse"].widget = forms.HiddenInput()
        if form.is_valid():
            obj = form.save(commit=False)
            try:
                ensure_can_edit_project_data(request.user, obj.mouse.project)
            except PermissionDenied:
                raise
            try:
                obj.save()
            except IntegrityError:
                form.add_error(
                    None,
                    "A genotype record already exists for this combination of mouse, gene, and locus.",
                )
            else:
                log_audit_event(
                    user=request.user,
                    action=AuditLog.Action.CREATE,
                    obj=obj,
                    message=f"Added genotype record for {obj.mouse.mouse_uid}.",
                )
                messages.success(request, "Genotype record saved.")
                return redirect("mice:mouse_detail", pk=obj.mouse_id)
    else:
        initial = {}
        if mouse_fixed:
            initial["mouse"] = mouse_fixed.pk
        form = MouseGenotypeForm(initial=initial, user=request.user)
        if mouse_fixed:
            form.fields["mouse"].widget = forms.HiddenInput()

    return render(
        request,
        "genotypes/mouse_genotype_form.html",
        {
            "form": form,
            "mouse_fixed": mouse_fixed,
            "page_title": "Add Genotype Record",
            "submit_label": "Save Genotype Record",
        },
    )


@authenticated_required
def mouse_genotype_edit(request: HttpRequest, pk: int) -> HttpResponse:
    record = get_object_or_404(
        MouseGenotype.objects.select_related("mouse", "mouse__project", "gene"),
        pk=pk,
    )
    ensure_can_edit_project_data(request.user, record.mouse.project)
    if request.method == "POST":
        form = MouseGenotypeForm(request.POST, instance=record, user=request.user)
        if form.is_valid():
            obj = form.save(commit=False)
            ensure_can_edit_project_data(request.user, obj.mouse.project)
            try:
                obj.save()
            except IntegrityError:
                form.add_error(
                    None,
                    "A genotype record already exists for this combination of mouse, gene, and locus.",
                )
            else:
                log_audit_event(
                    user=request.user,
                    action=AuditLog.Action.UPDATE,
                    obj=obj,
                    message=f"Updated genotype record for {obj.mouse.mouse_uid}.",
                )
                messages.success(request, "Genotype record updated.")
                return redirect("mice:mouse_detail", pk=obj.mouse_id)
    else:
        form = MouseGenotypeForm(instance=record, user=request.user)

    return render(
        request,
        "genotypes/mouse_genotype_form.html",
        {
            "form": form,
            "mouse_fixed": None,
            "page_title": f"Edit Genotype Record — {record.mouse.mouse_uid}",
            "submit_label": "Save Changes",
        },
    )


@role_required(can_import)
def genotype_import_template(request: HttpRequest) -> HttpResponse:
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="genotype_import_template.csv"'
    writer = csv.writer(response)
    writer.writerow(GENOTYPE_EXPECTED_COLUMNS)
    writer.writerow(
        [
            "M001",
            "Trp53",
            "wt",
            "ko",
            "het",
            "yes",
            "2026-04-14",
            "Example genotype record",
        ]
    )
    return response


@role_required(can_import)
def genotype_import_template_xlsx(request: HttpRequest) -> HttpResponse:
    rows = [
        [
            "M001",
            "Trp53",
            "wt",
            "ko",
            "het",
            "yes",
            "2026-04-14",
            "Example genotype record",
        ]
    ]
    return build_xlsx_response("genotype_import_template.xlsx", "GenotypeTemplate", GENOTYPE_EXPECTED_COLUMNS, rows)
