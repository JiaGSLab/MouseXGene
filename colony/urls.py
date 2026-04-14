from django.urls import path

from .views import cage_detail, cage_list


app_name = "colony"

urlpatterns = [
    path("", cage_list, name="cage_list"),
    path("<int:pk>/", cage_detail, name="cage_detail"),
]
