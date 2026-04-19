# Assign legacy project for mice without project, then require Mouse.project

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


LEGACY_PROJECT_NAME = "Legacy Imported"


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


def forwards_legacy_project_and_mice(apps, schema_editor):
    Project = apps.get_model("core", "Project")
    Mouse = apps.get_model("colony", "Mouse")
    if not Mouse.objects.filter(project_id__isnull=True).exists():
        return
    owner_id = _fallback_owner_id(apps)
    if owner_id is None:
        raise RuntimeError(
            "Cannot create Legacy Imported project: create at least one User before applying this migration."
        )
    legacy = Project.objects.filter(name=LEGACY_PROJECT_NAME).first()
    if legacy is None:
        legacy = Project.objects.create(
            name=LEGACY_PROJECT_NAME,
            description=(
                "Auto-created to hold mice that had no project before per-mouse project assignment was enforced."
            ),
            owner_id=owner_id,
            is_active=True,
        )
    elif legacy.owner_id is None:
        legacy.owner_id = owner_id
        legacy.save(update_fields=["owner_id"])

    Mouse.objects.filter(project_id__isnull=True).update(project_id=legacy.pk)


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("users", "0002_userprofile_role"),
        ("core", "0006_project_owner"),
        ("colony", "0004_cage_archived_at_mouse_death_reason_and_more"),
    ]

    operations = [
        migrations.RunPython(forwards_legacy_project_and_mice, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="mouse",
            name="project",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="mice",
                to="core.project",
            ),
        ),
    ]
