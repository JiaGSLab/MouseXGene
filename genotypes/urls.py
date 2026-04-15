from django.urls import path

from .views import (
    gene_create,
    gene_edit,
    gene_list,
    genotype_import,
    genotype_import_template,
    genotype_import_template_xlsx,
)


app_name = "genotypes"

urlpatterns = [
    path("", gene_list, name="gene_list"),
    path("new/", gene_create, name="gene_create"),
    path("<int:pk>/edit/", gene_edit, name="gene_edit"),
    path("import/", genotype_import, name="genotype_import"),
    path("import/template/", genotype_import_template, name="genotype_import_template"),
    path("import/template/xlsx/", genotype_import_template_xlsx, name="genotype_import_template_xlsx"),
]
