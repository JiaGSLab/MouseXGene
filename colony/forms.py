import json

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils import timezone
from django.utils.safestring import mark_safe

from users.import_prefix import get_effective_import_prefix

from .models import Cage, Mouse, MouseGenotypeComponent, StrainLine


class CageForm(forms.ModelForm):
    class Meta:
        model = Cage
        fields = [
            "cage_id",
            "created_date",
            "room",
            "rack",
            "position",
            "cage_type",
            "purpose",
            "status",
            "notes",
        ]
        widgets = {
            "created_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }


class MouseForm(forms.ModelForm):
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
        kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        active_strains = self.fields["strain_line"].queryset.filter(is_active=True)
        if self.instance and self.instance.pk and self.instance.strain_line_id:
            active_strains = (active_strains | StrainLine.objects.filter(pk=self.instance.strain_line_id)).distinct()
        self.fields["strain_line"].queryset = active_strains.order_by("line_name")
        self.fields["current_cage"].queryset = self.fields["current_cage"].queryset.order_by("cage_id")
        self.fields["sire"].queryset = self.fields["sire"].queryset.order_by("mouse_uid")
        self.fields["dam"].queryset = self.fields["dam"].queryset.order_by("mouse_uid")
        self.fields["sire"].label = "Sire (Father)"
        self.fields["dam"].label = "Dam (Mother)"
        self.fields["project"].queryset = self.fields["project"].queryset.order_by("name")
        self.fields["project"].required = True


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
        self.fields["destination_cage"].queryset = Cage.objects.order_by("cage_id")

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

    class Meta:
        model = StrainLine
        fields = [
            "name",
            "owner",
            "expected_loci_template",
            "expected_loci_config",
            "is_active",
            "notes",
        ]
        widgets = {
            "expected_loci_template": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
        help_texts = {
            "name": "Breeding-line template name. Example: Lyz2-Cre x Tet2 flox x Gpr82 KO. Example: CA/TA/RA KI mice.",
            "owner": "Lab contact for this strain line (shown on the Strain Lines list). Defaults to you when creating.",
            "expected_loci_template": (
                "Required. One locus per row (or comma/semicolon separated), e.g. Lyz2-Cre, Tet2, Gpr82. "
                "This template is used to auto-populate loci on New Mouse / offspring workflows."
            ),
            "notes": "Optional husbandry/genetics notes or provenance.",
        }
        labels = {
            "name": "Strain line name",
            "owner": "Owner",
            "expected_loci_template": "Included loci",
        }

    def clean_expected_loci_template(self):
        return (self.cleaned_data.get("expected_loci_template") or "").strip()

    def __init__(self, *args, user=None, **kwargs):
        self._actor_user = user
        super().__init__(*args, **kwargs)
        self.fields["owner"].queryset = get_user_model().objects.order_by("username")
        self.fields["owner"].required = False
        entries: list[dict[str, str]] = []
        if self.instance and self.instance.pk:
            entries = self.instance.expected_loci_entries()
        if entries and not self.initial.get("expected_loci_config"):
            self.initial["expected_loci_config"] = json.dumps(entries)
        if entries and not self.initial.get("expected_loci_template"):
            self.initial["expected_loci_template"] = "\n".join(item["locus_name"] for item in entries)

    def clean(self):
        cleaned = super().clean()
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
                locus = StrainLine.normalize_locus_name(str(row.get("locus_name", "")).strip())
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
                key = locus.casefold()
                if key in seen:
                    continue
                seen.add(key)
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
                normalized = StrainLine.normalize_locus_name(token)
                if not normalized:
                    continue
                key = normalized.casefold()
                if key in seen_fallback:
                    continue
                seen_fallback.add(key)
                parsed.append(
                    {
                        "locus_name": normalized,
                        "locus_type": StrainLine.LocusType.CUSTOM,
                        "chromosome_type": StrainLine.ChromosomeType.AUTOSOMAL,
                    }
                )

        if not parsed:
            self.add_error("expected_loci_template", "Included loci is required.")
            return cleaned

        cleaned["expected_loci_template"] = "\n".join(item["locus_name"] for item in parsed)
        cleaned["expected_loci_config"] = json.dumps(parsed)
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=False)
        raw_cfg = (self.cleaned_data.get("expected_loci_config") or "").strip()
        if raw_cfg:
            try:
                obj.expected_loci_config = json.loads(raw_cfg)
            except Exception:
                obj.expected_loci_config = []
        if (
            not self.instance.pk
            and not obj.owner_id
            and self._actor_user
            and getattr(self._actor_user, "is_authenticated", False)
        ):
            obj.owner = self._actor_user
        if commit:
            obj.save()
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
