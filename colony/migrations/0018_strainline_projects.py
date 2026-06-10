from django.db import migrations, models


def backfill_strain_line_projects(apps, schema_editor):
    StrainLine = apps.get_model("colony", "StrainLine")
    Mouse = apps.get_model("colony", "Mouse")

    for line in StrainLine.objects.all().iterator(chunk_size=100):
        project_ids = set()
        if line.default_project_id:
            project_ids.add(line.default_project_id)
        project_ids.update(
            Mouse.objects.filter(strain_line_id=line.pk)
            .exclude(project_id__isnull=True)
            .values_list("project_id", flat=True)
            .distinct()
        )
        if project_ids:
            line.projects.add(*project_ids)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0007_project_created_updated_by"),
        ("colony", "0017_mouse_colony_mouse_status_proj_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="strainline",
            name="projects",
            field=models.ManyToManyField(
                blank=True,
                help_text="Projects this strain line belongs to or can be used in.",
                related_name="strain_lines",
                to="core.project",
            ),
        ),
        migrations.RunPython(backfill_strain_line_projects, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="strainline",
            name="default_project",
        ),
    ]
