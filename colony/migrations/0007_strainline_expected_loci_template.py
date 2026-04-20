from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("colony", "0006_mousegenotypecomponent_locus_chromosome_and_class"),
    ]

    operations = [
        migrations.AddField(
            model_name="strainline",
            name="expected_loci_template",
            field=models.TextField(blank=True),
        ),
    ]
