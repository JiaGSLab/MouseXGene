from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.conf import settings


def format_project_owner_label(user) -> str:
    """Display name for a project owner: profile display_name, else full name, else username."""
    if not user:
        return ""
    try:
        profile = user.profile
    except ObjectDoesNotExist:
        profile = None
    if profile is not None:
        dn = (getattr(profile, "display_name", "") or "").strip()
        if dn:
            return dn[:128]
    full = (user.get_full_name() or "").strip()
    if full:
        return full[:128]
    return (user.get_username() or "")[:128]


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Project(TimeStampedModel):
    name = models.CharField(max_length=128, unique=True)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="owned_projects",
    )
    owner_name = models.CharField(max_length=128, blank=True)
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="managed_projects",
        blank=True,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("name",)

    @property
    def owner_display(self) -> str:
        if not self.owner_id:
            return "—"
        return format_project_owner_label(self.owner) or "—"

    def save(self, *args, **kwargs):
        if self.owner_id:
            self.owner_name = format_project_owner_label(self.owner)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class ProjectMembership(TimeStampedModel):
    class Role(models.TextChoices):
        MANAGER = "manager", "Manager"
        MEMBER = "member", "Member"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="project_memberships")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.MEMBER)

    class Meta:
        unique_together = ("project", "user")
        ordering = ("project__name", "user__username")

    def __str__(self) -> str:
        return f"{self.user} in {self.project} ({self.role})"


class AuditLog(models.Model):
    class Action(models.TextChoices):
        CREATE = "create", "Create"
        UPDATE = "update", "Update"
        MOVE_CAGE = "move_cage", "Move Cage"
        IMPORT = "import", "Import"
        RECORD_LITTER = "record_litter", "Record Litter"
        WEAN = "wean", "Wean"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=32, choices=Action.choices)
    object_type = models.CharField(max_length=128)
    object_id = models.CharField(max_length=64)
    object_repr = models.CharField(max_length=255, blank=True)
    message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.get_action_display()} - {self.object_type}#{self.object_id}"


class ImportLog(models.Model):
    class ImportType(models.TextChoices):
        CAGE = "cage", "Cage"
        MOUSE = "mouse", "Mouse"
        GENOTYPE = "genotype", "Genotype"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="import_logs",
    )
    import_type = models.CharField(max_length=20, choices=ImportType.choices)
    filename = models.CharField(max_length=255, blank=True)
    success = models.BooleanField(default=False)
    created_count = models.PositiveIntegerField(default=0)
    error_summary = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        status = "Success" if self.success else "Failed"
        return f"{self.get_import_type_display()} import {status} ({self.created_count})"
