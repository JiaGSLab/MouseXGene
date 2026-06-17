from django.urls import path

from .picker_api import cage_picker_api, mouse_picker_api, mouse_strain_line_map_api
from .views import (
    cage_create,
    cage_detail,
    cage_edit,
    cage_history,
    cage_import,
    cage_import_template,
    cage_import_template_xlsx,
    cage_inventory_export,
    cage_inventory_export_xlsx,
    cage_list,
    cage_print,
    cage_retire,
    cages_export,
    cages_export_xlsx,
    strain_line_create,
    strain_line_detail,
    strain_line_document_delete,
    strain_line_document_download,
    strain_line_edit,
    strain_line_list,
    strain_line_upload_documents,
)


app_name = "colony"

urlpatterns = [
    path("api/picker/", cage_picker_api, name="cage_picker_api"),
    path("strain-lines/", strain_line_list, name="strain_line_list"),
    path("strain-lines/new/", strain_line_create, name="strain_line_create"),
    path("strain-lines/<int:pk>/edit/", strain_line_edit, name="strain_line_edit"),
    path("strain-lines/<int:pk>/documents/upload/", strain_line_upload_documents, name="strain_line_upload_documents"),
    path(
        "strain-lines/<int:pk>/documents/<int:doc_pk>/download/",
        strain_line_document_download,
        name="strain_line_document_download",
    ),
    path(
        "strain-lines/<int:pk>/documents/<int:doc_pk>/delete/",
        strain_line_document_delete,
        name="strain_line_document_delete",
    ),
    path("strain-lines/<int:pk>/", strain_line_detail, name="strain_line_detail"),
    path("new/", cage_create, name="cage_create"),
    path("import/", cage_import, name="cage_import"),
    path("import/template/", cage_import_template, name="cage_import_template"),
    path("import/template/xlsx/", cage_import_template_xlsx, name="cage_import_template_xlsx"),
    path("export/", cages_export, name="cages_export"),
    path("export/xlsx/", cages_export_xlsx, name="cages_export_xlsx"),
    path("", cage_list, name="cage_list"),
    path("<int:pk>/export/", cage_inventory_export, name="cage_inventory_export"),
    path("<int:pk>/export/xlsx/", cage_inventory_export_xlsx, name="cage_inventory_export_xlsx"),
    path("<int:pk>/print/", cage_print, name="cage_print"),
    path("<int:pk>/history/", cage_history, name="cage_history"),
    path("<int:pk>/edit/", cage_edit, name="cage_edit"),
    path("<int:pk>/retire/", cage_retire, name="cage_retire"),
    path("<int:pk>/", cage_detail, name="cage_detail"),
]
