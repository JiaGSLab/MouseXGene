from django.contrib import admin

from .models import Breeding, Litter, LitterPup


class NoHardDeleteAdminMixin:
    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Breeding)
class BreedingAdmin(NoHardDeleteAdminMixin, admin.ModelAdmin):
    list_display = (
        "breeding_code",
        "cage",
        "breeding_type",
        "male",
        "female_1",
        "female_2",
        "start_date",
        "status",
        "active",
    )
    list_filter = ("breeding_type", "status", "active")
    search_fields = ("breeding_code", "cage__cage_id", "male__mouse_uid", "female_1__mouse_uid", "female_2__mouse_uid")


class LitterPupInline(admin.TabularInline):
    model = LitterPup
    extra = 0
    raw_id_fields = ("mouse",)


@admin.register(Litter)
class LitterAdmin(NoHardDeleteAdminMixin, admin.ModelAdmin):
    inlines = (LitterPupInline,)
    list_display = (
        "litter_code",
        "breeding",
        "birth_date",
        "litter_status",
        "total_born",
        "alive_count",
        "dead_count",
        "wean_date",
        "tail_tag_date",
        "is_archived",
    )
    list_filter = ("litter_status", "is_archived")
    search_fields = ("litter_code", "breeding__breeding_code")
