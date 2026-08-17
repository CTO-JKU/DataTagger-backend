from django.db import migrations


def forwards_func(apps, schema_editor):
    MetadataTemplate = apps.get_model("metadata", "MetadataTemplate")
    MetadataTemplateField = apps.get_model("metadata", "MetadataTemplateField")

    metadata_templates = MetadataTemplate.objects.all()

    for metadata_template in metadata_templates:
        metadata_template_fields = MetadataTemplateField.objects.filter(
            metadata_template=metadata_template.pk,
        )

        count = 0
        for metadata_template_field in metadata_template_fields:
            metadata_template_field.order = count
            metadata_template_field.save()
            count += 1


def reverse_func(apps, schema_editor):
    MetadataTemplateField = apps.get_model("metadata", "MetadataTemplateField")
    MetadataTemplateField.objects.all().update(order=0)


class Migration(migrations.Migration):
    dependencies = [
        ("metadata", "0040_migrate_metadata_ordering"),
    ]

    operations = [
        migrations.RunPython(
            code=forwards_func,
            reverse_code=reverse_func,
        ),
    ]
