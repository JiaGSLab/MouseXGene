from django.contrib.auth.views import LogoutView
from django.urls import path

from .views import AppLoginView, user_role_edit, user_role_list

app_name = "accounts"

urlpatterns = [
    path("login/", AppLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("roles/", user_role_list, name="user_role_list"),
    path("roles/<int:pk>/", user_role_edit, name="user_role_edit"),
]
