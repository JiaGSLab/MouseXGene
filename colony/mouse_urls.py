from django.urls import path

from .views import mouse_detail, mouse_list


app_name = "mice"

urlpatterns = [
    path("", mouse_list, name="mouse_list"),
    path("<int:pk>/", mouse_detail, name="mouse_detail"),
]
