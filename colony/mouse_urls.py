from django.urls import path

from .views import (
    family_tree,
    mouse_create,
    mouse_detail,
    mouse_edit,
    mouse_end,
    mouse_genotypes_export_xlsx,
    mouse_genotype_components_edit,
    mouse_import,
    mouse_import_template,
    mouse_import_template_xlsx,
    mouse_genotypes_export,
    mouse_list,
    mouse_pedigree,
    mouse_move,
    mice_export,
    mice_export_xlsx,
)


app_name = "mice"

urlpatterns = [
    path("family-tree/", family_tree, name="family_tree"),
    path("new/", mouse_create, name="mouse_create"),
    path("import/", mouse_import, name="mouse_import"),
    path("import/template/", mouse_import_template, name="mouse_import_template"),
    path("import/template/xlsx/", mouse_import_template_xlsx, name="mouse_import_template_xlsx"),
    path("export/", mice_export, name="mice_export"),
    path("export/xlsx/", mice_export_xlsx, name="mice_export_xlsx"),
    path("", mouse_list, name="mouse_list"),
    path("<int:pk>/edit/", mouse_edit, name="mouse_edit"),
    path("<int:pk>/move/", mouse_move, name="mouse_move"),
    path("<int:pk>/end/", mouse_end, name="mouse_end"),
    path("<int:pk>/pedigree/", mouse_pedigree, name="mouse_pedigree"),
    path("<int:pk>/genotypes/export/", mouse_genotypes_export, name="mouse_genotypes_export"),
    path("<int:pk>/genotypes/export/xlsx/", mouse_genotypes_export_xlsx, name="mouse_genotypes_export_xlsx"),
    path("<int:pk>/genotype-components/", mouse_genotype_components_edit, name="mouse_genotype_components_edit"),
    path("<int:pk>/", mouse_detail, name="mouse_detail"),
]
