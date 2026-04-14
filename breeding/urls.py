from django.urls import path

from .views import breeding_create, breeding_detail, breeding_list, litter_create


app_name = "breeding"

urlpatterns = [
    path("", breeding_list, name="breeding_list"),
    path("new/", breeding_create, name="breeding_create"),
    path("<int:breeding_pk>/litters/new/", litter_create, name="litter_create"),
    path("<int:pk>/", breeding_detail, name="breeding_detail"),
]
