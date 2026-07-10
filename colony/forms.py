import json

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils.html import format_html
from django.utils import timezone

from users.import_prefix import get_effective_import_prefix
from users.permissions import can_manage_strain_lines, is_admin
from breeding.forms import resolve_cage_from_lookup

from core.models import Project, format_project_owner_label

from .cage_form_helpers import editable_active_cage_queryset, editable_cage_queryset
from .cage_lifecycle import (
    TERMINAL_MOUSE_STATUSES,
    validate_active_breeding_cage_entry,
    validate_active_sex_compatible_with_cage,
)
from .id_uniqueness import normalize_identifier, validate_cage_id_available, validate_mouse_uid_available
from .models import Cage, Colony, Mouse, MouseGenotypeComponent, StrainLine
from .strain_line_choices import (
    CUSTOM_SELECT_VALUE,
    choice_field_with_custom,
    preset_select_initial,
    resolve_choice_or_custom,
)


MAX_IMPORT_FILE_BYTES = 10 * 1024 * 1024

ADMIN_CORRECTION_REASON_CHOICES = [
    ("", "— Select reason —"),
    ("Initial data entry correction", "Initial data entry correction"),
    ("Historical import correction", "Historical import correction"),
    ("Wrong record selected during workflow", "Wrong record selected during workflow"),
    ("Project manager reviewed correction", "Project manager reviewed correction"),
    ("Admin reviewed correction", "Admin reviewed correction"),
    ("Other approved data correction", "Other approved data correction"),
]

MOUSE_RESTORE_REASON_CHOICES = [
    ("", "— Select reason —"),
    ("Mistaken endpoint / euthanasia entry", "Mistaken endpoint / euthanasia entry"),
    ("Wrong mouse was ended", "Wrong mouse was ended"),
    ("Historical import correction", "Historical import correction"),
    ("Project manager reviewed correction", "Project manager reviewed correction"),
    ("Admin reviewed correction", "Admin reviewed correction"),
    ("Other approved data correction", "Other approved data correction"),
]

CAGE_RESTORE_REASON_CHOICES = [
    ("", "— Select reason —"),
    ("Mistaken cage close / retire entry", "Mistaken cage close / retire entry"),
    ("Wrong cage was retired", "Wrong cage was retired"),
    ("Cage card / ID still in use", "Cage card / ID still in use"),
    ("Historical import correction", "Historical import correction"),
    ("Project manager reviewed correction", "Project manager reviewed correction"),
    ("Admin reviewed correction", "Admin reviewed correction"),
    ("Other approved data correction", "Other approved data correction"),
]

MOUSE_SEX_CORRECTION_REASON_CHOICES = [
    ("", "— Select reason —"),
    ("Sex entered incorrectly at weaning", "Sex entered incorrectly at weaning"),
    ("Sex entered incorrectly during import", "Sex entered incorrectly during import"),
    ("Physical recheck confirmed sex", "Physical recheck confirmed sex"),
    ("Genotype or breeding record review confirmed sex", "Genotype or breeding record review confirmed sex"),
    ("Project manager reviewed correction", "Project manager reviewed correction"),
    ("Admin reviewed correction", "Admin reviewed correction"),
    ("Other approved data correction", "Other approved data correction"),
]


def validate_import_file_upload(uploaded, *, max_bytes: int = MAX_IMPORT_FILE_BYTES):
    name = (getattr(uploaded, "name", "") or "").strip()
    lower_name = name.lower()
    if not (lower_name.endswith(".csv") or lower_name.endswith(".xlsx")):
        raise ValidationError("Upload a .csv or .xlsx file.")
    size = getattr(uploaded, "size", 0) or 0
    if size <= 0:
        raise ValidationError(f"{name or 'File'} is empty.")
    if size > max_bytes:
        raise ValidationError(
            f"{name or 'File'} is too large ({size // (1024 * 1024)} MB). Maximum is {max_bytes // (1024 * 1024)} MB."
        )
    return uploaded


def _append_widget_class(field, css_class: str) -> None:
    existing = field.widget.attrs.get("class", "")
    classes = existing.split()
    if css_class not in classes:
        classes.append(css_class)
    field.widget.attrs["class"] = " ".join(classes).strip()


def _mark_admin_correction_field(field) -> None:
    field.widget.attrs["data-admin-correction-field"] = "1"
    _append_widget_class(field, "admin-correction-field")


def _lock_field(field) -> None:
    field.disabled = True
    field.widget.attrs["aria-disabled"] = "true"
    field.widget.attrs["title"] = "Locked after creation. Admins can unlock correction mode."
    _append_widget_class(field, "input-locked")


def _limited_codes(queryset, attr: str = "breeding_code", *, limit: int = 5) -> str:
    values = list(queryset.values_list(attr, flat=True)[: limit + 1])
    if not values:
        return ""
    suffix = "..." if len(values) > limit else ""
    return ", ".join(str(value) for value in values[:limit]) + suffix


def mouse_sex_correction_conflicts(mouse: Mouse, target_sex: str) -> list[str]:
    conflicts: list[str] = []
    if target_sex not in {Mouse.Sex.MALE, Mouse.Sex.FEMALE, Mouse.Sex.UNKNOWN}:
        return ["Select a valid sex."]

    if mouse.status == Mouse.Status.ACTIVE and mouse.current_cage_id:
        try:
            validate_active_sex_compatible_with_cage(
                mouse.current_cage,
                [target_sex],
                exclude_mouse_ids=[mouse.pk],
            )
        except ValidationError as exc:
            conflicts.extend(exc.messages)

    from breeding.models import Breeding

    sire_breedings = Breeding.objects.filter(male=mouse).order_by("breeding_code")
    sire_member_breedings = Breeding.objects.filter(
        breeding_members__mouse=mouse,
        breeding_members__role=Breeding.MemberRole.SIRE,
    ).order_by("breeding_code")
    dam_breedings = Breeding.objects.filter(
        Q(female_1=mouse) | Q(female_2=mouse) | Q(extra_female_links__mouse=mouse)
    ).distinct().order_by("breeding_code")
    dam_member_breedings = Breeding.objects.filter(
        breeding_members__mouse=mouse,
        breeding_members__role=Breeding.MemberRole.DAM,
    ).order_by("breeding_code")

    if target_sex != Mouse.Sex.MALE:
        sire_codes = _limited_codes(sire_breedings)
        member_codes = _limited_codes(sire_member_breedings)
        if sire_codes or member_codes:
            codes = ", ".join(part for part in [sire_codes, member_codes] if part)
            conflicts.append(f"This mouse is recorded as sire in breeding(s): {codes}.")
        if mouse.offspring_from_sire.exists():
            conflicts.append("This mouse is recorded as sire for offspring records.")

    if target_sex != Mouse.Sex.FEMALE:
        dam_codes = _limited_codes(dam_breedings)
        member_codes = _limited_codes(dam_member_breedings)
        if dam_codes or member_codes:
            codes = ", ".join(part for part in [dam_codes, member_codes] if part)
            conflicts.append(f"This mouse is recorded as dam in breeding(s): {codes}.")
        if mouse.offspring_from_dam.exists() or mouse.possible_offspring_from_dam.exists():
            conflicts.append("This mouse is recorded as dam or possible dam for offspring records.")

    genotype_rows = mouse.genotype_components.all()
    if target_sex == Mouse.Sex.FEMALE:
        if genotype_rows.filter(chromosome_type=MouseGenotypeComponent.ChromosomeType.Y_LINKED).exists():
            conflicts.append("This mouse has Y-linked genotype rows, which are not valid for female mice.")
        if genotype_rows.filter(
            chromosome_type=MouseGenotypeComponent.ChromosomeType.X_LINKED,
            allele_display_2__iexact="Y",
        ).exists():
            conflicts.append("This mouse has X-linked X/Y genotype rows, which are not valid for female mice.")
    elif target_sex == Mouse.Sex.MALE:
        invalid_x_rows = genotype_rows.filter(
            chromosome_type=MouseGenotypeComponent.ChromosomeType.X_LINKED,
        ).exclude(Q(allele_display_2__iexact="Y") | Q(allele_display_2=""))
        if invalid_x_rows.exists():
            conflicts.append("This mouse has female-style X-linked genotype rows; correct genotype rows first.")

    return conflicts


def _cage_project_queryset_for_user(user):
    projects = Project.objects.filter(is_active=True).order_by("name")
    if user is None or is_admin(user):
        return projects
    if not getattr(user, "is_authenticated", False):
        return Project.objects.none()
    return projects.filter(Q(owner=user) | Q(memberships__user=user)).distinct()


def _default_cage_project_for_user(user, project_queryset):
    if user is None or is_admin(user) or not getattr(user, "is_authenticated", False):
        return None
    owned_projects = Project.objects.filter(owner=user, is_active=True).order_by("name")
    if owned_projects.count() == 1:
        return owned_projects.first()
    if project_queryset.count() == 1:
        return project_queryset.first()
    return None


def _strain_line_project_queryset_for_user(user):
    projects = Project.objects.filter(is_active=True).order_by("name")
    if user is None or can_manage_strain_lines(user):
        return projects
    if not getattr(user, "is_authenticated", False):
        return Project.objects.none()
    return projects.filter(Q(owner=user) | Q(memberships__user=user)).distinct()


class AdminCorrectionFormMixin:
    admin_correction_frozen_fields: set[str] = set()

    def _configure_admin_correction(
        self,
        *,
        user=None,
        admin_correction_unlocked: bool = False,
        correction_allowed: bool = False,
    ) -> None:
        if user is None:
            self.admin_correction_is_admin = False
            self.admin_correction_unlocked = False
            self.admin_correction_available = False
            return
        self.admin_correction_is_admin = bool(is_admin(user) or correction_allowed)
        self.admin_correction_unlocked = bool(self.admin_correction_is_admin and admin_correction_unlocked)
        self.admin_correction_available = bool(self.instance and self.instance.pk and self.admin_correction_is_admin)
        if not (self.instance and self.instance.pk):
            return
        for name in self.admin_correction_frozen_fields:
            field = self.fields.get(name)
            if field is None:
                continue
            _mark_admin_correction_field(field)
            if not self.admin_correction_unlocked:
                _lock_field(field)

    def admin_correction_changed_fields(self) -> list[str]:
        return [name for name in self.changed_data if name in self.admin_correction_frozen_fields]


class CageForm(AdminCorrectionFormMixin, forms.ModelForm):
    admin_correction_frozen_fields = {
        "cage_id",
        "created_date",
        "project",
        "colony",
        "status",
    }

    room = forms.ChoiceField(
        required=False,
        choices=[],
        widget=forms.Select(attrs={"class": "filter-control", "id": "id_room"}),
        label="Room",
    )
    room_custom = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "filter-control strain-custom-field",
                "id": "id_room_custom",
                "placeholder": "Custom room",
            }
        ),
        label="Custom room",
    )
    cage_use = forms.ChoiceField(
        label="Cage Use",
        choices=[],
        initial=Cage.CageUse.HOLDING,
        widget=forms.Select(attrs={"class": "filter-control"}),
        help_text="Current workflow use of this cage. Breeding and weaning uses apply the correct internal cage settings automatically.",
    )

    class Meta:
        model = Cage
        fields = [
            "cage_id",
            "created_date",
            "project",
            "colony",
            "rack",
            "position",
            "cage_use",
            "status",
            "notes",
        ]
        widgets = {
            "created_date": forms.DateInput(attrs={"type": "date"}),
            "project": forms.Select(attrs={"class": "filter-control"}),
            "colony": forms.Select(attrs={"class": "filter-control"}),
            "rack": forms.TextInput(attrs={"placeholder": "e.g. R1 or Rack-A"}),
            "position": forms.TextInput(attrs={"placeholder": "e.g. A1"}),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }
        labels = {
            "colony": "Default Colony / Intended Line",
        }
        help_texts = {
            "project": "Home project for this cage. This makes empty cages filterable by owner/project.",
            "colony": "Optional intended project + strain line for this cage. Leave blank for mixed-use or uncertain cages.",
            "rack": "Use a consistent rack label, for example R1 or Rack-A.",
            "position": "Use a consistent position label, for example A1.",
        }

    def _room_choices(self) -> list[tuple[str, str]]:
        rooms = list(Cage.objects.exclude(room="").values_list("room", flat=True).distinct().order_by("room"))
        return [("", "— Select room —")] + [(room, room) for room in rooms] + [
            (CUSTOM_SELECT_VALUE, "Custom (type below)")
        ]

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        admin_correction_unlocked = kwargs.pop("admin_correction_unlocked", False)
        super().__init__(*args, **kwargs)
        self.fields["room"].choices = self._room_choices()
        known_rooms = {value for value, _label in self.fields["room"].choices if value and value != CUSTOM_SELECT_VALUE}
        stored = (self.instance.room or "").strip() if self.instance and self.instance.pk else ""
        if stored and stored not in known_rooms:
            self.initial.setdefault("room", CUSTOM_SELECT_VALUE)
            self.initial.setdefault("room_custom", stored)
        elif stored:
            self.initial.setdefault("room", stored)
        is_existing = bool(self.instance and self.instance.pk)
        current_use = self.instance.cage_use if is_existing else ""
        self.fields["cage_use"].choices = Cage.cage_use_choices(
            include_retired=current_use == Cage.CageUse.RETIRED
        )
        if not self.is_bound:
            if is_existing:
                self.initial.setdefault("cage_use", current_use)
            elif "cage_use" not in self.initial:
                self.initial["cage_use"] = Cage.cage_use_from_parts(
                    cage_type=self.initial.get("cage_type", ""),
                    purpose=self.initial.get("purpose", ""),
                )
        project_queryset = _cage_project_queryset_for_user(self.user)
        self.fields["project"].queryset = project_queryset
        self._project_required_for_new_cage = bool(self.user is not None and not is_admin(self.user) and not is_existing)
        self._single_available_project = None
        self.fields["project"].required = False
        if self._project_required_for_new_cage:
            self.fields["project"].help_text = (
                "Required for new cages so they stay visible in owner/project filters."
            )
            project_count = project_queryset.count()
            self._single_available_project = _default_cage_project_for_user(self.user, project_queryset)
            if self._single_available_project is not None:
                if not self.is_bound and "project" not in self.initial:
                    self.initial["project"] = self._single_available_project
            elif project_count > 1:
                self.fields["project"].widget.attrs["required"] = "required"
        self.fields["colony"].queryset = Colony.objects.select_related("project", "strain_line").order_by(
            "project__name",
            "strain_line__line_name",
        )
        self.fields["colony"].required = False
        self.fields["cage_id"].help_text = (
            "Must be unique across the entire system. Retired or archived cage IDs cannot be reused."
        )
        self._configure_admin_correction(
            user=self.user,
            admin_correction_unlocked=admin_correction_unlocked,
        )

    def clean_cage_id(self):
        cage_id = normalize_identifier(self.cleaned_data.get("cage_id"))
        if not cage_id:
            return cage_id
        exclude_pk = self.instance.pk if self.instance and self.instance.pk else None
        validate_cage_id_available(cage_id, exclude_pk=exclude_pk)
        return cage_id

    def clean(self):
        cleaned = super().clean()
        selected = (cleaned.get("room") or "").strip()
        custom = (cleaned.get("room_custom") or "").strip()
        if selected == CUSTOM_SELECT_VALUE:
            if not custom:
                self.add_error("room_custom", "Room is required when Custom is selected.")
            else:
                cleaned["room"] = custom
        elif selected:
            cleaned["room"] = selected
        else:
            cleaned["room"] = ""
        project = cleaned.get("project")
        colony = cleaned.get("colony")
        if colony and project and colony.project_id != project.pk:
            self.add_error("colony", "Colony must belong to the selected home project.")
        if colony and not project:
            cleaned["project"] = colony.project
            project = colony.project
        if self._project_required_for_new_cage and not project:
            project_queryset = self.fields["project"].queryset
            if self._single_available_project is not None:
                cleaned["project"] = self._single_available_project
            elif not project_queryset.exists():
                self.add_error(
                    "project",
                    "Project is required for new cages. Ask an admin to create or assign you to a project first.",
                )
            else:
                self.add_error(
                    "project",
                    "Project is required for new cages. Select the project this cage belongs to.",
                )
        if self.instance and self.instance.pk:
            current_use = self.instance.cage_use
            target_use = cleaned.get("cage_use") or current_use
            manager_workflow_uses = {Cage.CageUse.HOLDING, Cage.CageUse.BREEDING}
            if (
                target_use != current_use
                and not is_admin(self.user)
                and {current_use, target_use} - manager_workflow_uses
            ):
                self.add_error(
                    "cage_use",
                    "Users with cage edit access can switch Cage Use between Holding and Breeding. Other cage-use corrections require an admin.",
                )
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.room = (self.cleaned_data.get("room") or "").strip()
        obj.set_cage_use(self.cleaned_data.get("cage_use") or Cage.CageUse.HOLDING)
        if obj.colony_id and not obj.project_id:
            obj.project_id = obj.colony.project_id
        if commit:
            obj.save()
        return obj


class CageRetireForm(forms.Form):
    retire_date = forms.DateField(
        label="Retire Date",
        widget=forms.DateInput(attrs={"type": "date"}),
        initial=timezone.localdate,
    )
    reason = forms.ChoiceField(
        label="Reason",
        choices=[
            ("Empty cage no longer used", "Empty cage no longer used"),
            ("Cage card / ID retired", "Cage card / ID retired"),
            ("Room or rack cleanup", "Room or rack cleanup"),
            ("Historical test data cleanup", "Historical test data cleanup"),
            ("Duplicate or incorrect cage record", "Duplicate or incorrect cage record"),
            ("Other", "Other"),
        ],
        initial="Empty cage no longer used",
        widget=forms.Select(attrs={"class": "filter-control"}),
    )
    confirm = forms.BooleanField(
        label="I confirm this cage has no current mice and should be removed from active cage pickers.",
    )

    def __init__(self, *args, cage: Cage, **kwargs):
        self.cage = cage
        super().__init__(*args, **kwargs)

    def clean_retire_date(self):
        retire_date = self.cleaned_data["retire_date"]
        if retire_date > timezone.localdate():
            raise forms.ValidationError("Retire date cannot be in the future.")
        return retire_date

    def clean(self):
        cleaned = super().clean()
        if self.cage.status != Cage.Status.ACTIVE:
            self.add_error(None, "Only active cages can be retired through this workflow.")
        if self.cage.current_mice.exists():
            self.add_error(None, "Move or end all current mice before retiring this cage.")

        from breeding.models import Breeding

        active_breeding_exists = (
            Breeding.objects.filter(cage=self.cage, active=True)
            .exclude(status=Breeding.Status.CLOSED)
            .exists()
        )
        if active_breeding_exists:
            self.add_error(None, "End linked active breeding records before retiring this cage.")
        return cleaned


class CageRestoreForm(forms.Form):
    restore_date = forms.DateField(
        label="Restore Date",
        widget=forms.DateInput(attrs={"type": "date"}),
        initial=timezone.localdate,
    )
    cage_use = forms.ChoiceField(
        label="Cage Use",
        choices=[],
        initial=Cage.CageUse.HOLDING,
        widget=forms.Select(attrs={"class": "filter-control"}),
    )
    reason = forms.ChoiceField(
        label="Reason",
        choices=CAGE_RESTORE_REASON_CHOICES,
        widget=forms.Select(attrs={"class": "filter-control"}),
    )
    confirm = forms.BooleanField(
        label="I confirm this cage should return to active cage pickers.",
    )

    def __init__(self, *args, cage: Cage, **kwargs):
        self.cage = cage
        super().__init__(*args, **kwargs)
        self.fields["cage_use"].choices = Cage.cage_use_choices(include_retired=False)
        if not self.is_bound:
            current_use = cage.cage_use
            if current_use == Cage.CageUse.RETIRED:
                current_use = Cage.CageUse.HOLDING
            self.initial.setdefault("cage_use", current_use or Cage.CageUse.HOLDING)

    def clean_restore_date(self):
        restore_date = self.cleaned_data["restore_date"]
        if restore_date > timezone.localdate():
            raise forms.ValidationError("Restore date cannot be in the future.")
        return restore_date

    def clean(self):
        cleaned = super().clean()
        if self.cage.status == Cage.Status.ACTIVE:
            self.add_error(None, "This cage is already active.")
        if self.cage.status == Cage.Status.ARCHIVED:
            self.add_error(None, "Archived cages must be reviewed by an admin outside this workflow.")
        return cleaned


def _coerce_positive_int(value) -> int | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw.isdigit():
        return None
    value_int = int(raw)
    return value_int if value_int > 0 else None


def _selected_mouse_queryset(*values):
    ids = {_coerce_positive_int(value) for value in values}
    ids.discard(None)
    if not ids:
        return Mouse.objects.none()
    return Mouse.objects.filter(pk__in=ids).select_related("project", "strain_line").order_by("mouse_uid")


def _selected_cage_queryset(*values, active_only: bool = True):
    ids = {_coerce_positive_int(value) for value in values}
    ids.discard(None)
    if not ids:
        return Cage.objects.none()
    cages = Cage.objects.filter(pk__in=ids)
    if active_only:
        cages = cages.filter(status=Cage.Status.ACTIVE)
    return cages.order_by("cage_id")


class MouseParentageMode:
    NONE = "none"
    BREEDING_CAGE = "breeding_cage"
    SELECT_PARENTS = "select_parents"


MOUSE_PARENTAGE_CHOICES = (
    (MouseParentageMode.NONE, "No parentage recorded"),
    (MouseParentageMode.BREEDING_CAGE, "Use breeding cage parents (dam uncertain)"),
    (MouseParentageMode.SELECT_PARENTS, "Select sire and possible dam(s)"),
)


def _data_values(data, name: str) -> list:
    if not data:
        return []
    if hasattr(data, "getlist"):
        return list(data.getlist(name))
    value = data.get(name)
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _listify_initial(value) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _breeding_model():
    from breeding.models import Breeding

    return Breeding


def _parent_breeding_queryset(*values):
    Breeding = _breeding_model()
    ids = {_coerce_positive_int(value) for value in values}
    ids.discard(None)
    predicate = Q(active=True)
    if ids:
        predicate |= Q(pk__in=ids)
    return (
        Breeding.objects.filter(predicate)
        .select_related("cage", "male", "female_1", "female_2")
        .prefetch_related("breeding_members__mouse", "extra_female_links__mouse")
        .distinct()
        .order_by("cage__cage_id", "breeding_code")
    )


def _parent_breeding_label(breeding) -> str:
    cage = breeding.cage.cage_id if breeding.cage_id else "No cage"
    status = breeding.get_status_display()
    return f"{cage} - {breeding.breeding_code} ({status})"


def _breeding_parent_mice(breeding) -> tuple[Mouse | None, list[Mouse]]:
    from colony.breeding_pedigree import breeding_sire_and_dams

    return breeding_sire_and_dams(breeding)


def _apply_parentage_field_attrs(form) -> None:
    for name in ("parentage_mode", "source_breeding", "sire", "dam", "possible_dams"):
        field = form.fields.get(name)
        if field is not None:
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{existing} filter-control".strip()


def _configure_mouse_parentage_fields(form, *, instance: Mouse | None = None) -> None:
    selected_source = (
        form.data.get("source_breeding")
        if form.is_bound
        else form.initial.get("source_breeding")
        or (instance.source_breeding_id if instance is not None and instance.pk else None)
    )
    form.fields["source_breeding"].queryset = _parent_breeding_queryset(selected_source)
    form.fields["source_breeding"].label_from_instance = _parent_breeding_label

    source_sire_id = None
    source_dam_ids: list[int] = []
    source_id = _coerce_positive_int(selected_source)
    if source_id:
        breeding = form.fields["source_breeding"].queryset.filter(pk=source_id).first()
        if breeding is not None:
            source_sire, source_dams = _breeding_parent_mice(breeding)
            source_sire_id = source_sire.pk if source_sire is not None else None
            source_dam_ids = [dam.pk for dam in source_dams]

    initial_possible = _data_values(form.data, "possible_dams") if form.is_bound else _listify_initial(form.initial.get("possible_dams"))
    if instance is not None and instance.pk and not form.is_bound:
        if instance.source_breeding_id:
            form.initial.setdefault("source_breeding", instance.source_breeding_id)
        if instance.sire_id:
            form.initial.setdefault("sire", instance.sire_id)
        possible_ids = list(instance.possible_dams.values_list("pk", flat=True))
        if possible_ids:
            form.initial.setdefault("possible_dams", possible_ids)
            initial_possible.extend(possible_ids)
        elif instance.dam_id:
            form.initial.setdefault("possible_dams", [instance.dam_id])
            initial_possible.append(instance.dam_id)

        if instance.source_breeding_id:
            form.initial.setdefault("parentage_mode", MouseParentageMode.BREEDING_CAGE)
        elif instance.sire_id or instance.dam_id or possible_ids:
            form.initial.setdefault("parentage_mode", MouseParentageMode.SELECT_PARENTS)
        else:
            form.initial.setdefault("parentage_mode", MouseParentageMode.NONE)
    elif not form.is_bound:
        form.initial.setdefault("parentage_mode", form.initial.get("parentage_mode") or MouseParentageMode.NONE)

    form.fields["sire"].queryset = _selected_mouse_queryset(
        form.data.get("sire") if form.is_bound else form.initial.get("sire"),
        instance.sire_id if instance is not None and instance.pk else None,
        source_sire_id,
    )
    form.fields["dam"].queryset = _selected_mouse_queryset(
        form.data.get("dam") if form.is_bound else form.initial.get("dam"),
        instance.dam_id if instance is not None and instance.pk else None,
        *initial_possible,
        *source_dam_ids,
    )
    form.fields["possible_dams"].queryset = _selected_mouse_queryset(
        *initial_possible,
        form.data.get("dam") if form.is_bound else form.initial.get("dam"),
        instance.dam_id if instance is not None and instance.pk else None,
        *source_dam_ids,
    )
    form.fields["sire"].label = "Sire"
    form.fields["dam"].label = "Known dam (internal)"
    form.fields["possible_dams"].label = "Possible dam(s)"
    form.fields["possible_dams"].help_text = "Select one dam if known, or multiple dams when the exact mother is unknown."
    _apply_parentage_field_attrs(form)


def _clean_mouse_parentage(form, cleaned_data: dict) -> dict:
    mode = cleaned_data.get("parentage_mode") or MouseParentageMode.NONE
    source_breeding = cleaned_data.get("source_breeding")
    sire = cleaned_data.get("sire")
    hidden_dam = cleaned_data.get("dam")
    possible_dams = list(cleaned_data.get("possible_dams") or [])
    if hidden_dam is not None and all(dam.pk != hidden_dam.pk for dam in possible_dams):
        possible_dams.insert(0, hidden_dam)

    mode_was_posted = bool(getattr(form, "is_bound", False) and "parentage_mode" in getattr(form, "data", {}))
    if (
        mode == MouseParentageMode.NONE
        and not mode_was_posted
        and (source_breeding is not None or sire is not None or possible_dams)
    ):
        mode = MouseParentageMode.BREEDING_CAGE if source_breeding is not None and sire is None and not possible_dams else MouseParentageMode.SELECT_PARENTS

    if mode == MouseParentageMode.BREEDING_CAGE:
        if source_breeding is None:
            form.add_error("source_breeding", "Select a breeding cage, or choose another parentage mode.")
        else:
            sire, possible_dams = _breeding_parent_mice(source_breeding)
            if sire is None:
                form.add_error("source_breeding", "Selected breeding cage has no sire.")
            if not possible_dams:
                form.add_error("source_breeding", "Selected breeding cage has no dam.")
    elif mode == MouseParentageMode.SELECT_PARENTS:
        source_breeding = None
        if sire is None and not possible_dams:
            form.add_error("sire", "Select a sire and/or possible dam(s), or choose No parentage recorded.")
    else:
        mode = MouseParentageMode.NONE
        source_breeding = None
        sire = None
        possible_dams = []

    if sire is not None and sire.sex != Mouse.Sex.MALE:
        form.add_error("sire", "Sire must be a male mouse.")
    for dam in possible_dams:
        if dam.sex != Mouse.Sex.FEMALE:
            form.add_error("possible_dams", f"{dam.mouse_uid} is not a female mouse.")
            break

    cleaned_data["parentage_mode"] = mode
    cleaned_data["source_breeding"] = source_breeding
    cleaned_data["sire"] = sire
    cleaned_data["dam"] = possible_dams[0] if len(possible_dams) == 1 else None
    cleaned_data["possible_dams"] = possible_dams if len(possible_dams) > 1 else []
    return cleaned_data


class MouseForm(AdminCorrectionFormMixin, forms.ModelForm):
    admin_correction_frozen_fields = {
        "mouse_uid",
        "sex",
        "birth_date",
        "death_date",
        "euthanasia_date",
        "death_reason",
        "status",
        "strain_line",
        "sire",
        "dam",
        "source_breeding",
        "possible_dams",
        "parentage_mode",
        "project",
        "origin",
    }

    parentage_mode = forms.ChoiceField(
        choices=MOUSE_PARENTAGE_CHOICES,
        required=False,
        label="Parentage",
        initial=MouseParentageMode.NONE,
    )
    current_cage_lookup = forms.CharField(
        max_length=64,
        required=False,
        label="Or enter cage ID",
        help_text="Partial cage ID is supported. Must match an existing cage.",
    )
    source_breeding = forms.ModelChoiceField(
        queryset=_breeding_model().objects.none(),
        required=False,
        label="Breeding cage",
        help_text="Selecting a breeding cage uses its sire and all dams.",
    )
    possible_dams = forms.ModelMultipleChoiceField(
        queryset=Mouse.objects.none(),
        required=False,
        label="Possible dam(s)",
        widget=forms.SelectMultiple(attrs={"size": 6}),
    )

    class Meta:
        model = Mouse
        fields = [
            "mouse_uid",
            "sex",
            "birth_date",
            "death_date",
            "euthanasia_date",
            "death_reason",
            "status",
            "strain_line",
            "current_cage",
            "sire",
            "dam",
            "source_breeding",
            "possible_dams",
            "project",
            "ear_tag",
            "toe_tag",
            "origin",
            "coat_color",
            "notes",
        ]
        widgets = {
            "birth_date": forms.DateInput(attrs={"type": "date"}),
            "death_date": forms.DateInput(attrs={"type": "date"}),
            "euthanasia_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        admin_correction_unlocked = kwargs.pop("admin_correction_unlocked", False)
        super().__init__(*args, **kwargs)
        active_strains = self.fields["strain_line"].queryset.filter(is_active=True)
        if self.instance and self.instance.pk and self.instance.strain_line_id:
            active_strains = (active_strains | StrainLine.objects.filter(pk=self.instance.strain_line_id)).distinct()
        self.fields["strain_line"].queryset = active_strains.order_by("line_name")
        selected_cage_id = _coerce_positive_int(
            self.data.get("current_cage") if self.is_bound else self.initial.get("current_cage")
        )
        active_cage_qs = editable_active_cage_queryset(self.user) if self.user is not None else Cage.objects.filter(status=Cage.Status.ACTIVE)
        self.active_cage_queryset = active_cage_qs.order_by("cage_id")
        current_cage_ids: list[int] = []
        if selected_cage_id:
            current_cage_ids.extend(
                self.active_cage_queryset.filter(pk=selected_cage_id).values_list("pk", flat=True)
            )
        if self.instance and self.instance.pk and self.instance.current_cage_id:
            current_cage_ids.append(self.instance.current_cage_id)
        self.fields["current_cage"].queryset = Cage.objects.filter(
            pk__in=list(dict.fromkeys(current_cage_ids))
        ).order_by("cage_id")
        self.fields["current_cage"].required = False
        if self.instance.pk and self.instance.current_cage_id:
            self.fields["current_cage"].initial = self.instance.current_cage_id
            self.fields["current_cage_lookup"].initial = self.instance.current_cage.cage_id
        if self.instance.pk:
            self.fields["current_cage"].disabled = True
            self.fields["current_cage_lookup"].disabled = True
            self.fields["current_cage"].help_text = "Use Move Cage to change cage assignment and preserve movement history."
        self.fields["sire"].queryset = _selected_mouse_queryset(
            self.data.get("sire") if self.is_bound else self.initial.get("sire"),
            self.instance.sire_id if self.instance and self.instance.pk else None,
        )
        self.fields["dam"].queryset = _selected_mouse_queryset(
            self.data.get("dam") if self.is_bound else self.initial.get("dam"),
            self.instance.dam_id if self.instance and self.instance.pk else None,
        )
        _configure_mouse_parentage_fields(self, instance=self.instance)
        self.fields["project"].queryset = self.fields["project"].queryset.order_by("name")
        self.fields["project"].required = True
        if self.instance.pk:
            self.fields["status"].widget.attrs["data-initial-status"] = self.instance.status
        self.fields["mouse_uid"].help_text = (
            "Must be unique across the entire system. Dead, culled, or archived mouse UIDs cannot be reused."
        )
        self._configure_admin_correction(
            user=self.user,
            admin_correction_unlocked=admin_correction_unlocked,
        )

    def clean_mouse_uid(self):
        mouse_uid = normalize_identifier(self.cleaned_data.get("mouse_uid"))
        if not mouse_uid:
            return mouse_uid
        exclude_pk = self.instance.pk if self.instance and self.instance.pk else None
        validate_mouse_uid_available(mouse_uid, exclude_pk=exclude_pk)
        return mouse_uid

    def clean(self):
        cleaned_data = super().clean()
        if self.instance and self.instance.pk:
            previous_sex = self.instance.sex
            target_sex = cleaned_data.get("sex")
            if target_sex != previous_sex:
                self.add_error(
                    "sex",
                    "Use Correct Sex so cage occupancy, breeding roles, pedigree, and sex-linked genotype checks run together.",
                )
            previous_status = self.instance.status
            target_status = cleaned_data.get("status")
            if target_status != previous_status and (
                previous_status in TERMINAL_MOUSE_STATUSES
                or target_status in TERMINAL_MOUSE_STATUSES
            ):
                if previous_status in TERMINAL_MOUSE_STATUSES:
                    message = (
                        "Use Restore Mouse to reactivate a terminal mouse so cage occupancy "
                        "and history stay consistent."
                    )
                else:
                    message = (
                        "Use End Mouse, or End Breeding for breeders, to mark a mouse as terminal. "
                        "That workflow updates cage occupancy, memberships, and linked breedings together."
                    )
                self.add_error("status", message)
            cleaned_data["current_cage"] = self.instance.current_cage
            _clean_mouse_parentage(self, cleaned_data)
            return cleaned_data

        lookup = (cleaned_data.get("current_cage_lookup") or "").strip()
        if lookup:
            resolved, err = resolve_cage_from_lookup(lookup, queryset=self.active_cage_queryset)
            if err:
                self.add_error("current_cage_lookup", err)
            elif resolved is not None:
                cleaned_data["current_cage"] = resolved

        current_cage = cleaned_data.get("current_cage")
        if cleaned_data.get("status") == Mouse.Status.ACTIVE and current_cage is None:
            self.add_error("current_cage_lookup", "Active mice must be assigned to a current cage.")
        if current_cage is not None and cleaned_data.get("status") == Mouse.Status.ACTIVE:
            try:
                validate_active_sex_compatible_with_cage(current_cage, [cleaned_data.get("sex")])
            except ValidationError as exc:
                self.add_error("current_cage", exc)

        _clean_mouse_parentage(self, cleaned_data)
        return cleaned_data


MOUSE_BATCH_MAX_ROWS = 50


class MouseBatchSharedForm(forms.Form):
    """Shared litter/colony fields when creating multiple mice at once."""

    parentage_mode = forms.ChoiceField(
        choices=MOUSE_PARENTAGE_CHOICES,
        required=False,
        label="Parentage",
        initial=MouseParentageMode.NONE,
    )
    birth_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "filter-control", "placeholder": "YYYY-MM-DD"}),
    )
    death_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "filter-control", "placeholder": "YYYY-MM-DD"}),
    )
    euthanasia_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "filter-control", "placeholder": "YYYY-MM-DD"}),
    )
    death_reason = forms.CharField(
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={"class": "filter-control", "placeholder": "Optional: found dead / scheduled endpoint"}),
    )
    status = forms.ChoiceField(choices=Mouse.Status.choices, initial=Mouse.Status.ACTIVE)
    strain_line = forms.ModelChoiceField(queryset=StrainLine.objects.none(), required=True)
    current_cage = forms.ModelChoiceField(queryset=Cage.objects.none(), required=False, label="Current cage")
    current_cage_lookup = forms.CharField(
        max_length=64,
        required=False,
        label="Or enter cage ID",
        help_text="Partial cage ID is supported. Must match an existing cage.",
        widget=forms.TextInput(attrs={"class": "filter-control", "placeholder": "e.g. HGS_C110"}),
    )
    sire = forms.ModelChoiceField(queryset=Mouse.objects.none(), required=False, label="Sire (Father)")
    dam = forms.ModelChoiceField(queryset=Mouse.objects.none(), required=False, label="Dam (Mother)")
    source_breeding = forms.ModelChoiceField(
        queryset=_breeding_model().objects.none(),
        required=False,
        label="Breeding cage",
        help_text="Selecting a breeding cage uses its sire and all dams.",
    )
    possible_dams = forms.ModelMultipleChoiceField(
        queryset=Mouse.objects.none(),
        required=False,
        label="Possible dam(s)",
        widget=forms.SelectMultiple(attrs={"size": 6}),
    )
    project = forms.ModelChoiceField(queryset=Project.objects.none(), required=True)
    origin = forms.CharField(
        required=False,
        max_length=128,
        widget=forms.TextInput(attrs={"class": "filter-control", "placeholder": "Optional: weaned from BR-20260618-001"}),
    )
    coat_color = forms.CharField(
        required=False,
        max_length=64,
        widget=forms.TextInput(attrs={"class": "filter-control", "placeholder": "Optional: black / albino / agouti"}),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Optional: health, source, or handling notes."}),
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["strain_line"].queryset = StrainLine.objects.filter(is_active=True).order_by("line_name")
        self.active_cage_queryset = (
            editable_active_cage_queryset(user) if user is not None else Cage.objects.filter(status=Cage.Status.ACTIVE)
        ).order_by("cage_id")
        selected_cage_id = _coerce_positive_int(
            self.data.get("current_cage") if self.is_bound else self.initial.get("current_cage")
        )
        self.fields["current_cage"].queryset = (
            self.active_cage_queryset.filter(pk=selected_cage_id)
            if selected_cage_id
            else Cage.objects.none()
        )
        self.fields["sire"].queryset = _selected_mouse_queryset(
            self.data.get("sire") if self.is_bound else self.initial.get("sire")
        )
        self.fields["dam"].queryset = _selected_mouse_queryset(
            self.data.get("dam") if self.is_bound else self.initial.get("dam")
        )
        _configure_mouse_parentage_fields(self)
        self.fields["project"].queryset = Project.objects.filter(is_active=True).order_by("name")

    def clean(self):
        cleaned_data = super().clean()
        lookup = (cleaned_data.get("current_cage_lookup") or "").strip()
        if lookup:
            resolved, err = resolve_cage_from_lookup(lookup, queryset=self.active_cage_queryset)
            if err:
                self.add_error("current_cage_lookup", err)
            elif resolved is not None:
                cleaned_data["current_cage"] = resolved
        if cleaned_data.get("status") == Mouse.Status.ACTIVE and cleaned_data.get("current_cage") is None:
            self.add_error("current_cage_lookup", "Active mice must be assigned to a current cage.")
        _clean_mouse_parentage(self, cleaned_data)
        return cleaned_data


class MouseBatchEntryForm(forms.Form):
    mouse_uid = forms.CharField(
        max_length=64,
        widget=forms.TextInput(attrs={"class": "filter-control", "placeholder": "e.g. H_M425"}),
    )
    sex = forms.ChoiceField(choices=Mouse.Sex.choices, widget=forms.Select(attrs={"class": "filter-control"}))
    ear_tag = forms.CharField(
        required=False,
        max_length=64,
        widget=forms.TextInput(attrs={"class": "filter-control", "placeholder": "e.g. 25"}),
    )
    toe_tag = forms.CharField(
        required=False,
        max_length=64,
        widget=forms.TextInput(attrs={"class": "filter-control", "placeholder": "e.g. L3"}),
    )

    def clean_mouse_uid(self):
        mouse_uid = normalize_identifier(self.cleaned_data.get("mouse_uid"))
        if not mouse_uid:
            raise ValidationError("Mouse UID is required.")
        validate_mouse_uid_available(mouse_uid)
        return mouse_uid


class MoveCageForm(forms.Form):
    destination_cage = forms.ModelChoiceField(
        queryset=Cage.objects.none(),
        label="Destination Cage",
    )
    move_date = forms.DateField(
        label="Move Date",
        widget=forms.DateInput(attrs={"type": "date"}),
        initial=timezone.localdate,
    )
    reason = forms.CharField(max_length=128, required=False)
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}), required=False)

    def __init__(self, *args, mouse: Mouse, user=None, **kwargs):
        self.mouse = mouse
        self.user = user
        super().__init__(*args, **kwargs)
        active_cages = editable_active_cage_queryset(user) if user is not None else Cage.objects.filter(status=Cage.Status.ACTIVE)
        selected_id = _coerce_positive_int(
            self.data.get("destination_cage") if self.is_bound else self.initial.get("destination_cage")
        )
        if selected_id:
            self.fields["destination_cage"].queryset = active_cages.filter(pk=selected_id).order_by("cage_id")
        else:
            self.fields["destination_cage"].queryset = Cage.objects.none()
        self.fields["destination_cage"].widget.attrs.update({"class": "filter-control"})

    def clean_destination_cage(self):
        destination_cage = self.cleaned_data["destination_cage"]
        if self.mouse.current_cage_id and destination_cage.id == self.mouse.current_cage_id:
            raise forms.ValidationError("Destination cage cannot be the same as current cage.")
        from breeding.consistency import active_breedings_for_mouse

        active_breedings = list(active_breedings_for_mouse(self.mouse))
        off_target = [
            breeding
            for breeding in active_breedings
            if breeding.cage_id and breeding.cage_id != destination_cage.id
        ]
        if off_target:
            codes = ", ".join(breeding.breeding_code for breeding in off_target)
            cages = ", ".join(
                breeding.cage.cage_id
                for breeding in off_target
                if breeding.cage_id and breeding.cage is not None
            )
            raise forms.ValidationError(
                f"{self.mouse.mouse_uid} is in active breeding(s) {codes}. "
                f"Move it only to the breeding cage ({cages}), or end the breeding first."
            )
        try:
            validate_active_breeding_cage_entry(destination_cage, [self.mouse])
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages) from exc
        validate_active_sex_compatible_with_cage(
            destination_cage,
            [self.mouse.sex] if self.mouse.status == Mouse.Status.ACTIVE else [],
            exclude_mouse_ids=[self.mouse.pk],
        )
        return destination_cage


class MouseRestoreForm(forms.Form):
    destination_cage = forms.ModelChoiceField(
        queryset=Cage.objects.none(),
        label="Restore to Cage",
    )
    restore_date = forms.DateField(
        label="Restore Date",
        widget=forms.DateInput(attrs={"type": "date"}),
        initial=timezone.localdate,
    )
    reason = forms.ChoiceField(
        label="Reason",
        choices=MOUSE_RESTORE_REASON_CHOICES,
        initial="Mistaken endpoint / euthanasia entry",
        widget=forms.Select(attrs={"class": "filter-control"}),
    )
    confirm = forms.BooleanField(
        label="I confirm this mouse is alive and should be restored to active cage occupancy.",
    )

    def __init__(self, *args, mouse: Mouse, user=None, **kwargs):
        self.mouse = mouse
        self.user = user
        super().__init__(*args, **kwargs)
        selected_id = _coerce_positive_int(
            self.data.get("destination_cage") if self.is_bound else self.initial.get("destination_cage")
        )
        qs = Cage.objects.none()
        if selected_id:
            active_or_closed = (
                editable_cage_queryset(user, statuses=[Cage.Status.ACTIVE, Cage.Status.CLOSED])
                if user is not None
                else Cage.objects.filter(status__in=[Cage.Status.ACTIVE, Cage.Status.CLOSED])
            )
            qs = active_or_closed.filter(pk=selected_id)
        self.fields["destination_cage"].queryset = qs.order_by("cage_id")
        self.fields["destination_cage"].widget.attrs.update({"class": "filter-control"})

    def clean_restore_date(self):
        restore_date = self.cleaned_data["restore_date"]
        if restore_date > timezone.localdate():
            raise forms.ValidationError("Restore date cannot be in the future.")
        birth_date = self.mouse.birth_date
        if birth_date and restore_date < birth_date:
            raise forms.ValidationError("Restore date cannot be earlier than birth date.")
        return restore_date

    def clean_destination_cage(self):
        destination_cage = self.cleaned_data["destination_cage"]
        try:
            validate_active_sex_compatible_with_cage(
                destination_cage,
                [self.mouse.sex],
                exclude_mouse_ids=[self.mouse.pk],
            )
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages) from exc
        return destination_cage


class MouseSexCorrectionForm(forms.Form):
    sex = forms.ChoiceField(
        label="Correct Sex",
        choices=Mouse.Sex.choices,
        widget=forms.Select(attrs={"class": "filter-control"}),
    )
    reason = forms.ChoiceField(
        label="Reason",
        choices=MOUSE_SEX_CORRECTION_REASON_CHOICES,
        widget=forms.Select(attrs={"class": "filter-control"}),
    )
    confirm = forms.BooleanField(
        label="I confirm this is a data correction and the listed impact has been reviewed.",
    )

    def __init__(self, *args, mouse: Mouse, **kwargs):
        self.mouse = mouse
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.initial.setdefault("sex", mouse.sex)

    def clean_sex(self):
        sex = self.cleaned_data["sex"]
        if sex == self.mouse.sex:
            raise forms.ValidationError("Select a different sex to save a correction.")
        return sex

    def clean(self):
        cleaned = super().clean()
        target_sex = cleaned.get("sex")
        if target_sex:
            conflicts = mouse_sex_correction_conflicts(self.mouse, target_sex)
            for conflict in conflicts:
                self.add_error(None, conflict)
        return cleaned


class MouseEndForm(forms.Form):
    terminal_status = forms.ChoiceField(
        label="Final Status",
        choices=[
            (Mouse.Status.EUTHANIZED, Mouse.Status.EUTHANIZED.label),
            (Mouse.Status.DEAD, Mouse.Status.DEAD.label),
            (Mouse.Status.CULLED, Mouse.Status.CULLED.label),
        ],
        initial=Mouse.Status.EUTHANIZED,
        widget=forms.Select(attrs={"class": "filter-control"}),
    )
    end_date = forms.DateField(
        label="End Date",
        widget=forms.DateInput(attrs={"type": "date"}),
        initial=timezone.localdate,
    )
    reason = forms.ChoiceField(
        label="Reason",
        choices=[
            ("Scheduled endpoint", "Scheduled endpoint"),
            ("Experimental endpoint", "Experimental endpoint"),
            ("Health/welfare endpoint", "Health/welfare endpoint"),
            ("Found dead", "Found dead"),
            ("Breeding colony management", "Breeding colony management"),
            ("Other", "Other"),
        ],
        initial="Scheduled endpoint",
        widget=forms.Select(attrs={"class": "filter-control"}),
    )
    confirm = forms.BooleanField(
        label="I confirm this mouse should be removed from current cage occupancy.",
    )

    def __init__(self, *args, mouse: Mouse, **kwargs):
        self.mouse = mouse
        super().__init__(*args, **kwargs)

    def clean_end_date(self):
        end_date = self.cleaned_data["end_date"]
        if end_date > timezone.localdate():
            raise forms.ValidationError("End date cannot be in the future.")
        birth_date = self.mouse.birth_date
        if birth_date and end_date < birth_date:
            raise forms.ValidationError("End date cannot be earlier than birth date.")
        return end_date

    def clean(self):
        cleaned = super().clean()
        if self.mouse.status in {
            Mouse.Status.EUTHANIZED,
            Mouse.Status.DEAD,
            Mouse.Status.CULLED,
            Mouse.Status.TRANSFERRED,
            Mouse.Status.ARCHIVED,
        }:
            self.add_error(None, "This mouse is already in a terminal or archived status.")
        return cleaned


class BulkMouseExperimentForm(forms.Form):
    note = forms.CharField(
        label="Experiment note",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Optional experiment note"}),
    )
    confirm = forms.BooleanField(label="I confirm these mice should be marked as in experiment.")


class BulkMouseClearExperimentForm(forms.Form):
    confirm = forms.BooleanField(label="I confirm these mice should be cleared from active experiment status.")


class BulkMouseMoveCageForm(forms.Form):
    destination_cage = forms.ModelChoiceField(
        queryset=Cage.objects.none(),
        label="Destination Cage",
        widget=forms.Select(attrs={"class": "filter-control"}),
    )
    move_date = forms.DateField(
        label="Move Date",
        widget=forms.DateInput(attrs={"type": "date"}),
        initial=timezone.localdate,
    )
    reason = forms.CharField(
        max_length=128,
        required=False,
        initial="Bulk move",
        widget=forms.TextInput(attrs={"class": "filter-control"}),
    )
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)
    confirm = forms.BooleanField(label="I confirm these mice should be moved to the selected cage.")

    def __init__(self, *args, mice: list[Mouse], user=None, **kwargs):
        self.mice = mice
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["destination_cage"].queryset = (
            editable_active_cage_queryset(user) if user is not None else Cage.objects.filter(status=Cage.Status.ACTIVE)
        ).order_by("cage_id")

    def clean_move_date(self):
        move_date = self.cleaned_data["move_date"]
        if move_date > timezone.localdate():
            raise forms.ValidationError("Move date cannot be in the future.")
        earliest_birth = min((m.birth_date for m in self.mice if m.birth_date), default=None)
        if earliest_birth and move_date < earliest_birth:
            raise forms.ValidationError("Move date cannot be earlier than selected mouse birth dates.")
        return move_date

    def clean_destination_cage(self):
        destination_cage = self.cleaned_data["destination_cage"]
        if self.mice and all(m.current_cage_id == destination_cage.pk for m in self.mice):
            raise forms.ValidationError("All selected mice are already in this cage.")
        from breeding.consistency import active_breedings_for_mouse

        active_conflicts: list[str] = []
        for mouse in self.mice:
            off_target = [
                breeding
                for breeding in active_breedings_for_mouse(mouse)
                if not breeding.cage_id or breeding.cage_id != destination_cage.pk
            ]
            if off_target:
                codes = ", ".join(breeding.breeding_code for breeding in off_target)
                cages = ", ".join(
                    breeding.cage.cage_id
                    for breeding in off_target
                    if breeding.cage_id and breeding.cage is not None
                )
                target = f" ({cages})" if cages else ""
                active_conflicts.append(f"{mouse.mouse_uid}: {codes}{target}")
        if active_conflicts:
            raise forms.ValidationError(
                "Selected mice include active breeders. Move them only to their breeding cage, "
                "or end the breeding first: "
                + "; ".join(active_conflicts)
            )
        try:
            validate_active_breeding_cage_entry(destination_cage, self.mice)
            validate_active_sex_compatible_with_cage(
                destination_cage,
                [m.sex for m in self.mice if m.status == Mouse.Status.ACTIVE],
                exclude_mouse_ids=[m.pk for m in self.mice],
            )
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages) from exc
        return destination_cage


class BulkMouseEndForm(forms.Form):
    terminal_status = forms.ChoiceField(
        label="Final Status",
        choices=[
            (Mouse.Status.EUTHANIZED, Mouse.Status.EUTHANIZED.label),
            (Mouse.Status.DEAD, Mouse.Status.DEAD.label),
            (Mouse.Status.CULLED, Mouse.Status.CULLED.label),
        ],
        initial=Mouse.Status.EUTHANIZED,
        widget=forms.Select(attrs={"class": "filter-control"}),
    )
    end_date = forms.DateField(
        label="End Date",
        widget=forms.DateInput(attrs={"type": "date"}),
        initial=timezone.localdate,
    )
    reason = forms.ChoiceField(
        label="Reason",
        choices=[
            ("Scheduled endpoint", "Scheduled endpoint"),
            ("Experimental endpoint", "Experimental endpoint"),
            ("Health/welfare endpoint", "Health/welfare endpoint"),
            ("Found dead", "Found dead"),
            ("Breeding colony management", "Breeding colony management"),
            ("Other", "Other"),
        ],
        initial="Scheduled endpoint",
        widget=forms.Select(attrs={"class": "filter-control"}),
    )
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)
    confirm = forms.BooleanField(label="I confirm these mice should be ended and removed from cage occupancy.")

    def __init__(self, *args, mice: list[Mouse], **kwargs):
        self.mice = mice
        super().__init__(*args, **kwargs)

    def clean_end_date(self):
        end_date = self.cleaned_data["end_date"]
        if end_date > timezone.localdate():
            raise forms.ValidationError("End date cannot be in the future.")
        for mouse in self.mice:
            if mouse.birth_date and end_date < mouse.birth_date:
                raise forms.ValidationError(f"End date cannot be earlier than {mouse.mouse_uid}'s birth date.")
        return end_date


class CageImportForm(forms.Form):
    data_file = forms.FileField(
        label="CSV or XLSX file",
        help_text="Required. Use the provided template to avoid schema errors.",
    )
    apply_import_prefix = forms.BooleanField(
        required=False,
        initial=False,
        label="Prefix cage IDs with my import prefix",
        help_text="Prepends your profile prefix to each cage_id (e.g. C001 → JG-C001).",
    )
    update_existing = forms.BooleanField(
        required=False,
        initial=True,
        label="Update existing cages when cage_id matches",
        help_text="When checked, rows whose cage_id already exists update that cage instead of failing validation. You will be asked to confirm before any overwrite runs.",
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_data_file(self):
        return validate_import_file_upload(self.cleaned_data["data_file"])

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("apply_import_prefix") and self.user is not None:
            if not get_effective_import_prefix(self.user):
                raise ValidationError(
                    format_html(
                        'Set your import ID prefix in the <a href="#import-prefix">Import ID prefix</a> '
                        "section on this page first."
                    )
                )
        return cleaned


class MouseImportForm(forms.Form):
    data_file = forms.FileField(
        label="CSV or XLSX file",
        help_text="Required. Use template headers exactly; optional columns may be left blank.",
    )
    auto_create_missing_strain_lines = forms.BooleanField(
        required=False,
        initial=True,
        label="Auto-create missing strain lines",
        help_text="If enabled, unknown strain_line values will be created automatically.",
    )
    auto_create_missing_projects = forms.BooleanField(
        required=False,
        initial=True,
        label="Auto-create missing projects",
        help_text="If enabled, unknown project names will be created automatically.",
    )
    auto_create_missing_cages = forms.BooleanField(
        required=False,
        initial=True,
        label="Auto-create missing cages",
        help_text="If enabled, unknown current_cage values will create minimal valid cages.",
    )
    resolve_pedigree_within_file = forms.BooleanField(
        required=False,
        initial=True,
        label="Resolve sire/dam within this file",
        help_text="If enabled, sire/dam can reference mice that are also in the same import file.",
    )
    apply_import_prefix = forms.BooleanField(
        required=False,
        initial=False,
        label="Prefix mouse & cage IDs with my import prefix",
        help_text=(
            "Prepends your profile prefix to mouse_uid and to cage references when they are new. "
            "Existing cage/mouse IDs in the database are left unchanged so you can reference legacy records."
        ),
    )
    update_existing = forms.BooleanField(
        required=False,
        initial=True,
        label="Update existing mice when mouse_uid matches",
        help_text="When checked, rows whose mouse_uid already exists update that mouse instead of failing validation. You will be asked to confirm before any overwrite runs.",
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_data_file(self):
        return validate_import_file_upload(self.cleaned_data["data_file"])

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("apply_import_prefix") and self.user is not None:
            if not get_effective_import_prefix(self.user):
                raise ValidationError(
                    format_html(
                        'Set your import ID prefix in the <a href="#import-prefix">Import ID prefix</a> '
                        "section on this page first."
                    )
                )
        return cleaned


class StrainLineForm(AdminCorrectionFormMixin, forms.ModelForm):
    admin_correction_frozen_fields = {
        "name",
        "owner",
        "projects",
        "species",
        "source",
        "category",
        "category_custom",
        "background",
        "background_custom",
        "expected_loci_template",
        "expected_loci_config",
        "is_active",
        "notes",
    }

    expected_loci_config = forms.CharField(required=False, widget=forms.HiddenInput())
    category = forms.ChoiceField(
        choices=[],
        widget=forms.Select(attrs={"class": "filter-control", "id": "id_category"}),
        label="Category",
    )
    category_custom = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "filter-control strain-custom-field",
                "id": "id_category_custom",
                "placeholder": "Custom category",
            }
        ),
        label="Custom category",
    )
    background = forms.ChoiceField(
        choices=[],
        widget=forms.Select(attrs={"class": "filter-control", "id": "id_background"}),
        label="Background",
    )
    background_custom = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "filter-control strain-custom-field",
                "id": "id_background_custom",
                "placeholder": "Custom background",
            }
        ),
        label="Custom background",
    )

    class Meta:
        model = StrainLine
        fields = [
            "name",
            "owner",
            "projects",
            "species",
            "source",
            "category",
            "background",
            "expected_loci_template",
            "is_active",
            "notes",
        ]
        widgets = {
            "owner": forms.Select(attrs={"class": "filter-control"}),
            "projects": forms.SelectMultiple(attrs={"class": "filter-control strain-project-select"}),
            "species": forms.Select(attrs={"class": "filter-control"}),
            "source": forms.TextInput(attrs={"class": "filter-control"}),
            "expected_loci_template": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
        help_texts = {
            "name": "Breeding-line template name. Example: Lyz2-Cre x Tet2 flox x Gpr82 KO. Example: CA/TA/RA KI mice.",
            "owner": "Maintainer for the strain-line definition, PDFs, and genotype template. Animal ownership comes from projects.",
            "projects": (
                "Usually inferred from mice/colonies. Use this only to pre-link a project before mice exist."
            ),
            "species": "Species for this strain line record.",
            "source": "Optional source or vendor reference.",
            "expected_loci_template": (
                "Optional. One locus per row (or comma/semicolon separated), e.g. Lyz2-Cre, Tet2, Gpr82. "
                "Leave empty if this strain line has no standard genotype loci. "
                "When set, the template auto-populates loci on New Mouse / offspring workflows."
            ),
            "notes": "Optional husbandry/genetics notes or provenance.",
        }
        labels = {
            "name": "Strain line name",
            "owner": "Maintainer",
            "projects": "Assigned projects (optional)",
            "expected_loci_template": "Included loci",
        }

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise ValidationError("Strain line name is required.")
        conflict = StrainLine.objects.filter(line_name=name)
        if self.instance.pk:
            conflict = conflict.exclude(pk=self.instance.pk)
        if conflict.exists():
            raise ValidationError("A strain line with this name already exists.")
        return name

    def clean_expected_loci_template(self):
        return (self.cleaned_data.get("expected_loci_template") or "").strip()

    def __init__(self, *args, user=None, **kwargs):
        self._actor_user = user
        admin_correction_unlocked = kwargs.pop("admin_correction_unlocked", False)
        super().__init__(*args, **kwargs)
        self.fields["category"].choices = choice_field_with_custom(StrainLine.Category)
        self.fields["background"].choices = choice_field_with_custom(StrainLine.BackgroundPreset)
        if self.instance and self.instance.pk:
            cat_sel, cat_custom = preset_select_initial(self.instance.category, StrainLine.Category)
            if cat_sel:
                self.initial.setdefault("category", cat_sel)
            if cat_custom:
                self.initial.setdefault("category_custom", cat_custom)
            bg_sel, bg_custom = preset_select_initial(self.instance.background, StrainLine.BackgroundPreset)
            if bg_sel:
                self.initial.setdefault("background", bg_sel)
            elif not (self.instance.background or "").strip():
                self.initial.setdefault("background", StrainLine.BackgroundPreset.C57BL_6J)
            if bg_custom:
                self.initial.setdefault("background_custom", bg_custom)
        else:
            self.initial.setdefault("category", StrainLine.Category.COMPOUND_STRAIN)
            self.initial.setdefault("background", StrainLine.BackgroundPreset.C57BL_6J)
        user_model = get_user_model()
        if user is not None and getattr(user, "is_authenticated", False) and not can_manage_strain_lines(user):
            self.fields["owner"].queryset = user_model.objects.filter(pk=user.pk)
            self.fields["owner"].initial = user
            self.initial.setdefault("owner", user.pk)
            self.fields["owner"].disabled = True
        else:
            self.fields["owner"].queryset = user_model.objects.order_by("username")
        self.fields["owner"].required = False
        self.fields["owner"].label_from_instance = (
            lambda u: (format_project_owner_label(u) or u.get_username() or "").strip() or str(u.pk)
        )
        project_qs = _strain_line_project_queryset_for_user(user)
        if self.instance and self.instance.pk:
            project_qs = (project_qs | self.instance.projects.all()).distinct()
        self.fields["projects"].queryset = project_qs.order_by("name")
        self.fields["projects"].required = False
        self.fields["expected_loci_template"].required = False
        entries: list[dict[str, str]] = []
        if self.instance and self.instance.pk:
            entries = self.instance.editable_loci_entries()
        if entries and not self.initial.get("expected_loci_config"):
            self.initial["expected_loci_config"] = json.dumps(entries)
        if entries and not self.initial.get("expected_loci_template"):
            self.initial["expected_loci_template"] = "\n".join(item["locus_name"] for item in entries)
        if self.instance.pk and not self.instance.owner_id and getattr(self.instance, "created_by_id", None):
            self.initial.setdefault("owner", self.instance.created_by_id)
        if self.instance and self.instance.pk and not self.initial.get("name"):
            self.initial.setdefault(
                "name",
                (self.instance.name or self.instance.display_name or self.instance.line_name or "").strip(),
            )
        self._configure_admin_correction(
            user=user,
            admin_correction_unlocked=admin_correction_unlocked,
            correction_allowed=can_manage_strain_lines(user),
        )

    def clean(self):
        cleaned = super().clean()
        try:
            cleaned["category"] = resolve_choice_or_custom(
                cleaned.get("category") or "",
                cleaned.get("category_custom") or "",
                StrainLine.Category,
                field_label="Category",
            )
        except ValueError as exc:
            self.add_error("category", str(exc))
        try:
            cleaned["background"] = resolve_choice_or_custom(
                cleaned.get("background") or "",
                cleaned.get("background_custom") or "",
                StrainLine.BackgroundPreset,
                field_label="Background",
            )
        except ValueError as exc:
            self.add_error("background", str(exc))
        raw_cfg = (cleaned.get("expected_loci_config") or "").strip()
        parsed: list[dict[str, str]] = []
        if raw_cfg:
            try:
                data = json.loads(raw_cfg)
            except Exception as exc:
                raise ValidationError("Invalid loci config payload.") from exc
            if not isinstance(data, list):
                raise ValidationError("Invalid loci config payload.")
            seen: set[str] = set()
            for row in data:
                if not isinstance(row, dict):
                    continue
                locus = str(row.get("locus_name", "")).strip()
                locus_type = str(row.get("locus_type", "")).strip()
                chromosome_type = str(row.get("chromosome_type", "")).strip()
                if not locus:
                    continue
                if locus_type == "x_linked":
                    # Backward-compatible upgrade from old schema.
                    locus_type = StrainLine.LocusType.OTHER_CUSTOM
                    chromosome_type = StrainLine.ChromosomeType.X_LINKED
                locus_type = StrainLine.normalize_locus_type(
                    locus_type,
                    locus_name=locus,
                    line_name=cleaned.get("name") or getattr(self.instance, "line_name", ""),
                )
                if chromosome_type not in StrainLine.ChromosomeType.values:
                    chromosome_type = StrainLine.ChromosomeType.AUTOSOMAL
                locus_key = StrainLine.normalize_locus_name(locus).casefold()
                if locus_key in seen:
                    continue
                seen.add(locus_key)
                parsed.append(
                    {
                        "locus_name": locus,
                        "locus_type": locus_type,
                        "chromosome_type": chromosome_type,
                    }
                )

        if not parsed:
            # Backward-compatible fallback from plain text.
            text = (cleaned.get("expected_loci_template") or "").strip()
            tokens = [part.strip() for part in text.replace(";", "\n").replace(",", "\n").splitlines()]
            seen_fallback: set[str] = set()
            for token in tokens:
                normalized = token.strip()
                if not normalized:
                    continue
                if normalized in seen_fallback:
                    continue
                seen_fallback.add(normalized)
                parsed.append(
                    {
                        "locus_name": normalized,
                        "locus_type": StrainLine.LocusType.OTHER_CUSTOM,
                        "chromosome_type": StrainLine.ChromosomeType.AUTOSOMAL,
                    }
                )

        cleaned["expected_loci_template"] = "\n".join(item["locus_name"] for item in parsed)
        cleaned["_expected_loci_config_list"] = parsed
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.category = self.cleaned_data.get("category") or StrainLine.Category.COMPOUND_STRAIN
        obj.background = self.cleaned_data.get("background") or StrainLine.BackgroundPreset.C57BL_6J
        new_name = (self.cleaned_data.get("name") or "").strip()
        if new_name:
            obj.name = new_name
        obj.expected_loci_config = list(self.cleaned_data.get("_expected_loci_config_list") or [])
        obj.expected_loci_template = (self.cleaned_data.get("expected_loci_template") or "").strip()
        if (
            not self.instance.pk
            and not obj.owner_id
            and self._actor_user
            and getattr(self._actor_user, "is_authenticated", False)
        ):
            obj.owner = self._actor_user
        if commit:
            obj.save()
            self.save_m2m()
            used_project_ids = [
                project_id
                for project_id in Mouse.objects.filter(strain_line=obj)
                .exclude(project_id__isnull=True)
                .values_list("project_id", flat=True)
                .distinct()
            ]
            if used_project_ids:
                obj.projects.add(*used_project_ids)
            if not obj.owner_id and getattr(obj, "created_by_id", None):
                StrainLine.objects.filter(pk=obj.pk).update(owner_id=obj.created_by_id)
                obj.owner_id = obj.created_by_id
        return obj


class MouseGenotypeComponentForm(forms.ModelForm):
    class Meta:
        model = MouseGenotypeComponent
        fields = [
            "strain_line",
            "locus_name",
            "chromosome_type",
            "zygosity",
            "zygosity_class",
            "allele_display_1",
            "allele_display_2",
            "sort_order",
            "notes",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 2}),
        }
        help_texts = {
            "locus_name": "Optional precise locus label, e.g. Lyz2-Cre, Tet2, Foxp3-Cre.",
            "chromosome_type": "Choose autosomal/X/Y to enable sex-aware validation.",
            "zygosity": "Display string, e.g. +/-, fl/fl, Cre/Y, -/Y.",
            "zygosity_class": "Optional normalized class for downstream filtering.",
            "allele_display_1": "Optional allele text (e.g. +, -, fl, Cre, KO).",
            "allele_display_2": "Optional allele text (e.g. +, -, fl, Y). Use Y for X-linked male records.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["strain_line"].queryset = StrainLine.objects.filter(is_active=True).order_by("name", "line_name")
        # Locus template is controlled by strain-line templates and parent-line union.
        # Keep these immutable in Edit Genotype Components to prevent ad-hoc custom loci.
        if self.instance and self.instance.pk:
            self.fields["locus_name"].disabled = True
            self.fields["strain_line"].disabled = True

    def clean(self):
        cleaned = super().clean()
        allele_1 = (cleaned.get("allele_display_1") or "").strip()
        allele_2 = (cleaned.get("allele_display_2") or "").strip()
        zygosity = (cleaned.get("zygosity") or "").strip()
        if zygosity == "-":
            zygosity = ""
            cleaned["zygosity"] = ""
        if allele_1 == "-":
            allele_1 = ""
            cleaned["allele_display_1"] = ""
        if allele_2 == "-":
            allele_2 = ""
            cleaned["allele_display_2"] = ""
        chromosome_type = cleaned.get("chromosome_type")
        mouse_sex = getattr(getattr(self.instance, "mouse", None), "sex", None)

        if chromosome_type == MouseGenotypeComponent.ChromosomeType.AUTOSOMAL:
            if (allele_1 and not allele_2) or (allele_2 and not allele_1):
                raise ValidationError("Autosomal loci require both alleles (or leave both blank).")
        elif chromosome_type == MouseGenotypeComponent.ChromosomeType.X_LINKED and mouse_sex == Mouse.Sex.MALE:
            if allele_1 and not allele_2:
                allele_2 = "Y"
                cleaned["allele_display_2"] = "Y"
            elif allele_2 and not allele_1:
                raise ValidationError("For X-linked male records, allele_1 is required.")
            elif allele_2 and allele_2.upper() != "Y":
                raise ValidationError("For X-linked male records, allele_2 should be 'Y'.")
        elif chromosome_type == MouseGenotypeComponent.ChromosomeType.X_LINKED and mouse_sex == Mouse.Sex.FEMALE:
            if allele_2.upper() == "Y":
                raise ValidationError("Female X-linked records cannot use Y as allele_2.")
            if (allele_1 and not allele_2) or (allele_2 and not allele_1):
                raise ValidationError("For X-linked female records, provide both alleles.")
        elif chromosome_type == MouseGenotypeComponent.ChromosomeType.Y_LINKED:
            if mouse_sex == Mouse.Sex.FEMALE:
                raise ValidationError("Female mice cannot carry Y-linked loci.")
            if allele_1 and not allele_2:
                allele_2 = "Y"
                cleaned["allele_display_2"] = "Y"
            elif allele_2 and not allele_1:
                raise ValidationError("For Y-linked records, allele_1 is required.")
            elif allele_2 and allele_2.upper() != "Y":
                raise ValidationError("For Y-linked records, allele_2 should be 'Y'.")
        else:
            if (allele_1 and not allele_2) or (allele_2 and not allele_1):
                raise ValidationError("Please fill both allele fields, or leave both blank.")

        # Keep zygosity synchronized with explicit allele display when user filled both alleles.
        if allele_1 and allele_2:
            cleaned["zygosity"] = f"{allele_1}/{allele_2}"
        elif zygosity:
            parts = [p.strip() for p in zygosity.split("/", 1)]
            if len(parts) == 2 and parts[0] and parts[1]:
                cleaned["allele_display_1"] = parts[0]
                cleaned["allele_display_2"] = parts[1]

        return cleaned


class MouseGenotypeComponentInlineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        # Disallow creation of ad-hoc new component rows from this screen.
        for form in self.forms:
            if not hasattr(form, "cleaned_data"):
                continue
            if form.cleaned_data.get("DELETE"):
                continue
            if form.instance.pk:
                continue
            if form.has_changed():
                raise ValidationError(
                    "You cannot add custom loci here. Edit strain-line templates/parent templates instead."
                )


MouseGenotypeComponentFormSet = inlineformset_factory(
    Mouse,
    MouseGenotypeComponent,
    form=MouseGenotypeComponentForm,
    formset=MouseGenotypeComponentInlineFormSet,
    extra=0,
    can_delete=True,
)
