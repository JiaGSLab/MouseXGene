from django.urls import path

from .views import (
    gene_create,
    gene_edit,
    gene_list,
    genotype_import,
    genotype_import_template,
    genotype_import_template_xlsx,
    genotype_record_list,
    mouse_genotype_create,
    mouse_genotype_edit,
)


app_name = "genotypes"

urlpatterns = [
    path("", gene_list, name="gene_list"),
    path("import/", genotype_import, name="genotype_import"),
    path("import/template/", genotype_import_template, name="genotype_import_template"),
    path("import/template/xlsx/", genotype_import_template_xlsx, name="genotype_import_template_xlsx"),
    path("records/", genotype_record_list, name="genotype_record_list"),
    path("records/new/", mouse_genotype_create, name="mouse_genotype_create"),
    path("records/<int:pk>/edit/", mouse_genotype_edit, name="mouse_genotype_edit"),
    path("genes/new/", gene_create, name="gene_create"),
    path("genes/<int:pk>/edit/", gene_edit, name="gene_edit"),
]
