from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.db import transaction
from django.db.models import Q

from .forms import CageForm, MouseForm, MoveCageForm
from .models import Cage, CageMembership, Mouse
from genotypes.models import MouseGenotype


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


def cage_detail(request: HttpRequest, pk: int) -> HttpResponse:
    cage = get_object_or_404(Cage, pk=pk)
    current_mice = Mouse.objects.filter(current_cage=cage).order_by("mouse_uid")
    context = {
        "cage": cage,
        "current_mice": current_mice,
    }
    return render(request, "colony/cage_detail.html", context)


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
