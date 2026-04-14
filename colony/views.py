from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from .models import Cage, Mouse


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
