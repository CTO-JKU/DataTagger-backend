import logging

from django.db import migrations
from django.db.models import Count

logger = logging.getLogger(__name__)


def forwards_func(apps, schema_editor):
    Metadata = apps.get_model("metadata", "Metadata")

    duplicated_relations = (
        Metadata.objects.filter(
            assigned_to_content_type__app_label="uploads",
            assigned_to_content_type__model="uploadsversion",
            metadata_template_field__isnull=False,
        )
        .values(
            "assigned_to_object_id",
            "metadata_template_field_id",
        )
        .annotate(total=Count("id"))
        .filter(total__gt=1)
    )

    removed_duplicates = 0
    affected_uploads_versions = set()

    for duplicate in duplicated_relations.iterator():
        metadata_instances = Metadata.objects.filter(
            assigned_to_content_type__app_label="uploads",
            assigned_to_content_type__model="uploadsversion",
            assigned_to_object_id=duplicate["assigned_to_object_id"],
            metadata_template_field_id=duplicate["metadata_template_field_id"],
        ).order_by(
            "order",
            "creation_date",
            "pk",
        )

        keep_instance = metadata_instances.first()
        if not keep_instance:
            continue

        delete_queryset = metadata_instances.exclude(pk=keep_instance.pk)
        deleted_count = delete_queryset.count()
        if deleted_count:
            delete_queryset.delete()
            removed_duplicates += deleted_count
            affected_uploads_versions.add(str(duplicate["assigned_to_object_id"]))

    logger.info(
        "Migration 0040_dedupe_uploads_version_template_metadata deduplicated %s metadata rows across %s uploads "
        "versions.",
        removed_duplicates,
        len(affected_uploads_versions),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("uploads", "0039_uploadsdataset_metadata_upgrade_status"),
        ("metadata", "0045_migrate_metadata_template_containers_creation_date"),
    ]

    operations = [
        migrations.RunPython(
            code=forwards_func,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
