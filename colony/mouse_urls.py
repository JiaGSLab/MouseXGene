from django.urls import path

from .views import (
    mouse_create,
    mouse_detail,
    mouse_edit,
    mouse_import,
    mouse_import_template,
    mouse_genotypes_export,
    mouse_list,
    mouse_pedigree,
    mouse_move,
    mice_export,
)


app_name = "mice"

urlpatterns = [
    path("new/", mouse_create, name="mouse_create"),
    path("import/", mouse_import, name="mouse_import"),
    path("import/template/", mouse_import_template, name="mouse_import_template"),
    path("export/", mice_export, name="mice_export"),
    path("", mouse_list, name="mouse_list"),
    path("<int:pk>/edit/", mouse_edit, name="mouse_edit"),
    path("<int:pk>/move/", mouse_move, name="mouse_move"),
    path("<int:pk>/pedigree/", mouse_pedigree, name="mouse_pedigree"),
    path("<int:pk>/genotypes/export/", mouse_genotypes_export, name="mouse_genotypes_export"),
    path("<int:pk>/", mouse_detail, name="mouse_detail"),
]
