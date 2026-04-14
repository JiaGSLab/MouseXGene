from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib import messages
from django.db import transaction
from django.db.models import Q

from colony.models import CageMembership, Mouse

from .forms import BreedingForm, LitterForm, WeanLitterForm, get_pup_formset
from .models import Breeding, Litter


def breeding_list(request: HttpRequest) -> HttpResponse:
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()
    breeding_type = (request.GET.get("breeding_type") or "").strip()
    cage = (request.GET.get("cage") or "").strip()

    breedings = Breeding.objects.select_related("cage", "male", "female_1", "female_2").all()
    if q:
        breedings = breedings.filter(
            Q(breeding_code__icontains=q)
            | Q(male__mouse_uid__icontains=q)
            | Q(female_1__mouse_uid__icontains=q)
            | Q(female_2__mouse_uid__icontains=q)
        )
    if status:
        breedings = breedings.filter(status=status)
    if breeding_type:
        breedings = breedings.filter(breeding_type=breeding_type)
    if cage:
        breedings = breedings.filter(cage_id=cage)

    context = {
        "breedings": breedings.order_by("-start_date", "breeding_code"),
        "q": q,
        "status": status,
        "breeding_type": breeding_type,
        "cage": cage,
        "status_options": Breeding.Status.choices,
        "breeding_type_options": Breeding.BreedingType.choices,
        "cage_options": Breeding._meta.get_field("cage").related_model.objects.order_by("cage_id"),
    }
    return render(request, "breeding/breeding_list.html", context)


def breeding_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = BreedingForm(request.POST)
        if form.is_valid():
            breeding = form.save()
            return redirect("breeding:breeding_detail", pk=breeding.pk)
    else:
        form = BreedingForm()

    context = {
        "form": form,
        "page_title": "Create Breeding",
        "submit_label": "Save Breeding",
        "cancel_url": "breeding:breeding_list",
    }
    return render(request, "breeding/breeding_form.html", context)


def breeding_detail(request: HttpRequest, pk: int) -> HttpResponse:
    breeding = get_object_or_404(
        Breeding.objects.select_related("cage", "male", "female_1", "female_2"),
        pk=pk,
    )
    litters = breeding.litters.all()
    return render(request, "breeding/breeding_detail.html", {"breeding": breeding, "litters": litters})


def litter_list(request: HttpRequest) -> HttpResponse:
    q = (request.GET.get("q") or "").strip()
    weaned = (request.GET.get("weaned") or "").strip()
    breeding = (request.GET.get("breeding") or "").strip()
    birth_date_from = (request.GET.get("birth_date_from") or "").strip()
    birth_date_to = (request.GET.get("birth_date_to") or "").strip()

    litters = Litter.objects.select_related("breeding").all()
    if q:
        litters = litters.filter(litter_code__icontains=q)
    if weaned == "yes":
        litters = litters.filter(wean_date__isnull=False)
    elif weaned == "no":
        litters = litters.filter(wean_date__isnull=True)
    if breeding:
        litters = litters.filter(breeding_id=breeding)
    if birth_date_from:
        litters = litters.filter(birth_date__gte=birth_date_from)
    if birth_date_to:
        litters = litters.filter(birth_date__lte=birth_date_to)

    context = {
        "litters": litters.order_by("-birth_date", "litter_code"),
        "q": q,
        "weaned": weaned,
        "breeding": breeding,
        "birth_date_from": birth_date_from,
        "birth_date_to": birth_date_to,
        "breeding_options": Breeding.objects.order_by("breeding_code"),
    }
    return render(request, "breeding/litter_list.html", context)


def litter_create(request: HttpRequest, breeding_pk: int) -> HttpResponse:
    breeding = get_object_or_404(Breeding, pk=breeding_pk)
    if request.method == "POST":
        form = LitterForm(request.POST)
        if form.is_valid():
            litter = form.save(commit=False)
            litter.breeding = breeding
            litter.save()
            if breeding.status != Breeding.Status.LITTERED:
                breeding.status = Breeding.Status.LITTERED
                breeding.save(update_fields=["status"])
            messages.success(request, f"Litter {litter.litter_code or litter.pk} created.")
            return redirect("litters:litter_detail", pk=litter.pk)
    else:
        form = LitterForm()

    context = {
        "form": form,
        "breeding": breeding,
        "page_title": f"Record Litter for {breeding.breeding_code}",
    }
    return render(request, "breeding/litter_form.html", context)


def litter_detail(request: HttpRequest, pk: int) -> HttpResponse:
    litter = get_object_or_404(Litter.objects.select_related("breeding"), pk=pk)
    return render(request, "breeding/litter_detail.html", {"litter": litter})


def litter_wean(request: HttpRequest, pk: int) -> HttpResponse:
    litter = get_object_or_404(
        Litter.objects.select_related("breeding", "breeding__male", "breeding__female_1"),
        pk=pk,
    )
    breeding = litter.breeding

    if request.method == "POST":
        wean_form = WeanLitterForm(request.POST)
        number_of_pups = 1
        if wean_form.is_valid():
            number_of_pups = wean_form.cleaned_data["number_of_pups"]
        else:
            try:
                number_of_pups = max(1, int(request.POST.get("number_of_pups", "1")))
            except ValueError:
                number_of_pups = 1

        PupFormSet = get_pup_formset(number_of_pups)
        pup_formset = PupFormSet(request.POST, prefix="pups")

        if "refresh_forms" in request.POST:
            return render(
                request,
                "breeding/litter_wean.html",
                {"litter": litter, "wean_form": wean_form, "pup_formset": pup_formset},
            )

        if wean_form.is_valid():
            wean_date = wean_form.cleaned_data["wean_date"]
            if litter.alive_count is not None and number_of_pups > litter.alive_count:
                wean_form.add_error("number_of_pups", "number_of_pups cannot exceed litter.alive_count.")

            if pup_formset.is_valid() and not wean_form.errors:
                uid_list = [form.cleaned_data["mouse_uid"] for form in pup_formset.forms]
                duplicate_in_form = {uid for uid in uid_list if uid_list.count(uid) > 1}
                if duplicate_in_form:
                    wean_form.add_error(
                        None,
                        f"Duplicate mouse_uid in form: {', '.join(sorted(duplicate_in_form))}.",
                    )

                existing_uids = set(Mouse.objects.filter(mouse_uid__in=uid_list).values_list("mouse_uid", flat=True))
                if existing_uids:
                    wean_form.add_error(
                        None,
                        f"mouse_uid already exists in database: {', '.join(sorted(existing_uids))}.",
                    )

                if not wean_form.errors:
                    target_cage = wean_form.cleaned_data["target_cage"]
                    inherited_project = breeding.male.project or breeding.female_1.project
                    created_uids: list[str] = []
                    with transaction.atomic():
                        new_mice: list[Mouse] = []
                        for form in pup_formset.forms:
                            mouse = Mouse.objects.create(
                                mouse_uid=form.cleaned_data["mouse_uid"],
                                sex=form.cleaned_data["sex"],
                                birth_date=litter.birth_date,
                                status=Mouse.Status.ACTIVE,
                                strain_line=breeding.female_1.strain_line,
                                current_cage=target_cage,
                                sire=breeding.male,
                                dam=breeding.female_1,
                                project=inherited_project,
                                ear_tag=form.cleaned_data["ear_tag"],
                                coat_color=form.cleaned_data["coat_color"],
                                notes=form.cleaned_data["notes"],
                            )
                            new_mice.append(mouse)
                            created_uids.append(mouse.mouse_uid)

                        CageMembership.objects.bulk_create(
                            [
                                CageMembership(
                                    mouse=mouse,
                                    cage=target_cage,
                                    start_date=wean_date,
                                    end_date=None,
                                    is_current=True,
                                    reason="Weaned from litter",
                                    notes="",
                                )
                                for mouse in new_mice
                            ]
                        )

                        litter.wean_date = wean_date
                        litter.save(update_fields=["wean_date"])

                    messages.success(
                        request,
                        f"Weaned {len(created_uids)} pups: {', '.join(created_uids)}.",
                    )
                    return redirect("litters:litter_detail", pk=litter.pk)
    else:
        initial_pups = 1
        wean_form = WeanLitterForm(initial={"wean_date": litter.wean_date, "number_of_pups": initial_pups})
        PupFormSet = get_pup_formset(initial_pups)
        pup_formset = PupFormSet(prefix="pups")

    context = {
        "litter": litter,
        "wean_form": wean_form,
        "pup_formset": pup_formset,
    }
    return render(request, "breeding/litter_wean.html", context)
