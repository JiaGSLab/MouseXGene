from django.urls import path

from .views import litter_detail, litter_list, litter_wean


app_name = "litters"

urlpatterns = [
    path("", litter_list, name="litter_list"),
    path("<int:pk>/wean/", litter_wean, name="litter_wean"),
    path("<int:pk>/", litter_detail, name="litter_detail"),
]
