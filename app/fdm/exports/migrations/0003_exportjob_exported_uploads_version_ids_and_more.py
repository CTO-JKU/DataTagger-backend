import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("exports", "0002_exportjob_include_uploads_version_file_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="exportjob",
            name="exported_uploads_version_ids",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="exportjob",
            name="source_export_job",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="reused_export_jobs",
                to="exports.exportjob",
            ),
        ),
    ]
