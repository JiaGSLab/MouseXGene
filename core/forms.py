from django import forms
from django.contrib.auth import get_user_model
from django.forms import inlineformset_factory

from .models import Project, ProjectMembership
from users.permissions import is_admin


class ProjectForm(forms.ModelForm):
    confirm_owner_transfer = forms.BooleanField(
        required=False,
        label="I confirm this project should be transferred to the selected owner.",
    )

    class Meta:
        model = Project
        fields = ["name", "owner", "description", "is_active"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
        }
        help_texts = {
            "owner": "Owner display on the Projects list follows this user’s profile name, full name, or username.",
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["owner"].queryset = get_user_model().objects.order_by("username")
        self.fields["owner"].required = True
        if user is not None and not is_admin(user):
            owner = self.instance.owner if self.instance.pk else user
            self.fields["owner"].queryset = get_user_model().objects.filter(pk=owner.pk)
            self.fields["owner"].initial = owner
            self.fields["owner"].disabled = True
            self.fields.pop("confirm_owner_transfer")
        elif not self.instance.pk:
            self.fields.pop("confirm_owner_transfer")

    def clean(self):
        cleaned = super().clean()
        if (
            self.instance.pk
            and self.user is not None
            and is_admin(self.user)
            and cleaned.get("owner")
            and cleaned["owner"].pk != self.instance.owner_id
            and not cleaned.get("confirm_owner_transfer")
        ):
            self.add_error("confirm_owner_transfer", "Confirm the owner transfer before saving.")
        return cleaned


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
