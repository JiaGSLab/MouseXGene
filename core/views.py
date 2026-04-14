from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from colony.models import Cage, Mouse


def home(request: HttpRequest) -> HttpResponse:
    mice_without_cage_qs = Mouse.objects.filter(current_cage__isnull=True).select_related(
        "strain_line", "project"
    )
    mice_without_genotype_qs = (
        Mouse.objects.filter(genotypes__isnull=True)
        .select_related("strain_line", "project")
        .distinct()
    )
    cages_without_mice_qs = (
        Cage.objects.filter(current_mice__isnull=True)
        .order_by("cage_id")
        .distinct()
    )

    context = {
        "total_cages": Cage.objects.count(),
        "active_cages": Cage.objects.filter(status=Cage.Status.ACTIVE).count(),
        "total_mice": Mouse.objects.count(),
        "active_mice": Mouse.objects.filter(status=Mouse.Status.ACTIVE).count(),
        "mice_without_cage_count": mice_without_cage_qs.count(),
        "mice_without_genotype_count": mice_without_genotype_qs.count(),
        "cages_without_mice_count": cages_without_mice_qs.count(),
        "mice_without_cage": mice_without_cage_qs.order_by("mouse_uid")[:8],
        "mice_without_genotype": mice_without_genotype_qs.order_by("mouse_uid")[:8],
        "cages_without_mice": cages_without_mice_qs[:8],
        "recent_mice": Mouse.objects.select_related("strain_line", "current_cage").order_by("-created_at")[:8],
        "recent_cages": Cage.objects.order_by("-created_at")[:8],
    }
    return render(request, "core/home.html", context)
