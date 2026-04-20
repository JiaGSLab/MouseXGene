from django.contrib.auth.views import LogoutView, PasswordChangeDoneView
from django.urls import path

from .views import AppLoginView, AppPasswordChangeView, account_profile, account_profile_edit, user_role_edit, user_role_list

app_name = "accounts"

urlpatterns = [
    path("login/", AppLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("me/", account_profile, name="profile"),
    path("me/edit/", account_profile_edit, name="profile_edit"),
    path("password/change/", AppPasswordChangeView.as_view(), name="password_change"),
    path(
        "password/change/done/",
        PasswordChangeDoneView.as_view(template_name="users/password_change_done.html"),
        name="password_change_done",
    ),
    path("roles/", user_role_list, name="user_role_list"),
    path("roles/<int:pk>/", user_role_edit, name="user_role_edit"),
]
