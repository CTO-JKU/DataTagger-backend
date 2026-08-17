from django.db import migrations, models


def schedule_existing_availability_refreshes(apps, schema_editor):
    uploads_dataset = apps.get_model("uploads", "UploadsDataset")
    uploads_dataset.objects.update(metadata_upgrade_availability_status="SCHEDULED")


class Migration(migrations.Migration):
    dependencies = [
        ("uploads", "0042_uploadsversionfile_processing_lease"),
    ]

    operations = [
        migrations.AddField(
            model_name="uploadsdataset",
            name="metadata_upgrade_availability_status",
            field=models.CharField(
                choices=[
                    ("SCHEDULED", "Scheduled"),
                    ("IN_PROGRESS", "In progress"),
                    ("ERROR", "Error"),
                    ("FINISHED", "Finished"),
                ],
                default="FINISHED",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="uploadsdataset",
            name="metadata_template_processing_heartbeat_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="uploadsdataset",
            name="metadata_template_processing_lease_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="uploadsdataset",
            name="metadata_template_processing_lease_token",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.RunPython(
            schedule_existing_availability_refreshes,
            migrations.RunPython.noop,
        ),
    ]
