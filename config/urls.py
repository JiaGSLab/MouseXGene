from django.contrib import admin
from django.urls import include, path


urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("users.urls")),
    path("", include("core.urls")),
    path("cages/", include("colony.urls")),
    path("mice/", include("colony.mouse_urls")),
    path("breedings/", include("breeding.urls")),
    path("litters/", include("breeding.litter_urls")),
    path("genotypes/", include("genotypes.urls")),
]

handler403 = "core.views.permission_denied"
