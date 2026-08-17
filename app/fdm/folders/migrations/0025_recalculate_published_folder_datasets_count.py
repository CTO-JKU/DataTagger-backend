import logging

from django.db import migrations

logger = logging.getLogger(__name__)


def forwards_func(apps, schema_editor):
    Folder = apps.get_model("folders", "Folder")
    UploadsDataset = apps.get_model("uploads", "UploadsDataset")

    folders = Folder.objects.all()

    for folder in folders:
        datasets_count = UploadsDataset.objects.filter(
            folder=folder,
            publication_date__isnull=False,
        ).count()

        folder.datasets_count = datasets_count
        folder.save()


class Migration(migrations.Migration):
    dependencies = [
        ("folders", "0024_migrate_folder_metadata_templates_count"),
        ("uploads", "0040_dedupe_uploads_version_template_metadata"),
    ]

    operations = [
        migrations.RunPython(
            code=forwards_func,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
