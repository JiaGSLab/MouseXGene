from django.urls import path

from .views import mouse_create, mouse_detail, mouse_edit, mouse_list, mouse_move


app_name = "mice"

urlpatterns = [
    path("new/", mouse_create, name="mouse_create"),
    path("", mouse_list, name="mouse_list"),
    path("<int:pk>/edit/", mouse_edit, name="mouse_edit"),
    path("<int:pk>/move/", mouse_move, name="mouse_move"),
    path("<int:pk>/", mouse_detail, name="mouse_detail"),
]
