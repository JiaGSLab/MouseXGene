from django import forms
from django.utils import timezone

from .models import Cage, Mouse


class CageForm(forms.ModelForm):
    class Meta:
        model = Cage
        fields = [
            "cage_id",
            "room",
            "rack",
            "position",
            "cage_type",
            "purpose",
            "status",
            "notes",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 4}),
        }


class MouseForm(forms.ModelForm):
    class Meta:
        model = Mouse
        fields = [
            "mouse_uid",
            "sex",
            "birth_date",
            "status",
            "strain_line",
            "current_cage",
            "sire",
            "dam",
            "project",
            "ear_tag",
            "coat_color",
            "notes",
        ]
        widgets = {
            "birth_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["strain_line"].queryset = self.fields["strain_line"].queryset.order_by("line_name")
        self.fields["current_cage"].queryset = self.fields["current_cage"].queryset.order_by("cage_id")
        self.fields["sire"].queryset = self.fields["sire"].queryset.order_by("mouse_uid")
        self.fields["dam"].queryset = self.fields["dam"].queryset.order_by("mouse_uid")
        self.fields["project"].queryset = self.fields["project"].queryset.order_by("name")


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
    data_file = forms.FileField(label="CSV or XLSX file")


class MouseImportForm(forms.Form):
    data_file = forms.FileField(label="CSV or XLSX file")
