from datetime import timedelta

from django import forms
from django.forms import formset_factory, inlineformset_factory
from django.db import ProgrammingError, OperationalError
from django.db.models import Q
from django.utils import timezone

from colony.models import Cage, Mouse
from .models import Breeding, BreedingExtraFemale, Litter, LitterPup


class BreedingForm(forms.ModelForm):
    male = forms.ModelChoiceField(queryset=Mouse.objects.none(), required=False)
    female_1 = forms.ModelChoiceField(queryset=Mouse.objects.none(), required=False)
    female_2 = forms.ModelChoiceField(queryset=Mouse.objects.none(), required=False)
    sire = forms.ModelChoiceField(queryset=Mouse.objects.none(), required=True, label="Sire (male)")
    dams = forms.ModelMultipleChoiceField(
        queryset=Mouse.objects.none(),
        required=True,
        label="Dams (female)",
        help_text="Select 1-3 female breeders.",
        widget=forms.SelectMultiple(attrs={"size": 6}),
    )
    extra_females = forms.ModelMultipleChoiceField(
        queryset=Mouse.objects.none(),
        required=False,
        label="Extra Females (internal)",
        help_text="Internal compatibility field.",
        widget=forms.MultipleHiddenInput(),
    )

    class Meta:
        model = Breeding
        fields = [
            "breeding_code",
            "cage",
            "breeding_type",
            "male",
            "female_1",
            "female_2",
            "start_date",
            "plug_date",
            "expected_birth_date",
            "status",
            "notes",
            "active",
        ]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "plug_date": forms.DateInput(attrs={"type": "date"}),
            "expected_birth_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["breeding_code"].help_text = "Example: BR-2026-001 or Lyz2xTet2-01."
        self.fields["breeding_code"].required = False
        self.fields["breeding_code"].widget.attrs.update(
            {"placeholder": "Optional; auto-generated if left blank."}
        )
        self.fields["cage"].queryset = self.fields["cage"].queryset.order_by("cage_id")
        male_qs = Mouse.objects.filter(sex=Mouse.Sex.MALE).order_by("mouse_uid")
        self.fields["male"].queryset = male_qs
        self.fields["male"].required = False
        female_qs = Mouse.objects.filter(sex=Mouse.Sex.FEMALE).order_by("mouse_uid")
        self.fields["female_1"].queryset = female_qs
        self.fields["female_1"].required = False
        self.fields["female_2"].queryset = female_qs
        self.fields["sire"].queryset = male_qs
        self.fields["dams"].queryset = female_qs
        self.fields["extra_females"].queryset = female_qs
        self.warning_messages: list[str] = []
        self.member_rows: list[dict] = []
        if self.instance.pk:
            extra_ids = list(self.instance.extra_female_links.select_related("mouse").values_list("mouse_id", flat=True))
            dams_initial = [x for x in [self.instance.female_1_id, self.instance.female_2_id, *extra_ids] if x]
            self.fields["sire"].initial = self.instance.male_id
            self.fields["dams"].initial = dams_initial

    def _generate_breeding_code(self) -> str:
        prefix = timezone.localdate().strftime("BR-%Y%m%d")
        n = 1
        while True:
            candidate = f"{prefix}-{n:03d}"
            if not Breeding.objects.filter(breeding_code=candidate).exists():
                return candidate
            n += 1

    def _selected_mice(self) -> list[Mouse]:
        mice: list[Mouse] = []
        male = self.cleaned_data.get("male")
        female_1 = self.cleaned_data.get("female_1")
        female_2 = self.cleaned_data.get("female_2")
        extra_females = list(self.cleaned_data.get("extra_females") or [])
        for m in [male, female_1, female_2, *extra_females]:
            if m and m not in mice:
                mice.append(m)
        return mice

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get("start_date")
        if start_date:
            # Standardized breeding estimate: expected birth = start date + 21 days.
            cleaned_data["expected_birth_date"] = start_date + timedelta(days=21)

        sire = cleaned_data.get("sire")
        dams = list(cleaned_data.get("dams") or [])
        if not sire:
            self.add_error("sire", "At least one sire is required.")
        if not dams:
            self.add_error("dams", "At least one dam is required.")
        if len(dams) > 3:
            self.add_error("dams", "Select at most 3 dams.")
        breeding_type = cleaned_data.get("breeding_type")
        if breeding_type == Breeding.BreedingType.PAIR and len(dams) != 1:
            self.add_error("dams", "Pair breeding requires exactly 1 dam.")
        if breeding_type == Breeding.BreedingType.TRIO and len(dams) != 2:
            self.add_error("dams", "Trio breeding requires exactly 2 dams.")
        if sire and sire.sex != Mouse.Sex.MALE:
            self.add_error("sire", f"{sire.mouse_uid}: sire must be male.")
        for dam in dams:
            if dam.sex != Mouse.Sex.FEMALE:
                self.add_error("dams", f"{dam.mouse_uid}: dam must be female.")

        if sire and dams:
            cleaned_data["male"] = sire
            cleaned_data["female_1"] = dams[0]
            cleaned_data["female_2"] = dams[1] if len(dams) > 1 else None
            cleaned_data["extra_females"] = dams[2:] if len(dams) > 2 else []

        if not (cleaned_data.get("breeding_code") or "").strip():
            cleaned_data["breeding_code"] = self._generate_breeding_code()

        male = cleaned_data.get("male")
        female_1 = cleaned_data.get("female_1")
        female_2 = cleaned_data.get("female_2")
        extra_value = cleaned_data.get("extra_females") or []
        extra_females = list(extra_value)
        seen: set[int] = set()
        duplicate_ids: set[int] = set()
        for m in [male, female_1, female_2, *extra_females]:
            if not m:
                continue
            if m.pk in seen:
                duplicate_ids.add(m.pk)
            seen.add(m.pk)
        if duplicate_ids:
            self.add_error(None, "The same mouse cannot be selected multiple times in one breeding setup.")

        status_warn_set = {
            Mouse.Status.DEAD,
            Mouse.Status.EUTHANIZED,
            Mouse.Status.CULLED,
            Mouse.Status.ARCHIVED,
            Mouse.Status.TRANSFERRED,
        }
        warning_messages: list[str] = []
        today = timezone.localdate()
        for mouse in self._selected_mice():
            age_days = (today - mouse.birth_date).days if mouse.birth_date else None
            if mouse.status in status_warn_set:
                warning_messages.append(
                    f"{mouse.mouse_uid}: selected mouse is {mouse.get_status_display().lower()}."
                )
            elif mouse.status != Mouse.Status.ACTIVE:
                warning_messages.append(f"{mouse.mouse_uid}: selected mouse is not active.")

            active_breeding_q = Breeding.objects.filter(active=True).filter(
                Q(male=mouse)
                | Q(female_1=mouse)
                | Q(female_2=mouse)
                | Q(extra_female_links__mouse=mouse)
            )
            if self.instance.pk:
                active_breeding_q = active_breeding_q.exclude(pk=self.instance.pk)
            active_codes = sorted(set(active_breeding_q.values_list("breeding_code", flat=True)))
            if active_codes:
                warning_messages.append(
                    f"{mouse.mouse_uid}: already in active breeding(s): {', '.join(active_codes)}."
                )
            self.member_rows.append(
                {
                    "role": "Sire" if mouse == male else "Dam",
                    "mouse_uid": mouse.mouse_uid,
                    "sex": mouse.get_sex_display(),
                    "age_days": age_days,
                    "status": mouse.get_status_display(),
                    "active_breeding_codes": active_codes,
                }
            )
        self.warning_messages = warning_messages
        return cleaned_data

    def save(self, commit=True):
        if not (self.cleaned_data.get("breeding_code") or "").strip():
            self.cleaned_data["breeding_code"] = self._generate_breeding_code()
        breeding = super().save(commit=commit)
        if not commit:
            return breeding
        selected = list(self.cleaned_data.get("extra_females") or [])
        selected_ids = {m.id for m in selected}
        BreedingExtraFemale.objects.filter(breeding=breeding).exclude(mouse_id__in=selected_ids).delete()
        existing = set(BreedingExtraFemale.objects.filter(breeding=breeding).values_list("mouse_id", flat=True))
        BreedingExtraFemale.objects.bulk_create(
            [
                BreedingExtraFemale(breeding=breeding, mouse=mouse)
                for mouse in selected
                if mouse.id not in existing
            ]
        )
        try:
            breeding.sync_members_from_legacy_fields()
        except (ProgrammingError, OperationalError):
            # Keep create/edit usable even if DB migrations are pending.
            self.warning_messages.append(
                "Breeding members table is not ready yet. Run migrations to enable member synchronization."
            )
        return breeding


class LitterForm(forms.ModelForm):
    class Meta:
        model = Litter
        fields = [
            "litter_code",
            "birth_date",
            "total_born",
            "alive_count",
            "dead_count",
            "male_count",
            "female_count",
            "wean_date",
            "tail_tag_date",
            "litter_status",
            "notes",
        ]
        widgets = {
            "birth_date": forms.DateInput(attrs={"type": "date"}),
            "wean_date": forms.DateInput(attrs={"type": "date"}),
            "tail_tag_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }


class LitterPupForm(forms.ModelForm):
    class Meta:
        model = LitterPup
        fields = ["sort_order", "sex", "ear_tag", "toe_tag", "coat_color", "tail_tag_date", "notes"]
        widgets = {
            "tail_tag_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }


LitterPupFormSet = inlineformset_factory(
    Litter,
    LitterPup,
    form=LitterPupForm,
    extra=1,
    can_delete=True,
    min_num=0,
)


class WeanLitterForm(forms.Form):
    class ProjectAssignmentMode:
        SIRE = "sire"
        DAM = "dam"
        NEW = "new"

    target_cage = forms.ModelChoiceField(
        queryset=Cage.objects.none(),
        label="Target Cage",
        required=False,
    )
    target_cage_lookup = forms.CharField(
        max_length=64,
        required=False,
        label="Or enter cage ID",
        help_text="Type a cage ID to match if it is not in the filtered list.",
    )
    wean_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), label="Wean Date")
    number_of_pups = forms.IntegerField(min_value=1, label="Number of Pups")
    project_assignment_mode = forms.ChoiceField(
        label="Pups Project",
        choices=(
            (ProjectAssignmentMode.SIRE, "Use sire project"),
            (ProjectAssignmentMode.DAM, "Use dam project"),
            (ProjectAssignmentMode.NEW, "Create a new project"),
        ),
        initial=ProjectAssignmentMode.SIRE,
    )
    new_project_name = forms.CharField(
        max_length=128,
        required=False,
        label="New Project Name",
        help_text="Required when 'Create a new project' is selected.",
    )

    def __init__(self, *args, sire_project=None, dam_project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["target_cage"].queryset = Cage.objects.order_by("cage_id")
        sire_label = sire_project.name if sire_project else "Sire project"
        dam_label = dam_project.name if dam_project else "Dam project"
        self.fields["project_assignment_mode"].choices = (
            (self.ProjectAssignmentMode.SIRE, f"Use sire project ({sire_label})"),
            (self.ProjectAssignmentMode.DAM, f"Use dam project ({dam_label})"),
            (self.ProjectAssignmentMode.NEW, "Create a new project"),
        )

    def clean(self):
        cleaned_data = super().clean()
        mode = cleaned_data.get("project_assignment_mode")
        new_project_name = (cleaned_data.get("new_project_name") or "").strip()
        if mode == self.ProjectAssignmentMode.NEW and not new_project_name:
            self.add_error("new_project_name", "Please enter a project name.")

        target_cage = cleaned_data.get("target_cage")
        lookup = (cleaned_data.get("target_cage_lookup") or "").strip()
        if lookup and not target_cage:
            cage = Cage.objects.filter(cage_id__iexact=lookup).first()
            if cage is None:
                self.add_error("target_cage_lookup", f'No cage found matching "{lookup}".')
            else:
                cleaned_data["target_cage"] = cage
        if not cleaned_data.get("target_cage"):
            self.add_error("target_cage", "Select a target cage or enter a cage ID.")
        return cleaned_data


class PupEntryForm(forms.Form):
    mouse_uid = forms.CharField(max_length=64, label="Mouse UID")
    sex = forms.ChoiceField(choices=Mouse.Sex.choices, label="Sex")
    ear_tag = forms.CharField(max_length=64, required=False, label="Ear Tag")
    coat_color = forms.CharField(max_length=64, required=False, label="Coat Color")
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Notes")


def get_pup_formset(form_count: int):
    """Return a formset class that always renders exactly ``form_count`` pup rows."""
    expected = max(1, int(form_count))
    BaseFormSet = formset_factory(PupEntryForm, extra=0, min_num=expected, validate_min=True)

    class FixedCountPupFormSet(BaseFormSet):
        def total_form_count(self):
            # Ignore stale management-form TOTAL_FORMS from older page renders.
            return expected

    return FixedCountPupFormSet
