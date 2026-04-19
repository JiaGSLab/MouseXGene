# Litter workflow fields + LitterPup bridge model

import django.db.models.deletion
from django.db import migrations, models


def forwards_litter_status(apps, schema_editor):
    Litter = apps.get_model("breeding", "Litter")
    for lit in Litter.objects.all():
        if lit.is_archived:
            lit.litter_status = "archived"
        elif lit.wean_date:
            lit.litter_status = "weaned"
        else:
            lit.litter_status = "active"
        lit.save(update_fields=["litter_status"])


class Migration(migrations.Migration):

    dependencies = [
        ("breeding", "0003_breeding_archived_at_litter_archived_at_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="litter",
            name="female_count",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Optional count of female pups (can be derived from pup records when present).",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="litter",
            name="litter_status",
            field=models.CharField(
                choices=[
                    ("active", "Active"),
                    ("weaned", "Weaned"),
                    ("tail_tagged", "Tail tagged"),
                    ("ended", "Ended"),
                    ("archived", "Archived"),
                ],
                db_index=True,
                default="active",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="litter",
            name="male_count",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Optional count of male pups (can be derived from pup records when present).",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="litter",
            name="tail_tag_date",
            field=models.DateField(
                blank=True,
                help_text="Lab-wide tail-tag event date for this litter (optional).",
                null=True,
            ),
        ),
        migrations.RunPython(forwards_litter_status, migrations.RunPython.noop),
        migrations.CreateModel(
            name="LitterPup",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                ("sex", models.CharField(choices=[("M", "Male"), ("F", "Female"), ("U", "Unknown")], default="U", max_length=1)),
                ("ear_tag", models.CharField(blank=True, max_length=64)),
                ("toe_tag", models.CharField(blank=True, max_length=64)),
                ("coat_color", models.CharField(blank=True, max_length=64)),
                ("tail_tag_date", models.DateField(blank=True, null=True)),
                ("notes", models.TextField(blank=True)),
                (
                    "litter",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="pups", to="breeding.litter"),
                ),
                (
                    "mouse",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="litter_pup_origin",
                        to="colony.mouse",
                    ),
                ),
            ],
            options={
                "ordering": ("litter", "sort_order", "id"),
            },
        ),
    ]
