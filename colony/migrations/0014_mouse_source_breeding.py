from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("breeding", "0007_breeding_created_updated_by"),
        ("colony", "0013_strainline_default_project"),
    ]

    operations = [
        migrations.AddField(
            model_name="mouse",
            name="source_breeding",
            field=models.ForeignKey(
                blank=True,
                help_text="Breeding cage / mating this mouse was born from (when specific dam is unknown).",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="offspring_mice",
                to="breeding.breeding",
            ),
        ),
    ]
