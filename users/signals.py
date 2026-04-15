from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import UserProfile

User = get_user_model()


@receiver(post_save, sender=User)
def ensure_user_profile(sender, instance, created, **kwargs):
    if created:
        role = UserProfile.Role.ADMIN if instance.is_superuser else UserProfile.Role.MEMBER
        UserProfile.objects.create(user=instance, role=role)
        return
    profile, _ = UserProfile.objects.get_or_create(user=instance)
    if instance.is_superuser and profile.role != UserProfile.Role.ADMIN:
        profile.role = UserProfile.Role.ADMIN
        profile.save(update_fields=["role"])
