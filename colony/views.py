import csv

from django.http import HttpRequest, HttpResponse
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .forms import CageForm, CageImportForm, MouseForm, MouseImportForm, MoveCageForm
from .importers import EXPECTED_COLUMNS, MOUSE_EXPECTED_COLUMNS, parse_cage_import, parse_mouse_import
from .models import Cage, CageMembership, Mouse
from genotypes.models import MouseGenotype


def build_short_genotype_summary(mouse: Mouse) -> str:
    genotype_records = list(mouse.genotypes.all())
    parts = []
    for gt in genotype_records[:3]:
        locus = gt.gene.symbol if gt.gene else (gt.locus_name or "locus")
        genotype_part = gt.zygosity_display or "/".join(
            [p for p in [gt.allele_1, gt.allele_2] if p]
        )
        if genotype_part:
            parts.append(f"{locus}:{genotype_part}")
        else:
            parts.append(locus)
    if not parts:
        return "-"
    summary = ", ".join(parts)
    return f"{summary}..." if len(genotype_records) > 3 else summary


def cage_list(request: HttpRequest) -> HttpResponse:
    cages = Cage.objects.all().order_by("cage_id")
    return render(request, "colony/cage_list.html", {"cages": cages})


def cage_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = CageForm(request.POST)
        if form.is_valid():
            cage = form.save()
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


def cage_import(request: HttpRequest) -> HttpResponse:
    row_errors: list[str] = []
    if request.method == "POST":
        form = CageImportForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded_file = form.cleaned_data["data_file"]
            result = parse_cage_import(uploaded_file)
            if result.errors:
                row_errors = result.errors
            else:
                with transaction.atomic():
                    Cage.objects.bulk_create([Cage(**row) for row in result.rows])
                messages.success(request, f"Successfully imported {len(result.rows)} cages.")
                return redirect("colony:cage_list")
    else:
        form = CageImportForm()

    context = {
        "form": form,
        "row_errors": row_errors,
        "expected_columns": EXPECTED_COLUMNS,
    }
    return render(request, "colony/cage_import.html", context)


def cage_import_template(request: HttpRequest) -> HttpResponse:
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="cage_import_template.csv"'
    writer = csv.writer(response)
    writer.writerow(EXPECTED_COLUMNS)
    writer.writerow(
        [
            "C001",
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


def cage_detail(request: HttpRequest, pk: int) -> HttpResponse:
    cage = get_object_or_404(Cage, pk=pk)
    current_mice = Mouse.objects.filter(current_cage=cage).order_by("mouse_uid")
    context = {
        "cage": cage,
        "current_mice": current_mice,
    }
    return render(request, "colony/cage_detail.html", context)


def cage_print(request: HttpRequest, pk: int) -> HttpResponse:
    cage = get_object_or_404(Cage, pk=pk)
    current_mice = (
        Mouse.objects.filter(current_cage=cage)
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


def cages_export(request: HttpRequest) -> HttpResponse:
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="cages_export.csv"'
    writer = csv.writer(response)
    writer.writerow(
        [
            "cage_id",
            "room",
            "rack",
            "position",
            "cage_type",
            "purpose",
            "status",
            "notes",
        ]
    )
    for cage in Cage.objects.all().order_by("cage_id"):
        writer.writerow(
            [
                cage.cage_id,
                cage.room,
                cage.rack,
                cage.position,
                cage.cage_type,
                cage.purpose,
                cage.status,
                cage.notes,
            ]
        )
    return response


def cage_inventory_export(request: HttpRequest, pk: int) -> HttpResponse:
    cage = get_object_or_404(Cage, pk=pk)
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
    mice = Mouse.objects.filter(current_cage=cage).select_related("strain_line", "project").order_by("mouse_uid")
    for mouse in mice:
        writer.writerow(
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
        )
    return response


def mouse_list(request: HttpRequest) -> HttpResponse:
    query = (request.GET.get("q") or "").strip()
    mice = Mouse.objects.select_related("strain_line", "current_cage", "project").all()

    if query:
        mice = mice.filter(
            Q(mouse_uid__icontains=query)
            | Q(ear_tag__icontains=query)
            | Q(strain_line__line_name__icontains=query)
            | Q(project__name__icontains=query)
        )

    mice = mice.order_by("-birth_date", "mouse_uid")
    context = {
        "mice": mice,
        "query": query,
    }
    return render(request, "colony/mouse_list.html", context)


def mouse_import(request: HttpRequest) -> HttpResponse:
    row_errors: list[str] = []
    if request.method == "POST":
        form = MouseImportForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded_file = form.cleaned_data["data_file"]
            result = parse_mouse_import(uploaded_file)
            if result.errors:
                row_errors = result.errors
            else:
                with transaction.atomic():
                    created_mice = []
                    for row in result.rows:
                        mouse = Mouse.objects.create(**row)
                        created_mice.append(mouse)
                        if mouse.current_cage:
                            CageMembership.objects.create(
                                mouse=mouse,
                                cage=mouse.current_cage,
                                start_date=mouse.birth_date or timezone.localdate(),
                                end_date=None,
                                is_current=True,
                                reason="Imported with initial cage assignment",
                                notes="",
                            )

                messages.success(request, f"Successfully imported {len(created_mice)} mice.")
                return redirect("mice:mouse_list")
    else:
        form = MouseImportForm()

    context = {
        "form": form,
        "row_errors": row_errors,
        "expected_columns": MOUSE_EXPECTED_COLUMNS,
    }
    return render(request, "colony/mouse_import.html", context)


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
            "",
            "",
            "",
            "ET-001",
            "black",
            "Example imported mouse",
            "",
            "",
        ]
    )
    return response


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
            "coat_color",
            "notes",
        ]
    )
    mice = Mouse.objects.select_related("strain_line", "current_cage", "project").all().order_by("mouse_uid")
    for mouse in mice:
        writer.writerow(
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
                mouse.coat_color,
                mouse.notes,
            ]
        )
    return response


def mouse_detail(request: HttpRequest, pk: int) -> HttpResponse:
    mouse = get_object_or_404(
        Mouse.objects.select_related("strain_line", "current_cage", "project", "sire", "dam"),
        pk=pk,
    )
    genotype_records = MouseGenotype.objects.select_related("gene").filter(mouse=mouse)
    cage_history = mouse.cage_memberships.select_related("cage").all()
    context = {
        "mouse": mouse,
        "genotype_records": genotype_records,
        "cage_history": cage_history,
    }
    return render(request, "colony/mouse_detail.html", context)


def mouse_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = MouseForm(request.POST)
        if form.is_valid():
            mouse = form.save()
            return redirect("mice:mouse_detail", pk=mouse.pk)
    else:
        form = MouseForm()

    context = {
        "form": form,
        "page_title": "Create Mouse",
        "submit_label": "Save Mouse",
        "cancel_url": "mice:mouse_list",
    }
    return render(request, "colony/mouse_form.html", context)


def mouse_edit(request: HttpRequest, pk: int) -> HttpResponse:
    mouse = get_object_or_404(Mouse, pk=pk)
    if request.method == "POST":
        form = MouseForm(request.POST, instance=mouse)
        if form.is_valid():
            mouse = form.save()
            return redirect("mice:mouse_detail", pk=mouse.pk)
    else:
        form = MouseForm(instance=mouse)

    context = {
        "form": form,
        "page_title": f"Edit Mouse {mouse.mouse_uid}",
        "submit_label": "Save Changes",
        "cancel_url": "mice:mouse_detail",
        "cancel_kwargs": {"pk": mouse.pk},
    }
    return render(request, "colony/mouse_form.html", context)


def mouse_move(request: HttpRequest, pk: int) -> HttpResponse:
    mouse = get_object_or_404(
        Mouse.objects.select_related("strain_line", "current_cage"),
        pk=pk,
    )

    if request.method == "POST":
        form = MoveCageForm(request.POST, mouse=mouse)
        if form.is_valid():
            destination_cage = form.cleaned_data["destination_cage"]
            move_date = form.cleaned_data["move_date"]
            reason = form.cleaned_data["reason"]
            notes = form.cleaned_data["notes"]

            with transaction.atomic():
                mouse_locked = Mouse.objects.select_for_update().get(pk=mouse.pk)

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

            return redirect("mice:mouse_detail", pk=mouse.pk)
    else:
        form = MoveCageForm(mouse=mouse)

    context = {
        "mouse": mouse,
        "form": form,
    }
    return render(request, "colony/mouse_move.html", context)


def mouse_genotypes_export(request: HttpRequest, pk: int) -> HttpResponse:
    mouse = get_object_or_404(Mouse, pk=pk)
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
    genotype_records = MouseGenotype.objects.filter(mouse=mouse).order_by("-assay_date", "-created_at")
    for gt in genotype_records:
        writer.writerow(
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
        )
    return response
