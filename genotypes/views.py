import csv

from django.contrib import messages
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from .forms import GenotypeImportForm
from .importers import GENOTYPE_EXPECTED_COLUMNS, parse_genotype_import
from .models import MouseGenotype


def genotype_import(request: HttpRequest) -> HttpResponse:
    row_errors: list[str] = []
    if request.method == "POST":
        form = GenotypeImportForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded_file = form.cleaned_data["data_file"]
            result = parse_genotype_import(uploaded_file)
            if result.errors:
                row_errors = result.errors
            else:
                with transaction.atomic():
                    MouseGenotype.objects.bulk_create([MouseGenotype(**row) for row in result.rows])
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
