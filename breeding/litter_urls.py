from django.urls import path

from .views import litter_create_from_breeding, litter_detail, litter_edit, litter_end, litter_list, litter_wean


app_name = "litters"

urlpatterns = [
    path("", litter_list, name="litter_list"),
    path("new/", litter_create_from_breeding, name="litter_create_from_breeding"),
    path("<int:pk>/edit/", litter_edit, name="litter_edit"),
    path("<int:pk>/end/", litter_end, name="litter_end"),
    path("<int:pk>/wean/", litter_wean, name="litter_wean"),
    path("<int:pk>/", litter_detail, name="litter_detail"),
]
