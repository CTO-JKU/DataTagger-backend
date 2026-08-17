from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("uploads", "0041_uploadsdataset_deletable_until_date"),
    ]

    operations = [
        migrations.AddField(
            model_name="uploadsversionfile",
            name="processing_heartbeat_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="uploadsversionfile",
            name="processing_lease_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="uploadsversionfile",
            name="processing_lease_token",
            field=models.UUIDField(blank=True, null=True),
        ),
    ]
