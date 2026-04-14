from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Project(TimeStampedModel):
    name = models.CharField(max_length=128, unique=True)
    description = models.TextField(blank=True)
    owner_name = models.CharField(max_length=128, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class AuditLog(TimeStampedModel):
    actor = models.CharField(max_length=150)
    action = models.CharField(max_length=128)
    object_type = models.CharField(max_length=128)
    object_id = models.CharField(max_length=64)
    details = models.JSONField(default=dict, blank=True)

    def __str__(self) -> str:
        return f"{self.actor} - {self.action}"
