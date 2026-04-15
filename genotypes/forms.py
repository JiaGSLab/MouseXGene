from django import forms

from .models import Gene


class GenotypeImportForm(forms.Form):
    data_file = forms.FileField(
        label="CSV or XLSX file",
        help_text="Required. Use the template; one row per locus result.",
    )


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
