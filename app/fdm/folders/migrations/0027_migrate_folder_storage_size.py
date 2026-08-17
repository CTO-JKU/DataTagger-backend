from django.contrib.contenttypes.models import ContentType
from django.db import migrations

from fdm.core.helpers import get_metadata_value_file_size


def forwards_func(apps, schema_editor):
    Folder = apps.get_model("folders", "Folder")
    Metadata = apps.get_model("metadata", "Metadata")
    UploadsVersion = apps.get_model("uploads", "UploadsVersion")
    UploadsVersionFile = apps.get_model("uploads", "UploadsVersionFile")
    uploads_version_file_content_type = ContentType.objects.get_for_model(UploadsVersionFile)

    for folder in Folder.objects.all():
        total_size = 0
        file_ids = list(
            UploadsVersion.objects.filter(
                dataset__folder=folder.pk,
            ).values_list("version_file_id", flat=True),
        )

        if file_ids:
            metadata_values = Metadata.objects.filter(
                assigned_to_content_type=uploads_version_file_content_type.pk,
                assigned_to_object_id__in=file_ids,
                custom_key="FILE_SIZE",
            ).values_list("value", flat=True)

            for metadata_value in metadata_values:
                total_size += get_metadata_value_file_size(metadata_value)

        # We must use .update() to avoid running into a lock
        Folder.objects.filter(pk=folder.pk).update(storage_size=total_size)


class Migration(migrations.Migration):
    dependencies = [
        ("folders", "0026_folder_storage_size"),
    ]

    operations = [
        migrations.RunPython(
            code=forwards_func,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
