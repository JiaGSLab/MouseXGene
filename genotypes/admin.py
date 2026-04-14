from django.contrib import admin

from .models import Allele, Gene, MouseGenotype


@admin.register(Gene)
class GeneAdmin(admin.ModelAdmin):
    list_display = ("symbol", "full_name", "updated_at")
    search_fields = ("symbol", "full_name", "notes")


@admin.register(Allele)
class AlleleAdmin(admin.ModelAdmin):
    list_display = ("gene", "allele_name", "allele_type", "is_active", "updated_at")
    search_fields = ("gene__symbol", "allele_name", "description")
    list_filter = ("allele_type", "is_active")


@admin.register(MouseGenotype)
class MouseGenotypeAdmin(admin.ModelAdmin):
    list_display = ("mouse", "gene", "locus_name", "zygosity_display", "is_confirmed", "assay_date")
    search_fields = ("mouse__mouse_uid", "gene__symbol", "locus_name", "allele_1", "allele_2", "notes")
    list_filter = ("is_confirmed",)
