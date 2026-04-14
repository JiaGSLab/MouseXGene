from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.db.models import Q

from .models import Cage, Mouse
from genotypes.models import MouseGenotype


def cage_list(request: HttpRequest) -> HttpResponse:
    cages = Cage.objects.all().order_by("cage_id")
    return render(request, "colony/cage_list.html", {"cages": cages})


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
    context = {
        "mouse": mouse,
        "genotype_records": genotype_records,
    }
    return render(request, "colony/mouse_detail.html", context)
