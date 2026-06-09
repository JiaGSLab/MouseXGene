from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("colony", "0015_strainlinedocument_description"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="mouse",
            index=models.Index(fields=["status", "birth_date"], name="colony_mouse_status_birth"),
        ),
        migrations.AddIndex(
            model_name="mouse",
            index=models.Index(fields=["status", "mouse_uid"], name="colony_mouse_status_uid"),
        ),
        migrations.AddIndex(
            model_name="cage",
            index=models.Index(fields=["status", "cage_id"], name="colony_cage_status_id"),
        ),
    ]
