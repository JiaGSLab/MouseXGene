from django import forms
from django.contrib.auth import get_user_model
from django.forms import inlineformset_factory

from .models import Project, ProjectMembership


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ["name", "owner", "description", "is_active"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
        }
        help_texts = {
            "owner": "Owner display on the Projects list follows this user’s profile name, full name, or username.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["owner"].queryset = get_user_model().objects.order_by("username")
        self.fields["owner"].required = True


class ProjectMembershipForm(forms.ModelForm):
    class Meta:
        model = ProjectMembership
        fields = ["user", "role"]


ProjectMembershipFormSet = inlineformset_factory(
    Project,
    ProjectMembership,
    form=ProjectMembershipForm,
    extra=1,
    can_delete=True,
)
