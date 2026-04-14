from django import forms


class GenotypeImportForm(forms.Form):
    data_file = forms.FileField(label="CSV or XLSX file")
