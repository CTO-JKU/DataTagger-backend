from django.db import migrations


def forwards_func(apps, schema_editor):
    from django.contrib.contenttypes.models import ContentType

    UploadsVersion = apps.get_model("uploads", "UploadsVersion")
    Metadata = apps.get_model("metadata", "Metadata")

    upload_version_content_type = ContentType.objects.get_for_model(UploadsVersion)

    upload_versions = UploadsVersion.objects.all()

    for upload_version in upload_versions:
        metadata_list = Metadata.objects.filter(
            assigned_to_content_type=upload_version_content_type.pk,
            assigned_to_object_id=upload_version.pk,
        )

        count = 0
        for metadata in metadata_list:
            metadata.order = count
            metadata.save()
            count += 1


def reverse_func(apps, schema_editor):
    from django.contrib.contenttypes.models import ContentType

    UploadsVersion = apps.get_model("uploads", "UploadsVersion")
    Metadata = apps.get_model("metadata", "Metadata")

    upload_version_content_type = ContentType.objects.get_for_model(UploadsVersion)

    Metadata.objects.filter(
        assigned_to_content_type=upload_version_content_type.pk,
    ).update(order=0)


class Migration(migrations.Migration):
    dependencies = [
        ("metadata", "0039_alter_metadata_options_and_more"),
    ]

    operations = [
        migrations.RunPython(
            code=forwards_func,
            reverse_code=reverse_func,
        ),
    ]
