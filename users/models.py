from django.contrib.auth import get_user_model
from django.db import models


User = get_user_model()


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    display_name = models.CharField(max_length=150, blank=True)

    def __str__(self) -> str:
        return self.display_name or self.user.get_username()
