from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0007_project_created_updated_by"),
        ("colony", "0012_strainline_category_background_presets"),
    ]

    operations = [
        migrations.AddField(
            model_name="strainline",
            name="default_project",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional default project when creating mice on this strain line.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="default_for_strain_lines",
                to="core.project",
            ),
        ),
    ]
