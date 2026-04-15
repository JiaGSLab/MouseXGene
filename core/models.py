from django.db import models
from django.conf import settings


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Project(TimeStampedModel):
    name = models.CharField(max_length=128, unique=True)
    description = models.TextField(blank=True)
    owner_name = models.CharField(max_length=128, blank=True)
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="managed_projects",
        blank=True,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("name",)

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
