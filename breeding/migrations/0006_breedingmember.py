from django.db import migrations, models
import django.db.models.deletion


def forwards_sync_members(apps, schema_editor):
    Breeding = apps.get_model("breeding", "Breeding")
    BreedingExtraFemale = apps.get_model("breeding", "BreedingExtraFemale")
    BreedingMember = apps.get_model("breeding", "BreedingMember")
    for breeding in Breeding.objects.all().iterator():
        to_create = []
        if breeding.male_id:
            to_create.append(BreedingMember(breeding_id=breeding.id, mouse_id=breeding.male_id, role="sire", sort_order=1))
        if breeding.female_1_id:
            to_create.append(BreedingMember(breeding_id=breeding.id, mouse_id=breeding.female_1_id, role="dam", sort_order=1))
        if breeding.female_2_id:
            to_create.append(BreedingMember(breeding_id=breeding.id, mouse_id=breeding.female_2_id, role="dam", sort_order=2))
        extras = list(
            BreedingExtraFemale.objects.filter(breeding_id=breeding.id)
            .select_related("mouse")
            .order_by("mouse__mouse_uid")
        )
        for idx, row in enumerate(extras, start=3):
            to_create.append(BreedingMember(breeding_id=breeding.id, mouse_id=row.mouse_id, role="dam", sort_order=idx))
        existing_mouse_ids = set(
            BreedingMember.objects.filter(breeding_id=breeding.id).values_list("mouse_id", flat=True)
        )
        BreedingMember.objects.bulk_create(
            [m for m in to_create if m.mouse_id not in existing_mouse_ids],
            ignore_conflicts=True,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("breeding", "0005_breedingextrafemale"),
    ]

    operations = [
        migrations.CreateModel(
            name="BreedingMember",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "role",
                    models.CharField(
                        choices=[("sire", "Sire"), ("dam", "Dam")],
                        max_length=12,
                    ),
                ),
                ("sort_order", models.PositiveSmallIntegerField(default=1)),
                (
                    "breeding",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="breeding_members",
                        to="breeding.breeding",
                    ),
                ),
                (
                    "mouse",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="breeding_memberships",
                        to="colony.mouse",
                    ),
                ),
            ],
            options={"ordering": ("breeding", "role", "sort_order", "mouse__mouse_uid")},
        ),
        migrations.AddConstraint(
            model_name="breedingmember",
            constraint=models.UniqueConstraint(fields=("breeding", "mouse"), name="uq_breeding_member_mouse"),
        ),
        migrations.RunPython(forwards_sync_members, migrations.RunPython.noop),
    ]
