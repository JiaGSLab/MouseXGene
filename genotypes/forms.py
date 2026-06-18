from django import forms

from colony.forms import validate_import_file_upload
from colony.models import Mouse
from .models import Gene, MouseGenotype


class GenotypeImportForm(forms.Form):
    data_file = forms.FileField(
        label="CSV or XLSX file",
        help_text="Required. Use the template; one row per locus result.",
    )

    def clean_data_file(self):
        return validate_import_file_upload(self.cleaned_data["data_file"])


class GeneForm(forms.ModelForm):
    class Meta:
        model = Gene
        fields = ["symbol", "key_name", "display_name", "full_name", "is_active", "notes"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
        help_texts = {
            "symbol": "Required symbol. Example: Trp53.",
            "key_name": "Optional short key for imports/integration. Example: TRP53.",
            "display_name": "Optional display name used in UI labels.",
            "full_name": "Optional full gene/genotype name. Example: Tumor protein p53.",
            "notes": "Optional context such as panel source or naming conventions.",
        }


class MouseGenotypeForm(forms.ModelForm):
    """Single-row entry for assay-style genotype records (vs bulk import)."""

    class Meta:
        model = MouseGenotype
        fields = ["mouse", "gene", "locus_name", "allele_1", "allele_2", "zygosity_display", "is_confirmed", "assay_date", "notes"]
        widgets = {
            "assay_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        self.fields["mouse"].queryset = Mouse.objects.select_related("project").order_by("mouse_uid")
        self.fields["gene"].queryset = Gene.objects.filter(is_active=True).order_by("symbol")
        self.fields["gene"].required = False
        self.fields["locus_name"].required = False
        self.fields["locus_name"].help_text = "Use when not selecting a Gene row, or to disambiguate duplicate loci."

    def clean(self):
        cleaned = super().clean()
        gene = cleaned.get("gene")
        locus_name = (cleaned.get("locus_name") or "").strip()
        if not gene and not locus_name:
            raise forms.ValidationError("Select a gene or enter a locus name.")
        return cleaned
