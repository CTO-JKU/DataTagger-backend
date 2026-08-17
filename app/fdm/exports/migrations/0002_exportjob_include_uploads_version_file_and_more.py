from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("exports", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="exportjob",
            name="include_uploads_version_file",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="exportjob",
            name="include_uploads_version_file_metadata",
            field=models.BooleanField(default=True),
        ),
    ]
