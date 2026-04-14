from django.urls import path

from .views import (
    cage_create,
    cage_detail,
    cage_import,
    cage_import_template,
    cage_inventory_export,
    cage_list,
    cage_print,
    cages_export,
)


app_name = "colony"

urlpatterns = [
    path("new/", cage_create, name="cage_create"),
    path("import/", cage_import, name="cage_import"),
    path("import/template/", cage_import_template, name="cage_import_template"),
    path("export/", cages_export, name="cages_export"),
    path("", cage_list, name="cage_list"),
    path("<int:pk>/export/", cage_inventory_export, name="cage_inventory_export"),
    path("<int:pk>/print/", cage_print, name="cage_print"),
    path("<int:pk>/", cage_detail, name="cage_detail"),
]
