from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("breeding", "0007_breeding_created_updated_by"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="breeding",
            index=models.Index(fields=["active", "start_date"], name="breeding_active_start"),
        ),
        migrations.AddIndex(
            model_name="breeding",
            index=models.Index(fields=["status", "active"], name="breeding_status_active"),
        ),
        migrations.AddIndex(
            model_name="litter",
            index=models.Index(fields=["birth_date"], name="breeding_litter_birth"),
        ),
        migrations.AddIndex(
            model_name="litter",
            index=models.Index(fields=["wean_date"], name="breeding_litter_wean"),
        ),
    ]
