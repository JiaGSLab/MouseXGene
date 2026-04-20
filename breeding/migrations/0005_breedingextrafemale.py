from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("colony", "0008_strainline_expected_loci_config"),
        ("breeding", "0004_litter_workflow_litterpup"),
    ]

    operations = [
        migrations.CreateModel(
            name="BreedingExtraFemale",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "breeding",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="extra_female_links",
                        to="breeding.breeding",
                    ),
                ),
                (
                    "mouse",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="extra_female_breedings",
                        to="colony.mouse",
                    ),
                ),
            ],
            options={"ordering": ("breeding", "mouse__mouse_uid")},
        ),
        migrations.AddConstraint(
            model_name="breedingextrafemale",
            constraint=models.UniqueConstraint(fields=("breeding", "mouse"), name="uq_breeding_extra_female"),
        ),
    ]
