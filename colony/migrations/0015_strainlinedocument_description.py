from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("colony", "0014_mouse_source_breeding"),
    ]

    operations = [
        migrations.AddField(
            model_name="strainlinedocument",
            name="description",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="strainlinedocument",
            name="description_kind",
            field=models.CharField(
                choices=[
                    ("strain_line_info", "Strain line info"),
                    ("genotype_info", "Genotype info"),
                    ("custom", "Custom"),
                ],
                default="custom",
                max_length=32,
            ),
        ),
    ]
