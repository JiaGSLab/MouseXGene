from django.contrib import admin

from .models import UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "display_name", "import_uid_prefix", "role")
    search_fields = ("user__username", "display_name", "import_uid_prefix", "role")
    list_filter = ("role",)
