# Generated manually for project ownership backfill

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def _fallback_owner_id(apps):
    User = apps.get_model(settings.AUTH_USER_MODEL)
    UserProfile = apps.get_model("users", "UserProfile")
    u = User.objects.filter(is_superuser=True).order_by("id").first()
    if u:
        return u.pk
    prof = UserProfile.objects.filter(role="ADMIN").select_related("user").order_by("user_id").first()
    if prof:
        return prof.user_id
    u = User.objects.order_by("id").first()
    return u.pk if u else None


def forwards_assign_owners(apps, schema_editor):
    Project = apps.get_model("core", "Project")
    pending = Project.objects.filter(owner_id__isnull=True)
    if not pending.exists():
        return
    owner_id = _fallback_owner_id(apps)
    if owner_id is None:
        raise RuntimeError(
            "Cannot migrate Project.owner: create at least one User before applying this migration."
        )
    pending.update(owner_id=owner_id)


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("users", "0002_userprofile_role"),
        ("core", "0005_projectmembership"),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="owner",
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="owned_projects",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(forwards_assign_owners, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="project",
            name="owner",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="owned_projects",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
