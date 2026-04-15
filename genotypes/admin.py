from django.contrib import admin

from .models import Allele, Gene, MouseGenotype


class NoHardDeleteAdminMixin:
    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Gene)
class GeneAdmin(NoHardDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("symbol", "display_name", "key_name", "full_name", "is_active", "updated_at")
    search_fields = ("symbol", "display_name", "key_name", "full_name", "notes")
    list_filter = ("is_active",)


@admin.register(Allele)
class AlleleAdmin(NoHardDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("gene", "allele_name", "allele_type", "is_active", "updated_at")
    search_fields = ("gene__symbol", "allele_name", "description")
    list_filter = ("allele_type", "is_active")


@admin.register(MouseGenotype)
class MouseGenotypeAdmin(NoHardDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("mouse", "gene", "locus_name", "zygosity_display", "is_confirmed", "assay_date")
    search_fields = ("mouse__mouse_uid", "gene__symbol", "locus_name", "allele_1", "allele_2", "notes")
    list_filter = ("is_confirmed",)
