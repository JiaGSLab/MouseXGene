from django.contrib.auth.views import LoginView, PasswordChangeView
from django.contrib.auth import get_user_model
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy

from .forms import SelfProfileForm, UserRoleForm
from .models import UserProfile
from .permissions import authenticated_required, is_admin


class AppLoginView(LoginView):
    """Application login (not Django admin). Any active user may authenticate here."""

    template_name = "users/login.html"
    redirect_authenticated_user = True


class AppPasswordChangeView(SuccessMessageMixin, PasswordChangeView):
    template_name = "users/password_change_form.html"
    success_url = reverse_lazy("accounts:password_change_done")
    success_message = "Your password was changed successfully."


@authenticated_required
def account_profile(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    return render(
        request,
        "users/profile.html",
        {
            "profile": profile,
        },
    )


@authenticated_required
def account_profile_edit(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    if request.method == "POST":
        form = SelfProfileForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            return redirect("accounts:profile")
    else:
        form = SelfProfileForm(instance=profile)
    return render(request, "users/profile_form.html", {"form": form})


@authenticated_required
def user_role_list(request):
    if not is_admin(request.user):
        raise PermissionDenied("Only admins can manage roles.")
    users = get_user_model().objects.select_related("profile").order_by("username")
    return render(request, "users/user_role_list.html", {"users": users})


@authenticated_required
def user_role_edit(request, pk: int):
    if not is_admin(request.user):
        raise PermissionDenied("Only admins can manage roles.")
    user_obj = get_object_or_404(get_user_model(), pk=pk)
    profile, _ = UserProfile.objects.get_or_create(user=user_obj)
    if request.method == "POST":
        form = UserRoleForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            return redirect("accounts:user_role_list")
    else:
        form = UserRoleForm(instance=profile)
    return render(request, "users/user_role_form.html", {"form": form, "target_user": user_obj})
