from datetime import timedelta

from django import forms
from django.forms import formset_factory, inlineformset_factory
from django.db import IntegrityError, OperationalError, ProgrammingError, transaction
from django.db.models import Q
from django.utils import timezone

from colony.cage_lifecycle import validate_active_sex_compatible_with_cage
from colony.models import Cage, Mouse
from core.models import format_project_owner_label
from .consistency import active_breedings_for_mouse
from .models import Breeding, BreedingExtraFemale, Litter, LitterPup

CAGE_LOOKUP_MATCH_LIMIT = 20
BREEDING_CODE_RETRY_LIMIT = 5


def resolve_cage_from_lookup(lookup: str) -> tuple[Cage | None, str | None]:
    """Resolve a cage from manual entry. Supports exact and partial (icontains) match."""
    query = (lookup or "").strip()
    if not query:
        return None, None
    exact = Cage.objects.filter(cage_id__iexact=query).first()
    if exact is not None:
        return exact, None
    matches = list(Cage.objects.filter(cage_id__icontains=query).order_by("cage_id")[: CAGE_LOOKUP_MATCH_LIMIT + 1])
    if not matches:
        return None, f'No cage found matching "{query}". Create the cage first, then return to this form.'
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > CAGE_LOOKUP_MATCH_LIMIT:
        return None, (
            f'Too many cages match "{query}" (more than {CAGE_LOOKUP_MATCH_LIMIT}). '
            "Enter a more specific cage ID."
        )
    codes = ", ".join(cage.cage_id for cage in matches[:5])
    suffix = "…" if len(matches) > 5 else ""
    return None, f'Multiple cages match "{query}": {codes}{suffix}. Enter a more specific cage ID.'


class BreedingForm(forms.ModelForm):
    cage_lookup = forms.CharField(
        max_length=64,
        required=False,
        label="Or enter cage ID",
        help_text="Partial cage ID is supported. Must match an existing active cage.",
    )
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
        self._auto_generated_breeding_code = False
        self.fields["breeding_code"].help_text = "Example: BR-2026-001 or Lyz2xTet2-01."
        self.fields["breeding_code"].required = False
        self.fields["breeding_code"].widget.attrs.update(
            {"placeholder": "Optional; auto-generated if left blank."}
        )
        self.fields["cage"].queryset = Cage.objects.filter(status=Cage.Status.ACTIVE).order_by("cage_id")
        self.fields["cage"].required = False
        male_qs = Mouse.objects.filter(sex=Mouse.Sex.MALE).order_by("mouse_uid")
        self.fields["male"].queryset = male_qs
        self.fields["male"].required = False
        female_qs = Mouse.objects.filter(sex=Mouse.Sex.FEMALE).order_by("mouse_uid")
        self.fields["female_1"].queryset = female_qs
        self.fields["female_1"].required = False
        self.fields["female_2"].queryset = female_qs
        self.fields["sire"].queryset = Mouse.objects.none()
        self.fields["dams"].queryset = Mouse.objects.none()
        self.fields["extra_females"].queryset = female_qs
        if self.is_bound:
            bound_ids: list[int] = []
            sire_raw = str(self.data.get("sire") or "").strip()
            if sire_raw.isdigit():
                bound_ids.append(int(sire_raw))
            if hasattr(self.data, "getlist"):
                dam_raws = self.data.getlist("dams")
            else:
                dams_val = self.data.get("dams")
                if dams_val is None:
                    dam_raws = []
                elif isinstance(dams_val, (list, tuple)):
                    dam_raws = list(dams_val)
                else:
                    dam_raws = [dams_val]
            for raw in dam_raws:
                raw_text = str(raw or "").strip()
                if raw_text.isdigit():
                    bound_ids.append(int(raw_text))
            if bound_ids:
                bound_mice = Mouse.objects.filter(pk__in=bound_ids)
                self.fields["sire"].queryset = bound_mice.filter(sex=Mouse.Sex.MALE)
                self.fields["dams"].queryset = bound_mice.filter(sex=Mouse.Sex.FEMALE)
        self.warning_messages: list[str] = []
        self.member_rows: list[dict] = []
        if self.instance.pk:
            extra_ids = list(self.instance.extra_female_links.select_related("mouse").values_list("mouse_id", flat=True))
            dams_initial = [x for x in [self.instance.female_1_id, self.instance.female_2_id, *extra_ids] if x]
            self.fields["sire"].initial = self.instance.male_id
            self.fields["dams"].initial = dams_initial
            if not self.is_bound:
                selected_ids = [x for x in [self.instance.male_id, *dams_initial] if x]
                selected_mice = Mouse.objects.filter(pk__in=selected_ids)
                self.fields["sire"].queryset = selected_mice.filter(sex=Mouse.Sex.MALE)
                self.fields["dams"].queryset = selected_mice.filter(sex=Mouse.Sex.FEMALE)

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
            self._auto_generated_breeding_code = True
        else:
            self._auto_generated_breeding_code = False

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
        selected_mice = self._selected_mice()
        project_labels: dict[int, str] = {}
        owner_labels: dict[int, str] = {}
        for mouse in selected_mice:
            if mouse.project_id:
                project_labels.setdefault(mouse.project_id, mouse.project.name)
                if mouse.project.owner_id:
                    owner = mouse.project.owner
                    owner_labels.setdefault(
                        owner.pk,
                        (format_project_owner_label(owner) or owner.get_username() or str(owner.pk)).strip(),
                    )
        if len(project_labels) > 1:
            warning_messages.append(
                "Selected breeders come from multiple projects: "
                f"{', '.join(project_labels.values())}. Saving is allowed."
            )
        if len(owner_labels) > 1:
            warning_messages.append(
                "Selected breeders come from multiple users: "
                f"{', '.join(owner_labels.values())}. Saving is allowed."
            )
        for mouse in selected_mice:
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

        cage = cleaned_data.get("cage")
        lookup = (cleaned_data.get("cage_lookup") or "").strip()
        if lookup:
            resolved, err = resolve_cage_from_lookup(lookup)
            if err:
                self.add_error("cage_lookup", err)
            elif resolved is not None:
                if resolved.status != Cage.Status.ACTIVE:
                    self.add_error("cage_lookup", f"Cage {resolved.cage_id} is not active.")
                else:
                    cleaned_data["cage"] = resolved
        if not self.errors.get("cage_lookup") and not cleaned_data.get("cage"):
            self.add_error("cage", "Select a cage or enter a cage ID.")

        return cleaned_data

    def save(self, commit=True):
        if not (self.cleaned_data.get("breeding_code") or "").strip():
            self.cleaned_data["breeding_code"] = self._generate_breeding_code()
            self._auto_generated_breeding_code = True
        self.instance.breeding_code = self.cleaned_data["breeding_code"]
        if not commit:
            breeding = super().save(commit=False)
            return breeding

        last_error: IntegrityError | None = None
        for _attempt in range(BREEDING_CODE_RETRY_LIMIT):
            try:
                with transaction.atomic():
                    breeding = super().save(commit=True)
                break
            except IntegrityError as exc:
                if not self._auto_generated_breeding_code:
                    raise
                last_error = exc
                self.cleaned_data["breeding_code"] = self._generate_breeding_code()
                self.instance.breeding_code = self.cleaned_data["breeding_code"]
        else:
            if last_error is not None:
                raise last_error
            raise IntegrityError("Failed to allocate a breeding code.")

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


class EndBreedingForm(forms.Form):
    class MemberAction:
        MOVE = "move"

    TERMINAL_ACTIONS = {
        Mouse.Status.EUTHANIZED,
        Mouse.Status.CULLED,
        Mouse.Status.DEAD,
    }
    ACTION_CHOICES = [
        (MemberAction.MOVE, "Move to another cage"),
        (Mouse.Status.EUTHANIZED, "Euthanized"),
        (Mouse.Status.CULLED, "Culled"),
        (Mouse.Status.DEAD, "Found dead"),
    ]

    end_date = forms.DateField(
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="End date",
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Notes",
    )

    def __init__(self, *args, breeding: Breeding, members: list[Mouse], **kwargs):
        self.breeding = breeding
        self.members = list(members)
        self.destination_map: dict[int, Cage | None] = {}
        self.action_map: dict[int, str] = {}
        super().__init__(*args, **kwargs)
        cage_queryset = Cage.objects.filter(status=Cage.Status.ACTIVE).order_by("cage_id")
        if self.breeding.cage_id:
            cage_queryset = cage_queryset.exclude(pk=self.breeding.cage_id)
        for mouse in self.members:
            action_name = self.action_field_name(mouse)
            destination_name = self.destination_field_name(mouse)
            self.fields[action_name] = forms.ChoiceField(
                choices=self.ACTION_CHOICES,
                initial=self.MemberAction.MOVE,
                label=f"{mouse.mouse_uid} action",
            )
            self.fields[action_name].widget.attrs.update({"class": "filter-control end-breeding-action"})
            self.fields[destination_name] = forms.ModelChoiceField(
                queryset=cage_queryset,
                required=False,
                label=f"{mouse.mouse_uid} destination cage",
                empty_label="Select destination cage",
            )
            self.fields[destination_name].widget.attrs.update({"class": "filter-control"})
        self.member_rows = [
            {
                "mouse": mouse,
                "action": self[self.action_field_name(mouse)],
                "destination": self[self.destination_field_name(mouse)],
            }
            for mouse in self.members
        ]

    @staticmethod
    def action_field_name(mouse: Mouse) -> str:
        return f"member_action_{mouse.pk}"

    @staticmethod
    def destination_field_name(mouse: Mouse) -> str:
        return f"destination_cage_{mouse.pk}"

    def clean(self):
        cleaned_data = super().clean()
        self.destination_map = {}
        self.action_map = {}
        if not self.members:
            raise forms.ValidationError("This breeding has no breeder members to move.")

        member_ids = [mouse.pk for mouse in self.members]
        proposed_by_cage: dict[int, list[Mouse]] = {}
        for mouse in self.members:
            action_name = self.action_field_name(mouse)
            destination_name = self.destination_field_name(mouse)
            action = cleaned_data.get(action_name) or self.MemberAction.MOVE
            destination = cleaned_data.get(destination_name)
            if action not in {self.MemberAction.MOVE, *self.TERMINAL_ACTIONS}:
                self.add_error(action_name, "Choose how to handle this breeder.")
                continue
            self.action_map[mouse.pk] = action
            if action == self.MemberAction.MOVE:
                if not destination:
                    self.add_error(destination_name, "Select a destination cage, or choose a terminal outcome.")
                    continue
                if self.breeding.cage_id and destination.pk == self.breeding.cage_id:
                    self.add_error(destination_name, "Choose a cage other than the breeding cage being ended.")
                    continue
                other_active_qs = active_breedings_for_mouse(mouse).exclude(pk=self.breeding.pk)
                if self.breeding.cage_id:
                    other_active_qs = other_active_qs.exclude(cage_id=self.breeding.cage_id)
                other_active_breedings = list(other_active_qs.select_related("cage").order_by("breeding_code"))
                if other_active_breedings:
                    other_codes = ", ".join(b.breeding_code for b in other_active_breedings)
                    other_cage_ids = {b.cage_id for b in other_active_breedings if b.cage_id}
                    if len(other_cage_ids) != 1:
                        self.add_error(
                            destination_name,
                            (
                                f"{mouse.mouse_uid} is still assigned to active breeding(s) {other_codes}. "
                                "Resolve those breeding records before moving this mouse."
                            ),
                        )
                        continue
                    other_cage_id = next(iter(other_cage_ids))
                    if destination.pk != other_cage_id:
                        other_cage = next(b.cage for b in other_active_breedings if b.cage_id == other_cage_id)
                        self.add_error(
                            destination_name,
                            (
                                f"{mouse.mouse_uid} is still assigned to active breeding(s) {other_codes}. "
                                f"Move it to that breeding cage ({other_cage.cage_id}), or end that breeding first."
                            ),
                        )
                        continue
                self.destination_map[mouse.pk] = destination
                if mouse.status == Mouse.Status.ACTIVE:
                    proposed_by_cage.setdefault(destination.pk, []).append(mouse)
            else:
                if destination:
                    self.add_error(destination_name, "Leave destination cage empty when the mouse is terminal.")
                    continue
                self.destination_map[mouse.pk] = None

        for cage_id, moving_mice in proposed_by_cage.items():
            cage = self.destination_map.get(moving_mice[0].pk) or Cage.objects.filter(pk=cage_id).first()
            validate_active_sex_compatible_with_cage(
                cage,
                [mouse.sex for mouse in moving_mice],
                exclude_mouse_ids=member_ids,
            )
        return cleaned_data


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


class LitterRecordForm(forms.ModelForm):
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
            "tail_tag_date",
            "notes",
        ]
        widgets = {
            "birth_date": forms.DateInput(attrs={"type": "date"}),
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
    class ParentageMode:
        BREEDING_CAGE = "breeding_cage"
        SELECT_PARENTS = "select_parents"

    class ProjectAssignmentMode:
        SIRE = "sire"
        DAM = "dam"
        NEW = "new"

    class StrainAssignmentMode:
        SIRE = "sire"
        DAM = "dam"
        NEW = "new"

    parentage_mode = forms.ChoiceField(
        label="Parentage",
        required=False,
        choices=(
            (ParentageMode.BREEDING_CAGE, "Use breeding cage parents (dam uncertain)"),
            (ParentageMode.SELECT_PARENTS, "Select sire and possible dam(s)"),
        ),
        initial=ParentageMode.BREEDING_CAGE,
    )
    parent_breeding = forms.ModelChoiceField(
        queryset=Breeding.objects.none(),
        label="Breeding cage",
        required=False,
        help_text="Only breeding cage records are listed. Selecting a cage uses its sire and all dams.",
    )
    wean_sire = forms.ModelChoiceField(
        queryset=Mouse.objects.none(),
        label="Sire",
        required=False,
    )
    wean_possible_dams = forms.ModelMultipleChoiceField(
        queryset=Mouse.objects.none(),
        label="Possible dam(s)",
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Select one dam if known, or multiple dams when the exact mother is unknown.",
    )
    male_cage = forms.ModelChoiceField(
        queryset=Cage.objects.none(),
        label="Male pups cage",
        required=False,
    )
    male_cage_lookup = forms.CharField(
        max_length=64,
        required=False,
        label="Or enter male cage ID",
        help_text="Partial cage ID supported. Required when weaning male pups.",
    )
    female_cage = forms.ModelChoiceField(
        queryset=Cage.objects.none(),
        label="Female pups cage",
        required=False,
    )
    female_cage_lookup = forms.CharField(
        max_length=64,
        required=False,
        label="Or enter female cage ID",
        help_text="Partial cage ID supported. Required when weaning female pups.",
    )
    wean_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), label="Wean Date")
    male_pup_count = forms.IntegerField(min_value=0, label="Male pups", initial=0)
    female_pup_count = forms.IntegerField(min_value=0, label="Female pups", initial=0)
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
    strain_assignment_mode = forms.ChoiceField(
        label="Pups Strain Line",
        choices=(
            (StrainAssignmentMode.SIRE, "Follow sire strain line"),
            (StrainAssignmentMode.DAM, "Follow dam strain line"),
            (StrainAssignmentMode.NEW, "Create a new strain line"),
        ),
        initial=StrainAssignmentMode.DAM,
    )
    new_strain_line_name = forms.CharField(
        max_length=128,
        required=False,
        label="New Strain Line Name",
        help_text="Required when 'Create a new strain line' is selected. Must be unique.",
    )

    def __init__(
        self,
        *args,
        sire_project=None,
        dam_project=None,
        sire_strain=None,
        dam_strain=None,
        parent_breeding=None,
        parent_breeding_queryset=None,
        parent_sire=None,
        parent_dams=None,
        pup_male_count=0,
        pup_female_count=0,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.pup_male_count = int(pup_male_count or 0)
        self.pup_female_count = int(pup_female_count or 0)
        self.default_parent_breeding = parent_breeding
        self.default_parent_sire = parent_sire
        self.default_parent_dams = list(parent_dams or [])
        self.fields["parent_breeding"].queryset = (
            parent_breeding_queryset
            if parent_breeding_queryset is not None
            else Breeding.objects.filter(pk=parent_breeding.pk)
            if parent_breeding is not None
            else Breeding.objects.none()
        )
        self.fields["parent_breeding"].label_from_instance = self._parent_breeding_label
        sire_ids = [parent_sire.pk] if parent_sire is not None else []
        dam_ids = [dam.pk for dam in self.default_parent_dams]
        self.fields["wean_sire"].queryset = Mouse.objects.filter(pk__in=sire_ids).order_by("mouse_uid")
        self.fields["wean_possible_dams"].queryset = Mouse.objects.filter(pk__in=dam_ids).order_by("mouse_uid")
        if not self.is_bound:
            self.initial.setdefault(
                "parentage_mode",
                self.ParentageMode.BREEDING_CAGE
                if parent_breeding is not None and len(self.default_parent_dams) > 1
                else self.ParentageMode.SELECT_PARENTS,
            )
            if parent_breeding is not None:
                self.initial.setdefault("parent_breeding", parent_breeding.pk)
            if parent_sire is not None:
                self.initial.setdefault("wean_sire", parent_sire.pk)
            if self.default_parent_dams:
                self.initial.setdefault("wean_possible_dams", [dam.pk for dam in self.default_parent_dams])
        active_cages = Cage.objects.filter(status=Cage.Status.ACTIVE).order_by("cage_id")
        self.fields["male_cage"].queryset = active_cages
        self.fields["female_cage"].queryset = active_cages
        sire_label = sire_project.name if sire_project else "Sire project"
        dam_label = dam_project.name if dam_project else "Dam project"
        self.fields["project_assignment_mode"].choices = (
            (self.ProjectAssignmentMode.SIRE, f"Use sire project ({sire_label})"),
            (self.ProjectAssignmentMode.DAM, f"Use dam project ({dam_label})"),
            (self.ProjectAssignmentMode.NEW, "Create a new project"),
        )
        sire_strain_label = sire_strain.line_name if sire_strain else "No strain line"
        dam_strain_label = dam_strain.line_name if dam_strain else "No strain line"
        self.fields["strain_assignment_mode"].choices = (
            (self.StrainAssignmentMode.SIRE, f"Follow sire strain line ({sire_strain_label})"),
            (self.StrainAssignmentMode.DAM, f"Follow dam strain line ({dam_strain_label})"),
            (self.StrainAssignmentMode.NEW, "Create a new strain line"),
        )

    @staticmethod
    def _parent_breeding_label(breeding: Breeding) -> str:
        cage = breeding.cage.cage_id if breeding.cage_id else "No cage"
        status = breeding.get_status_display()
        return f"{cage} — {breeding.breeding_code} ({status})"

    def clean(self):
        cleaned_data = super().clean()
        parentage_mode = cleaned_data.get("parentage_mode") or self.ParentageMode.BREEDING_CAGE
        parent_breeding = cleaned_data.get("parent_breeding") or self.default_parent_breeding
        if self.is_bound and parentage_mode == self.ParentageMode.SELECT_PARENTS:
            sire = cleaned_data.get("wean_sire")
            possible_dams = list(cleaned_data.get("wean_possible_dams") or [])
        else:
            sire = cleaned_data.get("wean_sire") or self.default_parent_sire
            possible_dams = list(cleaned_data.get("wean_possible_dams") or self.default_parent_dams)
        if parentage_mode == self.ParentageMode.BREEDING_CAGE:
            if parent_breeding is None:
                self.add_error("parent_breeding", "Select a breeding cage.")
            else:
                sire, possible_dams = _breeding_parent_mice(parent_breeding)
                if sire is None:
                    self.add_error("parent_breeding", "Selected breeding cage has no sire.")
                if not possible_dams:
                    self.add_error("parent_breeding", "Selected breeding cage has no dam.")
        else:
            if sire is None:
                self.add_error("wean_sire", "Select a sire.")
            if not possible_dams:
                self.add_error("wean_possible_dams", "Select at least one possible dam.")
        if sire is not None and sire.sex != Mouse.Sex.MALE:
            self.add_error("wean_sire", "Sire must be a male mouse.")
        for dam in possible_dams:
            if dam.sex != Mouse.Sex.FEMALE:
                self.add_error("wean_possible_dams", f"{dam.mouse_uid} is not a female mouse.")
                break
        cleaned_data["parentage_mode"] = parentage_mode
        cleaned_data["resolved_parent_breeding"] = parent_breeding
        cleaned_data["resolved_sire"] = sire
        cleaned_data["resolved_possible_dams"] = possible_dams

        mode = cleaned_data.get("project_assignment_mode")
        new_project_name = (cleaned_data.get("new_project_name") or "").strip()
        if mode == self.ProjectAssignmentMode.NEW and not new_project_name:
            self.add_error("new_project_name", "Please enter a project name.")

        strain_mode = cleaned_data.get("strain_assignment_mode")
        new_strain_line_name = (cleaned_data.get("new_strain_line_name") or "").strip()
        if strain_mode == self.StrainAssignmentMode.NEW and not new_strain_line_name:
            self.add_error("new_strain_line_name", "Please enter a strain line name.")

        male_pups = int(cleaned_data.get("male_pup_count") or 0)
        female_pups = int(cleaned_data.get("female_pup_count") or 0)
        if male_pups + female_pups < 1:
            self.add_error("male_pup_count", "Enter at least one male or female pup to wean.")

        male_cage = _resolve_wean_cage_assignment(
            self,
            cleaned_data,
            cage_field="male_cage",
            lookup_field="male_cage_lookup",
        )
        female_cage = _resolve_wean_cage_assignment(
            self,
            cleaned_data,
            cage_field="female_cage",
            lookup_field="female_cage_lookup",
        )
        if self.pup_male_count > 0 and male_cage is None and not self.errors.get("male_cage_lookup"):
            self.add_error("male_cage", "Select a cage for male pups or enter a male cage ID.")
        if self.pup_female_count > 0 and female_cage is None and not self.errors.get("female_cage_lookup"):
            self.add_error("female_cage", "Select a cage for female pups or enter a female cage ID.")
        if (
            self.pup_male_count > 0
            and self.pup_female_count > 0
            and male_cage is not None
            and female_cage is not None
            and male_cage.pk == female_cage.pk
        ):
            self.add_error("female_cage", "Male and female pups must be placed in different cages.")
        if self.pup_male_count > 0 and male_cage is not None:
            try:
                validate_active_sex_compatible_with_cage(male_cage, [Mouse.Sex.MALE])
            except forms.ValidationError as exc:
                self.add_error("male_cage", exc)
        if self.pup_female_count > 0 and female_cage is not None:
            try:
                validate_active_sex_compatible_with_cage(female_cage, [Mouse.Sex.FEMALE])
            except forms.ValidationError as exc:
                self.add_error("female_cage", exc)
        cleaned_data["male_cage"] = male_cage
        cleaned_data["female_cage"] = female_cage
        return cleaned_data


def _resolve_wean_cage_assignment(form, cleaned_data, *, cage_field: str, lookup_field: str) -> Cage | None:
    cage = cleaned_data.get(cage_field)
    lookup = (cleaned_data.get(lookup_field) or "").strip()
    if lookup and not cage:
        resolved, err = resolve_cage_from_lookup(lookup)
        if err:
            form.add_error(lookup_field, err)
            return None
        if resolved is not None and resolved.status != Cage.Status.ACTIVE:
            form.add_error(lookup_field, f"Cage {resolved.cage_id} is not active.")
            return None
        return resolved
    if cage is not None and cage.status != Cage.Status.ACTIVE:
        form.add_error(cage_field, f"Cage {cage.cage_id} is not active.")
        return None
    return cage


def _breeding_parent_mice(breeding: Breeding) -> tuple[Mouse | None, list[Mouse]]:
    members = list(breeding.breeding_members.select_related("mouse").order_by("sort_order", "mouse__mouse_uid"))
    if members:
        sire = next((row.mouse for row in members if row.role == Breeding.MemberRole.SIRE), None)
        dams = [row.mouse for row in members if row.role == Breeding.MemberRole.DAM]
        return sire, dams
    dams = [dam for dam in (breeding.female_1, breeding.female_2) if dam is not None]
    extra_dams = [row.mouse for row in breeding.extra_female_links.select_related("mouse").order_by("mouse__mouse_uid")]
    seen = {dam.pk for dam in dams}
    for dam in extra_dams:
        if dam.pk not in seen:
            seen.add(dam.pk)
            dams.append(dam)
    return breeding.male, dams


class PupEntryForm(forms.Form):
    mouse_uid = forms.CharField(max_length=64, label="Mouse UID")
    sex = forms.ChoiceField(choices=Mouse.Sex.choices, label="Sex")
    ear_tag = forms.CharField(max_length=64, required=False, label="Ear Tag")
    coat_color = forms.CharField(max_length=64, required=False, label="Coat Color")
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Notes")


class WeanPupEntryForm(PupEntryForm):
    sex = forms.ChoiceField(
        choices=(
            (Mouse.Sex.MALE, "Male"),
            (Mouse.Sex.FEMALE, "Female"),
        ),
        label="Sex",
    )

    def clean_sex(self):
        sex = self.cleaned_data.get("sex")
        if sex not in {Mouse.Sex.MALE, Mouse.Sex.FEMALE}:
            raise forms.ValidationError("Select Male or Female before weaning.")
        return sex


def get_pup_formset(form_count: int):
    """Return a formset class that always renders exactly ``form_count`` pup rows."""
    expected = max(1, int(form_count))
    BaseFormSet = formset_factory(PupEntryForm, extra=0, min_num=expected, validate_min=True)

    class FixedCountPupFormSet(BaseFormSet):
        def total_form_count(self):
            # Ignore stale management-form TOTAL_FORMS from older page renders.
            return expected

    return FixedCountPupFormSet
