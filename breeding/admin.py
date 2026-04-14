from django.contrib import admin

from .models import Breeding, Litter


@admin.register(Breeding)
class BreedingAdmin(admin.ModelAdmin):
    list_display = ("code", "male", "female", "start_date", "active")
    list_filter = ("active",)
    search_fields = ("code", "male__mouse_uid", "female__mouse_uid")


@admin.register(Litter)
class LitterAdmin(admin.ModelAdmin):
    list_display = ("breeding", "litter_date", "size")
    search_fields = ("breeding__code",)
