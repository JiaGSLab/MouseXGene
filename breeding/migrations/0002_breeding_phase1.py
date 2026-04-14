from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("colony", "0001_initial"),
        ("breeding", "0001_initial"),
    ]

    operations = [
        migrations.RenameField(
            model_name="breeding",
            old_name="code",
            new_name="breeding_code",
        ),
        migrations.RenameField(
            model_name="breeding",
            old_name="female",
            new_name="female_1",
        ),
        migrations.RenameField(
            model_name="breeding",
            old_name="end_date",
            new_name="expected_birth_date",
        ),
        migrations.AddField(
            model_name="breeding",
            name="breeding_type",
            field=models.CharField(
                choices=[("pair", "Pair"), ("trio", "Trio")],
                default="pair",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="breeding",
            name="cage",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="breedings",
                to="colony.cage",
            ),
        ),
        migrations.AddField(
            model_name="breeding",
            name="female_2",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="maternal_breedings_secondary",
                to="colony.mouse",
            ),
        ),
        migrations.AddField(
            model_name="breeding",
            name="notes",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="breeding",
            name="plug_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="breeding",
            name="status",
            field=models.CharField(
                choices=[
                    ("setup", "Setup"),
                    ("plugged", "Plugged"),
                    ("pregnant", "Pregnant"),
                    ("littered", "Littered"),
                    ("closed", "Closed"),
                ],
                default="setup",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="breeding",
            name="female_1",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="maternal_breedings_primary",
                to="colony.mouse",
            ),
        ),
        migrations.AlterModelOptions(
            name="breeding",
            options={"ordering": ("-start_date", "breeding_code")},
        ),
        migrations.RenameField(
            model_name="litter",
            old_name="litter_date",
            new_name="birth_date",
        ),
        migrations.RenameField(
            model_name="litter",
            old_name="size",
            new_name="total_born",
        ),
        migrations.AddField(
            model_name="litter",
            name="alive_count",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="litter",
            name="dead_count",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="litter",
            name="litter_code",
            field=models.CharField(blank=True, max_length=64, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="litter",
            name="wean_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="litter",
            name="total_born",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AlterModelOptions(
            name="litter",
            options={"ordering": ("-birth_date", "litter_code")},
        ),
    ]
