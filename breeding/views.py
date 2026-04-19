from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Max, Q
from django.utils import timezone

from colony.models import CageMembership, Mouse
from colony.mouse_age import TIER_HINT, tier_map_for_breeding_select_mice

from .forms import BreedingForm, LitterForm, LitterPupFormSet, WeanLitterForm, get_pup_formset
from .models import Breeding, Litter, LitterPup
from core.audit import log_audit_event
from core.models import AuditLog
from users.permissions import (
    authenticated_required,
    ensure_can_edit_mice_projects,
    ensure_can_edit_project_data,
)


def _scoped_breedings(user):
    return Breeding.objects.select_related(
        "cage",
        "male",
        "male__project",
        "female_1",
        "female_1__project",
        "female_2",
        "female_2__project",
    )


def user_can_edit_litter(user, litter: Litter) -> bool:
    try:
        ensure_can_edit_mice_projects(
            user,
            [litter.breeding.male, litter.breeding.female_1, litter.breeding.female_2],
        )
        return True
    except PermissionDenied:
        return False


def _mouse_genotype_summary(mouse: Mouse | None) -> str:
    if mouse is None:
        return "-"
    if mouse.genotype_summary:
        return mouse.genotype_summary
    # Fallback to legacy assay-style records when summary has not been prebuilt.
    records = list(mouse.genotypes.select_related("gene").all())
    parts: list[str] = []
    for gt in records[:3]:
        locus = gt.gene.symbol if gt.gene else (gt.locus_name or "locus")
        genotype_part = gt.zygosity_display or "/".join([p for p in [gt.allele_1, gt.allele_2] if p])
        parts.append(f"{locus}:{genotype_part}" if genotype_part else locus)
    if not parts:
        return "-"
    summary = ", ".join(parts)
    return f"{summary}..." if len(records) > 3 else summary


@authenticated_required
def breeding_list(request: HttpRequest) -> HttpResponse:
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()
    breeding_type = (request.GET.get("breeding_type") or "").strip()
    cage = (request.GET.get("cage") or "").strip()
    include_inactive = (request.GET.get("include_inactive") or "").strip()

    breedings = _scoped_breedings(request.user)
    if include_inactive != "yes":
        breedings = breedings.filter(active=True)
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
        "include_inactive": include_inactive,
        "status_options": Breeding.Status.choices,
        "breeding_type_options": Breeding.BreedingType.choices,
        "cage_options": Breeding._meta.get_field("cage").related_model.objects.order_by("cage_id"),
    }
    return render(request, "breeding/breeding_list.html", context)


@authenticated_required
def breeding_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = BreedingForm(request.POST)
        if form.is_valid():
            ensure_can_edit_mice_projects(
                request.user,
                [form.cleaned_data["male"], form.cleaned_data["female_1"], form.cleaned_data.get("female_2")],
            )
            breeding = form.save()
            log_audit_event(
                user=request.user,
                action=AuditLog.Action.CREATE,
                obj=breeding,
                message=f"Created breeding {breeding.breeding_code}.",
            )
            return redirect("breeding:breeding_detail", pk=breeding.pk)
    else:
        form = BreedingForm()

    context = {
        "form": form,
        "page_title": "Create Breeding",
        "submit_label": "Save Breeding",
        "cancel_url": "breeding:breeding_list",
        "breeding_age_tier_map": tier_map_for_breeding_select_mice(),
        "breeding_age_hints": TIER_HINT,
    }
    return render(request, "breeding/breeding_form.html", context)


@authenticated_required
def breeding_detail(request: HttpRequest, pk: int) -> HttpResponse:
    breeding = get_object_or_404(
        _scoped_breedings(request.user),
        pk=pk,
    )
    litters = breeding.litters.all()
    return render(request, "breeding/breeding_detail.html", {"breeding": breeding, "litters": litters})


@authenticated_required
def breeding_end(request: HttpRequest, pk: int) -> HttpResponse:
    breeding = get_object_or_404(_scoped_breedings(request.user), pk=pk)
    ensure_can_edit_mice_projects(request.user, [breeding.male, breeding.female_1, breeding.female_2])
    if request.method != "POST":
        return redirect("breeding:breeding_detail", pk=breeding.pk)
    if breeding.status == Breeding.Status.CLOSED and not breeding.active:
        messages.info(request, f"Breeding {breeding.breeding_code} is already closed.")
        return redirect("breeding:breeding_detail", pk=breeding.pk)

    breeding.status = Breeding.Status.CLOSED
    breeding.active = False
    if not breeding.archived_at:
        breeding.archived_at = timezone.now()
    breeding.save(update_fields=["status", "active", "archived_at"])
    log_audit_event(
        user=request.user,
        action=AuditLog.Action.UPDATE,
        obj=breeding,
        message=f"Ended breeding {breeding.breeding_code}.",
    )
    messages.success(request, f"Breeding {breeding.breeding_code} ended.")
    return redirect("breeding:breeding_detail", pk=breeding.pk)


@authenticated_required
def litter_list(request: HttpRequest) -> HttpResponse:
    q = (request.GET.get("q") or "").strip()
    weaned = (request.GET.get("weaned") or "").strip()
    breeding = (request.GET.get("breeding") or "").strip()
    birth_date_from = (request.GET.get("birth_date_from") or "").strip()
    birth_date_to = (request.GET.get("birth_date_to") or "").strip()
    include_inactive = (request.GET.get("include_inactive") or "").strip()
    litter_status = (request.GET.get("litter_status") or "").strip()

    litters = (
        Litter.objects.filter(breeding__in=_scoped_breedings(request.user))
        .select_related(
            "breeding",
            "breeding__male",
            "breeding__male__strain_line",
            "breeding__female_1",
            "breeding__female_1__strain_line",
            "breeding__female_1__current_cage",
            "breeding__cage",
        )
        .annotate(
            _pup_total=Count("pups"),
            _pup_m=Count("pups", filter=Q(pups__sex=Mouse.Sex.MALE)),
            _pup_f=Count("pups", filter=Q(pups__sex=Mouse.Sex.FEMALE)),
            _max_pup_tail=Max("pups__tail_tag_date"),
        )
    )
    if include_inactive != "yes":
        litters = litters.exclude(
            litter_status__in=[Litter.LitterStatus.ENDED, Litter.LitterStatus.ARCHIVED],
        )
    if q:
        litters = litters.filter(
            Q(litter_code__icontains=q)
            | Q(breeding__breeding_code__icontains=q)
            | Q(breeding__male__mouse_uid__icontains=q)
            | Q(breeding__female_1__mouse_uid__icontains=q)
        )
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
    if litter_status:
        litters = litters.filter(litter_status=litter_status)

    litters = list(litters.order_by("-birth_date", "litter_code"))
    for litter in litters:
        litter.sire_genotype_summary = _mouse_genotype_summary(litter.breeding.male)
        litter.dam_genotype_summary = _mouse_genotype_summary(litter.breeding.female_1)
        litter.user_can_edit = user_can_edit_litter(request.user, litter)
        if litter.total_born is not None:
            litter.litter_size_display = litter.total_born
        elif litter.alive_count is not None:
            litter.litter_size_display = litter.alive_count
        elif litter._pup_total:
            litter.litter_size_display = litter._pup_total
        else:
            litter.litter_size_display = None
        if litter.male_count is not None:
            litter.males_display = litter.male_count
        elif litter._pup_total:
            litter.males_display = litter._pup_m
        else:
            litter.males_display = None
        if litter.female_count is not None:
            litter.females_display = litter.female_count
        elif litter._pup_total:
            litter.females_display = litter._pup_f
        else:
            litter.females_display = None
        litter.tail_tag_display = litter.tail_tag_date or litter._max_pup_tail

    context = {
        "litters": litters,
        "q": q,
        "weaned": weaned,
        "breeding": breeding,
        "birth_date_from": birth_date_from,
        "birth_date_to": birth_date_to,
        "include_inactive": include_inactive,
        "litter_status": litter_status,
        "litter_status_options": Litter.LitterStatus.choices,
        "breeding_options": _scoped_breedings(request.user).order_by("breeding_code"),
    }
    return render(request, "breeding/litter_list.html", context)


@authenticated_required
def litter_create(request: HttpRequest, breeding_pk: int) -> HttpResponse:
    breeding = get_object_or_404(_scoped_breedings(request.user), pk=breeding_pk)
    ensure_can_edit_mice_projects(request.user, [breeding.male, breeding.female_1, breeding.female_2])
    if request.method == "POST":
        form = LitterForm(request.POST)
        if form.is_valid():
            litter = form.save(commit=False)
            litter.breeding = breeding
            litter.save()
            if breeding.status != Breeding.Status.LITTERED:
                breeding.status = Breeding.Status.LITTERED
                breeding.save(update_fields=["status"])
            log_audit_event(
                user=request.user,
                action=AuditLog.Action.RECORD_LITTER,
                obj=litter,
                message=f"Recorded litter {litter.litter_code or litter.pk} for breeding {breeding.breeding_code}.",
            )
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


@authenticated_required
def litter_detail(request: HttpRequest, pk: int) -> HttpResponse:
    litter = get_object_or_404(
        Litter.objects.select_related(
            "breeding",
            "breeding__male",
            "breeding__male__strain_line",
            "breeding__male__current_cage",
            "breeding__female_1",
            "breeding__female_1__strain_line",
            "breeding__female_1__current_cage",
            "breeding__cage",
        )
        .prefetch_related("pups__mouse")
        .filter(breeding__in=_scoped_breedings(request.user)),
        pk=pk,
    )
    pups = list(litter.pups.all().order_by("sort_order", "id"))
    registered_offspring = list(
        Mouse.objects.filter(
            dam_id=litter.breeding.female_1_id,
            sire_id=litter.breeding.male_id,
            birth_date=litter.birth_date,
        )
        .select_related("strain_line", "current_cage", "project")
        .order_by("mouse_uid")
    )
    context = {
        "litter": litter,
        "pups": pups,
        "registered_offspring": registered_offspring,
        "sire_genotype_summary": _mouse_genotype_summary(litter.breeding.male),
        "dam_genotype_summary": _mouse_genotype_summary(litter.breeding.female_1),
        "can_edit_litter": user_can_edit_litter(request.user, litter),
    }
    return render(request, "breeding/litter_detail.html", context)


@authenticated_required
def litter_edit(request: HttpRequest, pk: int) -> HttpResponse:
    litter = get_object_or_404(
        Litter.objects.select_related(
            "breeding",
            "breeding__male",
            "breeding__female_1",
            "breeding__female_2",
        ).filter(breeding__in=_scoped_breedings(request.user)),
        pk=pk,
    )
    if not user_can_edit_litter(request.user, litter):
        raise PermissionDenied("You do not have permission to edit this litter.")
    if request.method == "POST":
        form = LitterForm(request.POST, instance=litter)
        formset = LitterPupFormSet(request.POST, instance=litter)
        if form.is_valid() and formset.is_valid():
            litter = form.save(commit=False)
            if litter.litter_status in (Litter.LitterStatus.ARCHIVED, Litter.LitterStatus.ENDED):
                litter.is_archived = True
                if not litter.archived_at:
                    litter.archived_at = timezone.now()
            else:
                litter.is_archived = False
                litter.archived_at = None
            litter.save()
            formset.save()
            messages.success(request, "Litter and pup rows saved.")
            return redirect("litters:litter_detail", pk=litter.pk)
    else:
        form = LitterForm(instance=litter)
        formset = LitterPupFormSet(instance=litter)

    context = {
        "litter": litter,
        "form": form,
        "formset": formset,
        "page_title": f"Manage litter {litter.litter_id_display}",
    }
    return render(request, "breeding/litter_edit.html", context)


@authenticated_required
def litter_end(request: HttpRequest, pk: int) -> HttpResponse:
    litter = get_object_or_404(
        Litter.objects.select_related("breeding", "breeding__male", "breeding__female_1", "breeding__female_2").filter(
            breeding__in=_scoped_breedings(request.user)
        ),
        pk=pk,
    )
    if not user_can_edit_litter(request.user, litter):
        raise PermissionDenied("You do not have permission to end this litter.")
    if request.method != "POST":
        return redirect("litters:litter_detail", pk=litter.pk)
    if litter.litter_status in (Litter.LitterStatus.ENDED, Litter.LitterStatus.ARCHIVED):
        messages.info(request, "This litter is already closed.")
        return redirect("litters:litter_detail", pk=litter.pk)

    litter.litter_status = Litter.LitterStatus.ENDED
    litter.is_archived = True
    if not litter.archived_at:
        litter.archived_at = timezone.now()
    litter.save(update_fields=["litter_status", "is_archived", "archived_at"])
    log_audit_event(
        user=request.user,
        action=AuditLog.Action.UPDATE,
        obj=litter,
        message=f"Ended litter workflow for {litter.litter_id_display}.",
    )
    messages.success(request, f"Litter {litter.litter_id_display} marked as ended.")
    return redirect("litters:litter_detail", pk=litter.pk)


@authenticated_required
def litter_wean(request: HttpRequest, pk: int) -> HttpResponse:
    litter = get_object_or_404(
        Litter.objects.select_related("breeding", "breeding__male", "breeding__female_1").filter(
            breeding__in=_scoped_breedings(request.user)
        ),
        pk=pk,
    )
    breeding = litter.breeding
    ensure_can_edit_mice_projects(request.user, [breeding.male, breeding.female_1, breeding.female_2])
    if litter.litter_status in (Litter.LitterStatus.ENDED, Litter.LitterStatus.ARCHIVED):
        messages.error(request, "This litter is closed; you cannot wean additional pups.")
        return redirect("litters:litter_detail", pk=litter.pk)
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
                    if breeding.male.project_id != breeding.female_1.project_id:
                        wean_form.add_error(
                            None,
                            "Sire and dam must belong to the same project before weaning pups into that project.",
                        )
                if not wean_form.errors:
                    inherited_project = breeding.male.project
                    ensure_can_edit_project_data(request.user, inherited_project)
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

                        orphan_pups = list(
                            LitterPup.objects.filter(litter=litter, mouse_id__isnull=True).order_by(
                                "sort_order", "id"
                            )
                        )
                        for i, mouse in enumerate(new_mice):
                            if i < len(orphan_pups):
                                pup = orphan_pups[i]
                                pup.mouse = mouse
                                pup.save(update_fields=["mouse_id", "updated_at"])

                        litter.wean_date = wean_date
                        if litter.litter_status == Litter.LitterStatus.ACTIVE:
                            litter.litter_status = Litter.LitterStatus.WEANED
                        litter.save(update_fields=["wean_date", "litter_status", "updated_at"])

                    messages.success(
                        request,
                        f"Weaned {len(created_uids)} pups: {', '.join(created_uids)}.",
                    )
                    log_audit_event(
                        user=request.user,
                        action=AuditLog.Action.WEAN,
                        obj=litter,
                        message=(
                            f"Weaned {len(created_uids)} pups from litter {litter.litter_code or litter.pk} "
                            f"into cage {target_cage.cage_id}: {', '.join(created_uids)}."
                        ),
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
