from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("colony", "0005_mouse_project_required_legacy_imported"),
    ]

    operations = [
        migrations.AddField(
            model_name="mousegenotypecomponent",
            name="chromosome_type",
            field=models.CharField(
                choices=[
                    ("autosomal", "Autosomal"),
                    ("x_linked", "X-linked"),
                    ("y_linked", "Y-linked"),
                    ("unknown", "Unknown"),
                ],
                default="unknown",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="mousegenotypecomponent",
            name="locus_name",
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.AddField(
            model_name="mousegenotypecomponent",
            name="zygosity_class",
            field=models.CharField(
                choices=[
                    ("wt", "WT"),
                    ("het", "Heterozygous"),
                    ("hom", "Homozygous"),
                    ("hemizygous", "Hemizygous"),
                    ("unknown", "Unknown"),
                ],
                default="unknown",
                max_length=16,
            ),
        ),
    ]
