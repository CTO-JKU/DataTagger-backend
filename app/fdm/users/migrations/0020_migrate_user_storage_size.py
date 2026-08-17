from django.contrib.contenttypes.models import ContentType
from django.db import migrations

from fdm.core.helpers import get_metadata_value_file_size


def forwards_func(apps, schema_editor):
    User = apps.get_model("users", "User")
    Metadata = apps.get_model("metadata", "Metadata")
    UploadsVersionFile = apps.get_model("uploads", "UploadsVersionFile")
    uploads_version_file_content_type = ContentType.objects.get_for_model(UploadsVersionFile)

    for user in User.objects.all():
        total_size = 0
        file_ids = list(
            UploadsVersionFile.objects.filter(
                created_by=user.pk,
            ).values_list("id", flat=True),
        )

        if file_ids:
            metadata_values = Metadata.objects.filter(
                assigned_to_content_type=uploads_version_file_content_type.pk,
                assigned_to_object_id__in=file_ids,
                custom_key="FILE_SIZE",
            ).values_list("value", flat=True)

            for metadata_value in metadata_values:
                total_size += get_metadata_value_file_size(metadata_value)

        user.storage_size = total_size
        user.save(update_fields=["storage_size"])


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0019_user_storage_size"),
    ]

    operations = [
        migrations.RunPython(
            code=forwards_func,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
