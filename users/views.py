import hashlib

from django.contrib import messages
from django.contrib.auth.views import LoginView, PasswordChangeView
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy

from .forms import SelfProfileForm, UserRoleForm
from .models import UserProfile
from .permissions import authenticated_required, is_admin
from core.audit import log_audit_event
from core.models import AuditLog


class AppLoginView(LoginView):
    """Application login (not Django admin). Any active user may authenticate here."""

    template_name = "users/login.html"
    redirect_authenticated_user = True

    rate_limit_attempts = 8
    rate_limit_window_seconds = 15 * 60

    def _rate_limit_key(self) -> str:
        forwarded_ip = (self.request.META.get("HTTP_X_REAL_IP") or "").strip()
        remote_ip = forwarded_ip or self.request.META.get("REMOTE_ADDR", "unknown")
        username = (self.request.POST.get("username") or "").strip().casefold()
        digest = hashlib.sha256(f"{remote_ip}|{username}".encode("utf-8")).hexdigest()
        return f"login-failures:{digest}"

    def post(self, request, *args, **kwargs):
        key = self._rate_limit_key()
        failures = int(cache.get(key, 0) or 0)
        if failures >= self.rate_limit_attempts:
            form = self.get_form()
            form.add_error(None, "Too many failed sign-in attempts. Wait 15 minutes and try again.")
            return self.form_invalid(form)
        response = super().post(request, *args, **kwargs)
        if 300 <= response.status_code < 400:
            cache.delete(key)
        else:
            cache.set(key, failures + 1, self.rate_limit_window_seconds)
        return response


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
            messages.success(request, "Profile updated.")
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
            previous_role = profile.role
            profile = form.save()
            log_audit_event(
                user=request.user,
                action=AuditLog.Action.UPDATE,
                obj=profile,
                message=f"Changed role for {user_obj.get_username()} from {previous_role} to {profile.role}.",
            )
            messages.success(request, f"Role updated for {user_obj.get_username()}.")
            return redirect("accounts:user_role_list")
    else:
        form = UserRoleForm(instance=profile)
    return render(request, "users/user_role_form.html", {"form": form, "target_user": user_obj})


@authenticated_required
def user_detail(request, pk: int):
    user_obj = get_object_or_404(get_user_model().objects.select_related("profile"), pk=pk)
    return render(request, "users/user_detail.html", {"target_user": user_obj})
