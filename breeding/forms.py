from django import forms
from django.forms import formset_factory

from colony.models import Cage, Mouse
from .models import Breeding, Litter


class BreedingForm(forms.ModelForm):
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
        self.fields["cage"].queryset = self.fields["cage"].queryset.order_by("cage_id")
        self.fields["male"].queryset = Mouse.objects.filter(sex=Mouse.Sex.MALE).order_by("mouse_uid")
        female_qs = Mouse.objects.filter(sex=Mouse.Sex.FEMALE).order_by("mouse_uid")
        self.fields["female_1"].queryset = female_qs
        self.fields["female_2"].queryset = female_qs


class LitterForm(forms.ModelForm):
    class Meta:
        model = Litter
        fields = [
            "litter_code",
            "birth_date",
            "total_born",
            "alive_count",
            "dead_count",
            "wean_date",
            "notes",
        ]
        widgets = {
            "birth_date": forms.DateInput(attrs={"type": "date"}),
            "wean_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }


class WeanLitterForm(forms.Form):
    target_cage = forms.ModelChoiceField(queryset=Cage.objects.none(), label="Target Cage")
    wean_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), label="Wean Date")
    number_of_pups = forms.IntegerField(min_value=1, label="Number of Pups")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["target_cage"].queryset = Cage.objects.order_by("cage_id")


class PupEntryForm(forms.Form):
    mouse_uid = forms.CharField(max_length=64, label="Mouse UID")
    sex = forms.ChoiceField(choices=Mouse.Sex.choices, label="Sex")
    ear_tag = forms.CharField(max_length=64, required=False, label="Ear Tag")
    coat_color = forms.CharField(max_length=64, required=False, label="Coat Color")
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Notes")


def get_pup_formset(form_count: int):
    return formset_factory(PupEntryForm, extra=form_count, min_num=form_count, validate_min=True)
