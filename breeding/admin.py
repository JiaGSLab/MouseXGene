from django.contrib import admin

from .models import Breeding, Litter


@admin.register(Breeding)
class BreedingAdmin(admin.ModelAdmin):
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


@admin.register(Litter)
class LitterAdmin(admin.ModelAdmin):
    list_display = ("litter_code", "breeding", "birth_date", "total_born", "alive_count", "dead_count", "wean_date")
    search_fields = ("litter_code", "breeding__breeding_code")
