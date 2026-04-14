from django.contrib import admin

from .models import AuditLog, ImportLog, Project


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "owner_name", "is_active", "updated_at")
    search_fields = ("name", "owner_name", "description")
    list_filter = ("is_active",)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "action", "object_type", "object_id", "object_repr")
    search_fields = ("message", "object_type", "object_id", "object_repr", "user__username")
    list_filter = ("action", "object_type", "user")
    ordering = ("-created_at",)


@admin.register(ImportLog)
class ImportLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "import_type", "filename", "success", "created_count", "user")
    search_fields = ("filename", "error_summary", "user__username")
    list_filter = ("import_type", "success", "user")
    ordering = ("-created_at",)
