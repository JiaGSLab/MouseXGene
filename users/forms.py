from django import forms

from .models import UserProfile


class UserRoleForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ["display_name", "role"]
