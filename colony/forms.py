from django import forms
from django.core.exceptions import ValidationError
from django.forms import inlineformset_factory
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
    class Meta:
        model = StrainLine
        fields = [
            "name",
            "short_name",
            "category",
            "gene_or_locus",
            "line_name",
            "key_name",
            "display_name",
            "species",
            "background",
            "source",
            "is_active",
            "notes",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
        help_texts = {
            "name": "Full component name. Example: Tet2 flox, Lyz2-CreERT2, Rosa26-LSL-tdTomato.",
            "short_name": "Short display abbreviation. Example: Tet2fl, Lyz2-CreERT2, R26-LSL-tdT.",
            "category": "Component category (Cre, flox, KO, KI, reporter, transgene, etc.).",
            "gene_or_locus": "Optional gene or locus. Example: Tet2, Lyz2, Rosa26.",
            "line_name": "Legacy/internal identifier kept for backward compatibility.",
            "key_name": "Optional legacy key for imports and quick filters. Example: LGR5_CRE.",
            "display_name": "Optional UI display alias (legacy-compatible).",
            "notes": "Optional husbandry/genetics notes or provenance.",
        }


class MouseGenotypeComponentForm(forms.ModelForm):
    class Meta:
        model = MouseGenotypeComponent
        fields = [
            "strain_line",
            "zygosity",
            "allele_display_1",
            "allele_display_2",
            "sort_order",
            "notes",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 2}),
        }
        help_texts = {
            "zygosity": "Examples: +/-, -/-, fl/+, fl/fl, KI/+, Cre/+.",
            "allele_display_1": "Optional custom allele label for UI.",
            "allele_display_2": "Optional custom allele label for UI.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["strain_line"].queryset = StrainLine.objects.filter(is_active=True).order_by("name", "line_name")


MouseGenotypeComponentFormSet = inlineformset_factory(
    Mouse,
    MouseGenotypeComponent,
    form=MouseGenotypeComponentForm,
    extra=1,
    can_delete=True,
)
