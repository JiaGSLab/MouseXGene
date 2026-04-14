from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from colony.models import Cage, Mouse


def home(request: HttpRequest) -> HttpResponse:
    context = {
        "cage_count": Cage.objects.count(),
        "mouse_count": Mouse.objects.count(),
        "active_cage_count": Cage.objects.filter(status=Cage.Status.ACTIVE).count(),
    }
    return render(request, "core/home.html", context)
