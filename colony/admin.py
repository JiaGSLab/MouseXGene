from django.contrib import admin

from .cage_lifecycle import reconcile_mouse_cage_membership
from .models import Cage, CageMembership, Colony, Mouse, MouseExperimentAssignment, MouseGenotypeComponent, StrainLine


class NoHardDeleteAdminMixin:
    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StrainLine)
class StrainLineAdmin(NoHardDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("name", "owner", "short_name", "category", "gene_or_locus", "is_active", "updated_at")
    search_fields = ("name", "short_name", "category", "gene_or_locus", "line_name", "key_name", "notes")
    list_filter = ("category", "species", "is_active")


class MouseGenotypeComponentInline(admin.TabularInline):
    model = MouseGenotypeComponent
    extra = 1
    autocomplete_fields = ("strain_line",)


@admin.register(Cage)
class CageAdmin(NoHardDeleteAdminMixin, admin.ModelAdmin):
    list_display = (
        "cage_id",
        "project",
        "colony",
        "created_date",
        "room",
        "rack",
        "position",
        "cage_use_display",
        "cage_type",
        "purpose",
        "status",
        "archived_at",
    )
    search_fields = ("cage_id", "room", "rack", "position", "notes")
    list_filter = ("project", "colony", "cage_type", "purpose", "status")

    @admin.display(description="Cage use", ordering="purpose")
    def cage_use_display(self, obj):
        return obj.get_cage_use_display()


@admin.register(Colony)
class ColonyAdmin(NoHardDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("name", "project", "strain_line", "status", "updated_at")
    search_fields = ("name", "project__name", "strain_line__line_name", "strain_line__name", "notes")
    list_filter = ("status", "project", "strain_line")


@admin.register(Mouse)
class MouseAdmin(NoHardDeleteAdminMixin, admin.ModelAdmin):
    list_display = (
        "mouse_uid",
        "genotype_summary",
        "sex",
        "status",
        "strain_line",
        "colony",
        "current_cage",
        "birth_date",
        "death_date",
        "euthanasia_date",
        "toe_tag",
        "origin",
    )
    search_fields = ("mouse_uid", "ear_tag", "toe_tag", "origin", "coat_color", "death_reason", "notes")
    list_filter = ("sex", "status", "strain_line", "colony", "project")
    inlines = (MouseGenotypeComponentInline,)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        reconcile_mouse_cage_membership(
            obj,
            reason="Django admin cage history sync.",
            apply=True,
        )


@admin.register(CageMembership)
class CageMembershipAdmin(NoHardDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("mouse", "cage", "start_date", "end_date", "is_current", "reason")
    search_fields = ("mouse__mouse_uid", "cage__cage_id", "reason", "notes")
    list_filter = ("is_current",)


@admin.register(MouseExperimentAssignment)
class MouseExperimentAssignmentAdmin(NoHardDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("mouse", "started_at", "ended_at", "created_by", "ended_by")
    search_fields = ("mouse__mouse_uid", "note")
    list_filter = ("ended_at", "started_at")
