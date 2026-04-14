from django.contrib import admin

from .models import AuditLog, Project


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "owner_name", "is_active", "updated_at")
    search_fields = ("name", "owner_name", "description")
    list_filter = ("is_active",)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("actor", "action", "object_type", "object_id", "created_at")
    search_fields = ("actor", "action", "object_type", "object_id")
