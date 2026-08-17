from django.db import migrations


def forwards_func(apps, schema_editor):
    MetadataTemplateContainer = apps.get_model("metadata", "MetadataTemplateContainer")
    MetadataTemplate = apps.get_model("metadata", "MetadataTemplate")

    metadata_templates = MetadataTemplate.objects.filter(metadata_template_container__isnull=True)

    for metadata_template in metadata_templates:
        name = metadata_template.name

        metadata_template.metadata_template_container = MetadataTemplateContainer.objects.create(
            name=name,
            assigned_to_content_type=metadata_template.assigned_to_content_type,
            assigned_to_object_id=metadata_template.assigned_to_object_id,
            created_by=metadata_template.created_by,
            last_modified_by=metadata_template.last_modified_by,
        )
        metadata_template.name = None
        metadata_template.save()


def reverse_func(apps, schema_editor):
    MetadataTemplateContainer = apps.get_model("metadata", "MetadataTemplateContainer")
    MetadataTemplate = apps.get_model("metadata", "MetadataTemplate")

    metadata_templates = MetadataTemplate.objects.all()

    for metadata_template in metadata_templates:
        metadata_template.name = metadata_template.metadata_template_container.name
        metadata_template.assigned_to_content_type = (
            metadata_template.metadata_template_container.assigned_to_content_type
        )
        metadata_template.assigned_to_object_id = metadata_template.metadata_template_container.assigned_to_object_id
        metadata_template.save()

    MetadataTemplateContainer.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("metadata", "0031_metadatatemplatecontainer_and_more"),
    ]

    operations = [
        migrations.RunPython(
            code=forwards_func,
            reverse_code=reverse_func,
        ),
    ]
