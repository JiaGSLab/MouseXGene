from django.contrib import admin

from .models import Cage, CageMembership, Colony, Mouse, MouseGenotypeComponent, StrainLine


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
        "cage_type",
        "purpose",
        "status",
        "archived_at",
    )
    search_fields = ("cage_id", "room", "rack", "position", "notes")
    list_filter = ("project", "colony", "cage_type", "purpose", "status")


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


@admin.register(CageMembership)
class CageMembershipAdmin(NoHardDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("mouse", "cage", "start_date", "end_date", "is_current", "reason")
    search_fields = ("mouse__mouse_uid", "cage__cage_id", "reason", "notes")
    list_filter = ("is_current",)
