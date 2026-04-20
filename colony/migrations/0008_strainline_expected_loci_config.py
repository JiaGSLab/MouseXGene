from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("colony", "0007_strainline_expected_loci_template"),
    ]

    operations = [
        migrations.AddField(
            model_name="strainline",
            name="expected_loci_config",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
