from django.db import migrations


def forwards_func(apps, schema_editor):
    from django.contrib.contenttypes.models import ContentType

    UploadsVersionFile = apps.get_model("uploads", "UploadsVersionFile")
    Metadata = apps.get_model("metadata", "Metadata")

    upload_version_file_content_type = ContentType.objects.get_for_model(UploadsVersionFile)

    Metadata.objects.filter(
        assigned_to_content_type=upload_version_file_content_type.pk,
    ).update(order=0)


class Migration(migrations.Migration):
    dependencies = [
        ("metadata", "0041_migrate_metadata_template_fields_ordering"),
    ]

    operations = [
        migrations.RunPython(
            code=forwards_func,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
