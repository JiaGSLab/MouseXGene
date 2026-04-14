import csv
from io import BytesIO

from django.contrib import messages
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from openpyxl import Workbook

from .forms import GenotypeImportForm
from .importers import GENOTYPE_EXPECTED_COLUMNS, parse_genotype_import
from .models import MouseGenotype
from core.audit import log_audit_event
from core.models import AuditLog, ImportLog
from users.permissions import role_required, can_import


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
