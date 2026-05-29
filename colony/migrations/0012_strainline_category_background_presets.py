from django.db import migrations, models


CATEGORY_MAP = {
    "cre": "cre_driver",
    "creERT2": "cre_driver",
    "flox": "floxed_allele",
    "ko": "knockout",
    "ki": "knock_in",
    "reporter": "reporter",
    "transgene": "compound_strain",
    "other": "compound_strain",
}

PRESET_CATEGORIES = {
    "wild_type",
    "inbred_strain",
    "cre_driver",
    "reporter",
    "floxed_allele",
    "knockout",
    "knock_in",
    "compound_strain",
}

PRESET_BACKGROUNDS = {
    "c57bl_6j",
    "balb_c",
    "balb_cj",
    "nod_scid",
    "nsg",
}


def _normalize_background_key(raw: str) -> str:
    return (raw or "").strip().lower().replace(" ", "").replace("_", "").replace("/", "")


BACKGROUND_ALIASES = {
    "c57bl6j": "c57bl_6j",
    "c57blj": "c57bl_6j",
    "c57bl6": "c57bl_6j",
    "balbc": "balb_c",
    "balbcj": "balb_cj",
    "nodscid": "nod_scid",
}


def forwards(apps, schema_editor):
    StrainLine = apps.get_model("colony", "StrainLine")
    for line in StrainLine.objects.all().only("id", "category", "background"):
        changed = False
        cat = (line.category or "").strip()
        if cat in CATEGORY_MAP:
            line.category = CATEGORY_MAP[cat]
            changed = True
        bg = (line.background or "").strip()
        if not bg:
            line.background = "c57bl_6j"
            changed = True
        else:
            norm = _normalize_background_key(bg)
            preset = BACKGROUND_ALIASES.get(norm)
            if preset:
                line.background = preset
                changed = True
            elif norm in {_normalize_background_key(p) for p in PRESET_BACKGROUNDS}:
                for p in PRESET_BACKGROUNDS:
                    if _normalize_background_key(p) == norm:
                        line.background = p
                        changed = True
                        break
        if changed:
            line.save(update_fields=["category", "background"])


def backwards(apps, schema_editor):
    StrainLine = apps.get_model("colony", "StrainLine")
    reverse_cat = {
        "cre_driver": "cre",
        "floxed_allele": "flox",
        "knockout": "ko",
        "knock_in": "ki",
        "reporter": "reporter",
        "compound_strain": "other",
        "wild_type": "other",
        "inbred_strain": "other",
    }
    for line in StrainLine.objects.all().only("id", "category"):
        if line.category in reverse_cat:
            line.category = reverse_cat[line.category]
            line.save(update_fields=["category"])


class Migration(migrations.Migration):
    dependencies = [
        ("colony", "0011_strainlinedocument"),
    ]

    operations = [
        migrations.AlterField(
            model_name="strainline",
            name="category",
            field=models.CharField(
                choices=[
                    ("wild_type", "Wild type"),
                    ("inbred_strain", "Inbred strain"),
                    ("cre_driver", "Cre driver"),
                    ("reporter", "Reporter"),
                    ("floxed_allele", "Floxed allele"),
                    ("knockout", "Knockout"),
                    ("knock_in", "Knock-in"),
                    ("compound_strain", "Compound strain"),
                ],
                default="compound_strain",
                max_length=48,
            ),
        ),
        migrations.AlterField(
            model_name="strainline",
            name="background",
            field=models.CharField(
                blank=True,
                default="c57bl_6j",
                max_length=128,
            ),
        ),
        migrations.RunPython(forwards, backwards),
    ]
