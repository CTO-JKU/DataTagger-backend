# Generated manually for SITUMFDM-825

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("rest_framework_tus", "0009_alter_upload_uploaded_file"),
    ]

    operations = [
        migrations.AlterField(
            model_name="upload",
            name="upload_metadata",
            field=models.JSONField(default=dict),
        ),
    ]
