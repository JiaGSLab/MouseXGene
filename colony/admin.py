from django.contrib import admin

from .models import Cage, CageMembership, Mouse, StrainLine


@admin.register(StrainLine)
class StrainLineAdmin(admin.ModelAdmin):
    list_display = ("line_name", "species", "background", "is_active", "updated_at")
    search_fields = ("line_name", "background", "source", "notes")
    list_filter = ("species", "is_active")


@admin.register(Cage)
class CageAdmin(admin.ModelAdmin):
    list_display = ("cage_id", "room", "rack", "position", "cage_type", "purpose", "status")
    search_fields = ("cage_id", "room", "rack", "position", "notes")
    list_filter = ("cage_type", "purpose", "status")


@admin.register(Mouse)
class MouseAdmin(admin.ModelAdmin):
    list_display = ("mouse_uid", "sex", "status", "strain_line", "current_cage", "birth_date")
    search_fields = ("mouse_uid", "ear_tag", "coat_color", "notes")
    list_filter = ("sex", "status", "strain_line")


@admin.register(CageMembership)
class CageMembershipAdmin(admin.ModelAdmin):
    list_display = ("mouse", "cage", "start_date", "end_date", "is_current", "reason")
    search_fields = ("mouse__mouse_uid", "cage__cage_id", "reason", "notes")
    list_filter = ("is_current",)
