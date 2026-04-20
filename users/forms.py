from django import forms

from .import_prefix import validate_import_prefix_format
from .models import UserProfile


class UserRoleForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ["display_name", "import_uid_prefix", "role"]
        help_texts = {
            "import_uid_prefix": "Optional lab initials or code (e.g. JG). Used when importing cages/mice with “prefix my IDs”.",
        }

    def clean_import_uid_prefix(self):
        raw = self.cleaned_data.get("import_uid_prefix") or ""
        if not raw.strip():
            return ""
        return validate_import_prefix_format(raw)


class UserImportPrefixForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ["import_uid_prefix"]
        help_texts = {
            "import_uid_prefix": "Letters, numbers, hyphens (1–15 chars). Example: JG → stored IDs like JG-M001.",
        }

    def clean_import_uid_prefix(self):
        raw = self.cleaned_data.get("import_uid_prefix") or ""
        if not raw.strip():
            return ""
        return validate_import_prefix_format(raw)


class SelfProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ["display_name", "import_uid_prefix"]
        help_texts = {
            "display_name": "Optional name shown in project owner displays.",
            "import_uid_prefix": "Letters, numbers, hyphens (1-15 chars). Example: JG.",
        }

    def clean_import_uid_prefix(self):
        raw = self.cleaned_data.get("import_uid_prefix") or ""
        if not raw.strip():
            return ""
        return validate_import_prefix_format(raw)
