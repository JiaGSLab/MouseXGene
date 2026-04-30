from django.conf import settings
from django.db import migrations, models
from django.db.models import F
import django.db.models.deletion


def forwards_owner_from_created_by(apps, schema_editor):
    StrainLine = apps.get_model("colony", "StrainLine")
    StrainLine.objects.filter(owner_id__isnull=True, created_by_id__isnull=False).update(owner_id=F("created_by_id"))


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("colony", "0009_cage_mouse_strainline_created_updated_by"),
    ]

    operations = [
        migrations.AddField(
            model_name="strainline",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="owned_strain_lines",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(forwards_owner_from_created_by, migrations.RunPython.noop),
    ]
