from django.urls import path

from .views import cage_create, cage_detail, cage_list


app_name = "colony"

urlpatterns = [
    path("new/", cage_create, name="cage_create"),
    path("", cage_list, name="cage_list"),
    path("<int:pk>/", cage_detail, name="cage_detail"),
]
