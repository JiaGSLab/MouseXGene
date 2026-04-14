from django.urls import path

from .views import genotype_import, genotype_import_template


app_name = "genotypes"

urlpatterns = [
    path("import/", genotype_import, name="genotype_import"),
    path("import/template/", genotype_import_template, name="genotype_import_template"),
]
