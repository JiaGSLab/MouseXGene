import json

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils import timezone
from django.utils.safestring import mark_safe

from users.import_prefix import get_effective_import_prefix
from breeding.forms import resolve_cage_from_lookup

from core.models import Project, format_project_owner_label

from .id_uniqueness import normalize_identifier, validate_cage_id_available, validate_mouse_uid_available
from .models import Cage, Mouse, MouseGenotypeComponent, StrainLine
from .strain_line_choices import (
    CUSTOM_SELECT_VALUE,
    choice_field_with_custom,
    preset_select_initial,
    resolve_choice_or_custom,
)


class CageForm(forms.ModelForm):
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

    class Meta:
        model = Cage
        fields = [
            "cage_id",
            "created_date",
            "rack",
            "position",
            "cage_type",
            "purpose",
            "status",
            "notes",
        ]
        widgets = {
            "created_date": forms.DateInput(attrs={"type": "date"}),
            "rack": forms.TextInput(attrs={"placeholder": "e.g. R1 or Rack-A"}),
            "position": forms.TextInput(attrs={"placeholder": "e.g. A1"}),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }
        help_texts = {
            "rack": "Use a consistent rack label, for example R1 or Rack-A.",
            "position": "Use a consistent position label, for example A1.",
        }

    def _room_choices(self) -> list[tuple[str, str]]:
        rooms = list(Cage.objects.exclude(room="").values_list("room", flat=True).distinct().order_by("room"))
        return [("", "— Select room —")] + [(room, room) for room in rooms] + [
            (CUSTOM_SELECT_VALUE, "Custom (type below)")
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["room"].choices = self._room_choices()
        known_rooms = {value for value, _label in self.fields["room"].choices if value and value != CUSTOM_SELECT_VALUE}
        stored = (self.instance.room or "").strip() if self.instance and self.instance.pk else ""
        if stored and stored not in known_rooms:
            self.initial.setdefault("room", CUSTOM_SELECT_VALUE)
            self.initial.setdefault("room_custom", stored)
        elif stored:
            self.initial.setdefault("room", stored)
        self.fields["cage_id"].help_text = (
            "Must be unique across the entire system. Retired or archived cage IDs cannot be reused."
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
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.room = (self.cleaned_data.get("room") or "").strip()
        if commit:
            obj.save()
        return obj


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


class MouseForm(forms.ModelForm):
    current_cage_lookup = forms.CharField(
        max_length=64,
        required=False,
        label="Or enter cage ID",
        help_text="Partial cage ID is supported. Must match an existing cage.",
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
        super().__init__(*args, **kwargs)
        active_strains = self.fields["strain_line"].queryset.filter(is_active=True)
        if self.instance and self.instance.pk and self.instance.strain_line_id:
            active_strains = (active_strains | StrainLine.objects.filter(pk=self.instance.strain_line_id)).distinct()
        self.fields["strain_line"].queryset = active_strains.order_by("line_name")
        selected_cage_id = _coerce_positive_int(
            self.data.get("current_cage") if self.is_bound else self.initial.get("current_cage")
        )
        current_cage_qs = Cage.objects.none()
        if selected_cage_id:
            current_cage_qs = current_cage_qs | Cage.objects.filter(
                pk=selected_cage_id,
                status=Cage.Status.ACTIVE,
            )
        if self.instance and self.instance.pk and self.instance.current_cage_id:
            current_cage_qs = current_cage_qs | Cage.objects.filter(pk=self.instance.current_cage_id)
        self.fields["current_cage"].queryset = current_cage_qs.distinct().order_by("cage_id")
        self.fields["current_cage"].required = False
        if self.instance.pk and self.instance.current_cage_id:
            self.fields["current_cage_lookup"].initial = self.instance.current_cage.cage_id
        self.fields["sire"].queryset = _selected_mouse_queryset(
            self.data.get("sire") if self.is_bound else self.initial.get("sire"),
            self.instance.sire_id if self.instance and self.instance.pk else None,
        )
        self.fields["dam"].queryset = _selected_mouse_queryset(
            self.data.get("dam") if self.is_bound else self.initial.get("dam"),
            self.instance.dam_id if self.instance and self.instance.pk else None,
        )
        self.fields["sire"].label = "Sire (Father)"
        self.fields["dam"].label = "Dam (Mother)"
        self.fields["project"].queryset = self.fields["project"].queryset.order_by("name")
        self.fields["project"].required = True
        if self.instance.pk:
            self.fields["status"].widget.attrs["data-initial-status"] = self.instance.status
        self.fields["mouse_uid"].help_text = (
            "Must be unique across the entire system. Dead, culled, or archived mouse UIDs cannot be reused."
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
        lookup = (cleaned_data.get("current_cage_lookup") or "").strip()
        if lookup:
            resolved, err = resolve_cage_from_lookup(lookup)
            if err:
                self.add_error("current_cage_lookup", err)
            elif resolved is not None:
                cleaned_data["current_cage"] = resolved

        return cleaned_data


MOUSE_BATCH_MAX_ROWS = 50


class MouseBatchSharedForm(forms.Form):
    """Shared litter/colony fields when creating multiple mice at once."""

    birth_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "filter-control"}),
    )
    death_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "filter-control"}),
    )
    euthanasia_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "filter-control"}),
    )
    death_reason = forms.CharField(required=False, max_length=255)
    status = forms.ChoiceField(choices=Mouse.Status.choices, initial=Mouse.Status.ACTIVE)
    strain_line = forms.ModelChoiceField(queryset=StrainLine.objects.none(), required=True)
    current_cage = forms.ModelChoiceField(queryset=Cage.objects.none(), required=False, label="Current cage")
    current_cage_lookup = forms.CharField(
        max_length=64,
        required=False,
        label="Or enter cage ID",
        help_text="Partial cage ID is supported. Must match an existing cage.",
    )
    sire = forms.ModelChoiceField(queryset=Mouse.objects.none(), required=False, label="Sire (Father)")
    dam = forms.ModelChoiceField(queryset=Mouse.objects.none(), required=False, label="Dam (Mother)")
    project = forms.ModelChoiceField(queryset=Project.objects.none(), required=True)
    origin = forms.CharField(required=False, max_length=128)
    coat_color = forms.CharField(required=False, max_length=64)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["strain_line"].queryset = StrainLine.objects.filter(is_active=True).order_by("line_name")
        self.fields["current_cage"].queryset = _selected_cage_queryset(
            self.data.get("current_cage") if self.is_bound else self.initial.get("current_cage")
        )
        self.fields["sire"].queryset = _selected_mouse_queryset(
            self.data.get("sire") if self.is_bound else self.initial.get("sire")
        )
        self.fields["dam"].queryset = _selected_mouse_queryset(
            self.data.get("dam") if self.is_bound else self.initial.get("dam")
        )
        self.fields["project"].queryset = Project.objects.filter(is_active=True).order_by("name")

    def clean(self):
        cleaned_data = super().clean()
        lookup = (cleaned_data.get("current_cage_lookup") or "").strip()
        if lookup:
            resolved, err = resolve_cage_from_lookup(lookup)
            if err:
                self.add_error("current_cage_lookup", err)
            elif resolved is not None:
                cleaned_data["current_cage"] = resolved
        return cleaned_data


class MouseBatchEntryForm(forms.Form):
    mouse_uid = forms.CharField(
        max_length=64,
        widget=forms.TextInput(attrs={"class": "filter-control", "placeholder": "Mouse UID"}),
    )
    sex = forms.ChoiceField(choices=Mouse.Sex.choices, widget=forms.Select(attrs={"class": "filter-control"}))
    ear_tag = forms.CharField(
        required=False,
        max_length=64,
        widget=forms.TextInput(attrs={"class": "filter-control", "placeholder": "Ear tag"}),
    )
    toe_tag = forms.CharField(
        required=False,
        max_length=64,
        widget=forms.TextInput(attrs={"class": "filter-control", "placeholder": "Toe tag"}),
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

    def __init__(self, *args, mouse: Mouse, **kwargs):
        self.mouse = mouse
        super().__init__(*args, **kwargs)
        selected_id = _coerce_positive_int(
            self.data.get("destination_cage") if self.is_bound else self.initial.get("destination_cage")
        )
        if selected_id:
            self.fields["destination_cage"].queryset = Cage.objects.filter(
                pk=selected_id,
                status=Cage.Status.ACTIVE,
            ).order_by("cage_id")
        else:
            self.fields["destination_cage"].queryset = Cage.objects.none()
        self.fields["destination_cage"].widget.attrs.update({"class": "filter-control"})

    def clean_destination_cage(self):
        destination_cage = self.cleaned_data["destination_cage"]
        if self.mouse.current_cage_id and destination_cage.id == self.mouse.current_cage_id:
            raise forms.ValidationError("Destination cage cannot be the same as current cage.")
        return destination_cage


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

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("apply_import_prefix") and self.user is not None:
            if not get_effective_import_prefix(self.user):
                raise ValidationError(
                    mark_safe(
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

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("apply_import_prefix") and self.user is not None:
            if not get_effective_import_prefix(self.user):
                raise ValidationError(
                    mark_safe(
                        'Set your import ID prefix in the <a href="#import-prefix">Import ID prefix</a> '
                        "section on this page first."
                    )
                )
        return cleaned


class StrainLineForm(forms.ModelForm):
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
            "projects": forms.SelectMultiple(attrs={"class": "filter-control", "size": 8}),
            "species": forms.Select(attrs={"class": "filter-control"}),
            "source": forms.TextInput(attrs={"class": "filter-control"}),
            "expected_loci_template": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
        help_texts = {
            "name": "Breeding-line template name. Example: Lyz2-Cre x Tet2 flox x Gpr82 KO. Example: CA/TA/RA KI mice.",
            "owner": "Lab contact (shown on Strain Lines list). Defaults to the creating user; you can change it here.",
            "projects": (
                "Projects this strain line belongs to or can be used in. New Mouse auto-selects the project "
                "only when exactly one project is linked."
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
            "owner": "Owner",
            "projects": "Projects",
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
        self.fields["owner"].queryset = get_user_model().objects.order_by("username")
        self.fields["owner"].required = False
        self.fields["owner"].label_from_instance = (
            lambda u: (format_project_owner_label(u) or u.get_username() or "").strip() or str(u.pk)
        )
        project_qs = Project.objects.filter(is_active=True)
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
                    locus_type = StrainLine.LocusType.CUSTOM
                    chromosome_type = StrainLine.ChromosomeType.X_LINKED
                if locus_type not in StrainLine.LocusType.values:
                    locus_type = StrainLine.LocusType.CUSTOM
                if chromosome_type not in StrainLine.ChromosomeType.values:
                    chromosome_type = StrainLine.ChromosomeType.AUTOSOMAL
                if locus in seen:
                    continue
                seen.add(locus)
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
                        "locus_type": StrainLine.LocusType.CUSTOM,
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
