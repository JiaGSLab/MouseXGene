from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models

from .import_prefix import validate_import_prefix_format


User = get_user_model()


class UserProfile(models.Model):
    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Admin"
        MANAGER = "MANAGER", "Manager"
        MEMBER = "MEMBER", "Member"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    display_name = models.CharField(max_length=150, blank=True)
    import_uid_prefix = models.CharField(
        max_length=16,
        blank=True,
        help_text="Optional. Used when you enable “prefix my IDs” on cage/mouse import "
        "(e.g. JG → JG-M001). Keeps numeric IDs unique across people.",
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.MEMBER)

    def clean(self) -> None:
        super().clean()
        if self.import_uid_prefix:
            try:
                self.import_uid_prefix = validate_import_prefix_format(self.import_uid_prefix)
            except ValidationError as exc:
                raise ValidationError({"import_uid_prefix": exc.messages}) from exc
        else:
            self.import_uid_prefix = ""

    def __str__(self) -> str:
        return self.display_name or self.user.get_username()
