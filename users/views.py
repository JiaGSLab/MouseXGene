from django.contrib.auth.views import LoginView
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render

from .forms import UserRoleForm
from .models import UserProfile
from .permissions import authenticated_required, is_admin


class AppLoginView(LoginView):
    """Application login (not Django admin). Any active user may authenticate here."""

    template_name = "users/login.html"
    redirect_authenticated_user = True


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
