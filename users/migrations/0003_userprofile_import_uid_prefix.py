from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0002_userprofile_role"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="import_uid_prefix",
            field=models.CharField(
                blank=True,
                help_text="Optional. Used when you enable “prefix my IDs” on cage/mouse import "
                "(e.g. JG → JG-M001). Keeps numeric IDs unique across people.",
                max_length=16,
            ),
        ),
    ]
