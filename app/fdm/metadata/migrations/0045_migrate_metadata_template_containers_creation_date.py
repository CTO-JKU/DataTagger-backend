from django.db import migrations


def forwards_func(apps, schema_editor):
    MetadataTemplateContainer = apps.get_model("metadata", "MetadataTemplateContainer")
    MetadataTemplate = apps.get_model("metadata", "MetadataTemplate")

    metadata_template_containers = MetadataTemplateContainer.objects.all()

    for metadata_template_container in metadata_template_containers:
        first_metadata_template = (
            MetadataTemplate.objects.filter(
                metadata_template_container=metadata_template_container,
            )
            .order_by("creation_date")
            .first()
        )

        metadata_template_container.creation_date = first_metadata_template.creation_date
        metadata_template_container.save()


class Migration(migrations.Migration):
    dependencies = [
        ("metadata", "0044_migrate_uploads_version_file_metadata_ordering"),
    ]

    operations = [
        migrations.RunPython(
            code=forwards_func,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
