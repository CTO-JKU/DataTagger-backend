import logging
from collections import Counter

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from fdm.core.rest.permissions import can_edit_in_folder
from fdm.metadata.helpers import check_empty_value, set_metadata, set_metadata_for_relation, validate_metadata
from fdm.metadata.models import Metadata, MetadataTemplate, MetadataTemplateField
from fdm.uploads.models import UploadsDataset, UploadsVersion, UploadsVersionFile

__all__ = [
    "create_uploads_version_with_new_file_for_dataset",
    "create_uploads_version_with_new_metadata_for_dataset",
    "get_bulk_fill_metadata_template_fields_context",
    "get_bulk_fill_skip_reason_payload",
    "get_bulk_fill_uploads_datasets",
    "submit_bulk_fill_metadata_template_fields",
]

logger = logging.getLogger(__name__)

BULK_FILL_SKIP_REASON_PENDING_TEMPLATE_UPGRADE = "PENDING_TEMPLATE_UPGRADE"
BULK_FILL_SKIP_REASON_NOT_IN_FOLDER_SCOPE = "NOT_IN_FOLDER_SCOPE"
BULK_FILL_SKIP_REASON_NO_INHERITED_TEMPLATE_FIELDS = "NO_INHERITED_TEMPLATE_FIELDS"
BULK_FILL_SKIP_REASON_STALE_BASE_VERSION = "STALE_BASE_VERSION"
BULK_FILL_SKIP_REASON_NO_CHANGES = "NO_CHANGES"

BULK_FILL_WARNING_DIFFERENT_DATASET_METADATA_TEMPLATES = "DIFFERENT_DATASET_METADATA_TEMPLATES"

BULK_FILL_SKIP_REASON_ORDER = [
    BULK_FILL_SKIP_REASON_PENDING_TEMPLATE_UPGRADE,
    BULK_FILL_SKIP_REASON_NOT_IN_FOLDER_SCOPE,
    BULK_FILL_SKIP_REASON_NO_INHERITED_TEMPLATE_FIELDS,
    BULK_FILL_SKIP_REASON_STALE_BASE_VERSION,
    BULK_FILL_SKIP_REASON_NO_CHANGES,
]


@transaction.atomic
def create_uploads_version_with_new_file_for_dataset(
    dataset: UploadsDataset | None = None,
    name: str | None = None,
    uploaded_file: any = None,
    uploaded_using_tus: bool = False,
    original_file_name: str | None = None,
    original_file_path: str | None = None,
) -> UploadsVersion:
    file = UploadsVersionFile(
        uploaded_file=uploaded_file,
        uploaded_using_tus=uploaded_using_tus,
    )
    file.save()

    file_name = original_file_name or uploaded_file.name
    if file_name:
        set_metadata(
            assigned_to_content_type=file.get_content_type(),
            assigned_to_object_id=file.pk,
            custom_key="ORIGINAL_FILE_NAME",
            value=file_name,
            read_only=True,
        )

    if original_file_path:
        set_metadata(
            assigned_to_content_type=file.get_content_type(),
            assigned_to_object_id=file.pk,
            custom_key="ORIGINAL_FILE_PATH",
            value=original_file_path,
            read_only=True,
        )

    if not dataset:
        dataset = UploadsDataset.objects.create(
            name=file_name,
        )

    latest_version = dataset.latest_version

    uploads_version = UploadsVersion.objects.create(
        version_file=file,
        dataset=dataset,
        name=name,
    )

    # If there's already at least one version in this dataset, apply all the metadata from that version to this new one
    if latest_version:
        set_metadata_for_relation(
            metadata_list=latest_version.metadata.all(),
            relation=uploads_version,
            set_pk_as_previous_version_field=True,
        )

        # Link metadata templates from the latest version to the new version
        uploads_version.metadata_templates.set(
            [metadata_template for metadata_template in latest_version.metadata_templates.all()],
        )

    if dataset.is_published() and not uploads_version.is_published():
        uploads_version.publish()

    from fdm.file_parser.tasks import dispatch_uploaded_file_processing, prepare_uploaded_file_processing

    prepare_uploaded_file_processing(uploads_version.version_file)
    transaction.on_commit(lambda: dispatch_uploaded_file_processing(str(uploads_version.version_file.pk)))

    return uploads_version


def create_uploads_version_with_new_metadata_for_dataset(
    dataset: UploadsDataset | None = None,
    name: str | None = None,
    metadata_list: list[dict | Metadata] = None,
    link_metadata_templates: list[dict | MetadataTemplate] = None,
    retain_existing_metadata: bool = False,
    link_currently_active_metadata_templates: bool = False,
) -> UploadsVersion:
    if dataset.locked and not dataset.is_locked_by_myself():
        raise PermissionDenied(_("You must not edit an element which has been locked by another user."))

    # There must be at least one uploads version in existence because of a file upload.
    # TODO: In the future it should be possible to have versions without files. We'll need to change this then.
    if not dataset.latest_version:
        raise PermissionDenied(_("You must not create a new uploads version on an empty uploads dataset."))

    latest_version = dataset.latest_version
    latest_version_metadata_list = latest_version.metadata.order_by(
        "order",
        "custom_key",
    )

    # TODO: In the future it should be possible to have versions without files. We'll need to change this then.
    if not latest_version.version_file:
        raise PermissionDenied(
            _("You must not create a new uploads version on an uploads dataset without a single file attached to it."),
        )

    uploads_version = UploadsVersion.objects.create(
        version_file=latest_version.version_file,
        dataset=dataset,
        name=name,
    )

    if retain_existing_metadata and latest_version_metadata_list:
        set_metadata_for_relation(
            metadata_list=latest_version_metadata_list,
            relation=uploads_version,
        )

    if metadata_list is not None:

        def get_metadata_template_field_id(metadata: dict | Metadata | MetadataTemplateField):
            metadata_template_field_id = None
            if isinstance(metadata, Metadata):
                metadata_template_field_id = metadata.metadata_template_field_id
            elif isinstance(metadata, MetadataTemplateField):
                metadata_template_field_id = metadata.pk
            elif isinstance(metadata, dict):
                metadata_template_field = metadata.get("metadata_template_field")
                if isinstance(metadata_template_field, MetadataTemplateField):
                    metadata_template_field_id = metadata_template_field.pk
                else:
                    metadata_template_field_id = metadata_template_field

            return str(metadata_template_field_id) if metadata_template_field_id is not None else None

        existing_metadata_template_field_ids = set()
        if retain_existing_metadata:
            existing_metadata_template_field_ids = {
                str(metadata_template_field_id)
                for metadata_template_field_id in uploads_version.metadata.exclude(
                    metadata_template_field__isnull=True,
                ).values_list(
                    "metadata_template_field_id",
                    flat=True,
                )
            }

        filtered_metadata_list = []
        processed_metadata_template_field_ids = set()
        for metadata in metadata_list:
            metadata_template_field_id = get_metadata_template_field_id(metadata)
            if metadata_template_field_id and (
                metadata_template_field_id in existing_metadata_template_field_ids
                or metadata_template_field_id in processed_metadata_template_field_ids
            ):
                continue

            if metadata_template_field_id:
                processed_metadata_template_field_ids.add(metadata_template_field_id)

            filtered_metadata_list.append(metadata)

        set_metadata_for_relation(
            metadata_list=filtered_metadata_list,
            relation=uploads_version,
            retain_existing_metadata=True,
            order_offset=(
                max(metadata.order or 0 for metadata in latest_version_metadata_list) + 1
                if retain_existing_metadata and latest_version_metadata_list
                else 0
            ),
        )

    # Overwrite linked metadata templates (e.g. from an upgrade)
    if link_metadata_templates is not None:
        uploads_version.metadata_templates.set(
            list({metadata_template for metadata_template in link_metadata_templates}),
        )
    else:
        # Link metadata templates from the latest version to the new version
        uploads_version.metadata_templates.set(
            list({metadata_template for metadata_template in latest_version.metadata_templates.all()}),
        )

        # Link currently active metadata templates to the new version
        if link_currently_active_metadata_templates:
            uploads_version.link_currently_active_metadata_templates()

    if dataset.is_published() and not uploads_version.is_published():
        uploads_version.publish()

    return uploads_version


def get_bulk_fill_skip_reason_payload(skip_reason_counts: Counter) -> list[dict]:
    return [
        {
            "code": code,
            "count": skip_reason_counts[code],
        }
        for code in BULK_FILL_SKIP_REASON_ORDER
        if skip_reason_counts[code]
    ]


def _get_common_dataset_metadata_template_fields(uploads_datasets: list[UploadsDataset]) -> tuple[list, list[str]]:
    if not uploads_datasets:
        return [], []

    active_template_ids = {dataset.metadata_template_id for dataset in uploads_datasets}
    if len(active_template_ids) != 1:
        return [], [BULK_FILL_WARNING_DIFFERENT_DATASET_METADATA_TEMPLATES]

    active_template_id = next(iter(active_template_ids))
    if not active_template_id:
        return [], []

    for dataset in uploads_datasets:
        if (
            not dataset.latest_version
            or not dataset.latest_version.metadata_templates.filter(pk=active_template_id).exists()
        ):
            return [], [BULK_FILL_WARNING_DIFFERENT_DATASET_METADATA_TEMPLATES]

    metadata_template = uploads_datasets[0].metadata_template
    return list(
        metadata_template.metadata_template_fields.order_by(
            "order",
            "custom_key",
        ),
    ), []


def _has_usable_dataset_metadata_template(dataset: UploadsDataset) -> bool:
    return bool(
        dataset.metadata_template_id
        and dataset.latest_version
        and dataset.latest_version.metadata_templates.filter(pk=dataset.metadata_template_id).exists(),
    )


def get_bulk_fill_uploads_datasets(uploads_dataset_pks: list, user) -> list[UploadsDataset]:
    uploads_datasets = []
    seen_dataset_pks = set()

    for uploads_dataset_pk in uploads_dataset_pks:
        dataset_pk = str(uploads_dataset_pk)
        if dataset_pk in seen_dataset_pks:
            continue

        try:
            uploads_dataset = UploadsDataset.objects.get(pk=dataset_pk)
        except UploadsDataset.DoesNotExist:
            raise

        seen_dataset_pks.add(dataset_pk)
        uploads_datasets.append(uploads_dataset)

    if any(
        [
            (
                dataset.is_published()
                and not can_edit_in_folder(
                    user=user,
                    folder_pk=dataset.folder.pk if dataset.folder else None,
                )
            )
            or (not dataset.is_published() and dataset.created_by != user)
            for dataset in uploads_datasets
        ],
    ):
        raise PermissionDenied(
            _("You must not edit any published uploads datasets in a folder you haven't got the permission to."),
        )

    return uploads_datasets


def _get_bulk_fill_field_payload(
    metadata_template_field: MetadataTemplateField,
    values: list,
) -> dict:
    various_inputs = any(value != values[0] for value in values[1:]) if values else False
    return {
        "pk": str(metadata_template_field.pk),
        "metadata_template": metadata_template_field.metadata_template,
        "field": metadata_template_field.field,
        "custom_key": metadata_template_field.custom_key,
        "field_type": metadata_template_field.field_type,
        "value": None if various_inputs else (values[0] if values else None),
        "mandatory": metadata_template_field.mandatory,
        "config": metadata_template_field.config,
        "previous_version_field": (
            str(metadata_template_field.previous_version_field.pk)
            if metadata_template_field.previous_version_field
            else None
        ),
        "order": metadata_template_field.order,
        "various_inputs": various_inputs,
    }


def get_bulk_fill_metadata_template_fields_context(uploads_datasets: list[UploadsDataset]) -> dict:
    skip_reason_counts = Counter()
    eligible_uploads_datasets = []
    anchor_folder_id = uploads_datasets[0].folder_id if uploads_datasets else None

    for dataset in uploads_datasets:
        if dataset.folder_id != anchor_folder_id:
            skip_reason_counts[BULK_FILL_SKIP_REASON_NOT_IN_FOLDER_SCOPE] += 1
            continue

        if dataset.is_latest_version_inherited_metadata_upgradeable():
            skip_reason_counts[BULK_FILL_SKIP_REASON_PENDING_TEMPLATE_UPGRADE] += 1
            continue

        if (
            not dataset.latest_version
            or not dataset.get_inherited_metadata_template_fields()
            and not _has_usable_dataset_metadata_template(dataset)
        ):
            skip_reason_counts[BULK_FILL_SKIP_REASON_NO_INHERITED_TEMPLATE_FIELDS] += 1
            continue

        eligible_uploads_datasets.append(dataset)

    inherited_metadata_template_fields = (
        eligible_uploads_datasets[0].get_inherited_metadata_template_fields() if eligible_uploads_datasets else []
    )
    dataset_metadata_template_fields, warnings = _get_common_dataset_metadata_template_fields(eligible_uploads_datasets)
    metadata_template_fields = inherited_metadata_template_fields + dataset_metadata_template_fields
    metadata_template_field_ids = [metadata_template_field.pk for metadata_template_field in metadata_template_fields]

    fields_payload = []
    for metadata_template_field in metadata_template_fields:
        values = []
        for dataset in eligible_uploads_datasets:
            metadata = (
                dataset.latest_version.metadata.filter(
                    metadata_template_field_id__in=metadata_template_field_ids,
                )
                .filter(
                    metadata_template_field=metadata_template_field,
                )
                .first()
            )
            values.append(metadata.get_value() if metadata else None)

        fields_payload.append(
            _get_bulk_fill_field_payload(
                metadata_template_field=metadata_template_field,
                values=values,
            ),
        )

    return {
        "eligible_uploads_datasets": eligible_uploads_datasets,
        "eligible_uploads_datasets_payload": [
            {
                "pk": str(dataset.pk),
                "display_name": dataset.display_name,
                "base_version_pk": str(dataset.latest_version.pk),
            }
            for dataset in eligible_uploads_datasets
        ],
        "field_map": {
            str(metadata_template_field.pk): metadata_template_field
            for metadata_template_field in metadata_template_fields
        },
        "fields_payload": fields_payload,
        "skip_reason_counts": skip_reason_counts,
        "warnings": warnings,
    }


def _build_bulk_fill_metadata_snapshot(
    dataset: UploadsDataset,
    selected_fields: dict[str, dict],
) -> tuple[list[dict], bool]:
    latest_metadata = list(
        dataset.latest_version.metadata.order_by(
            "order",
            "custom_key",
        ),
    )
    metadata_snapshot = []
    metadata_by_template_field_id = {}
    changed = False

    for metadata in latest_metadata:
        snapshot_item = {
            "field": metadata.field,
            "custom_key": metadata.custom_key,
            "field_type": metadata.field_type,
            "value": metadata.get_value(),
            "config": metadata.config,
            "metadata_template_field": metadata.metadata_template_field,
            "previous_version_field": metadata.pk,
            "order": metadata.order,
        }
        metadata_snapshot.append(snapshot_item)

        if metadata.metadata_template_field_id:
            metadata_by_template_field_id[str(metadata.metadata_template_field_id)] = snapshot_item

    next_order = max([metadata.order or 0 for metadata in latest_metadata], default=-1) + 1

    for metadata_template_field_pk, payload in selected_fields.items():
        metadata_template_field = payload["metadata_template_field"]
        value = payload["value"]
        current_metadata = metadata_by_template_field_id.get(metadata_template_field_pk)

        if current_metadata:
            if check_empty_value(current_metadata["value"]) == check_empty_value(value):
                continue

            current_metadata["field"] = metadata_template_field.field
            current_metadata["custom_key"] = metadata_template_field.custom_key
            current_metadata["field_type"] = metadata_template_field.field_type
            current_metadata["value"] = value
            current_metadata["config"] = metadata_template_field.config
            current_metadata["metadata_template_field"] = metadata_template_field
            changed = True
            continue

        metadata_snapshot.append(
            {
                "field": metadata_template_field.field,
                "custom_key": metadata_template_field.custom_key,
                "field_type": metadata_template_field.field_type,
                "value": value,
                "config": metadata_template_field.config,
                "metadata_template_field": metadata_template_field,
                "previous_version_field": None,
                "order": next_order,
            },
        )
        next_order += 1
        changed = True

    metadata_snapshot.sort(
        key=lambda metadata: (
            metadata.get("order", 0) if metadata.get("order", 0) is not None else 0,
            metadata.get("custom_key") or "",
        ),
    )
    for order, metadata in enumerate(metadata_snapshot):
        metadata["order"] = order

    return metadata_snapshot, changed


def submit_bulk_fill_metadata_template_fields(
    uploads_datasets: list[UploadsDataset],
    base_version_map: dict[str, str],
    field_payloads: list[dict],
) -> dict:
    skip_reason_counts = Counter()
    anchor_folder_id = uploads_datasets[0].folder_id if uploads_datasets else None
    metadata_template_fields = []

    for dataset in uploads_datasets:
        if dataset.folder_id == anchor_folder_id and dataset.get_inherited_metadata_template_fields():
            metadata_template_fields = dataset.get_inherited_metadata_template_fields()
            break

    eligible_uploads_datasets = [
        dataset
        for dataset in uploads_datasets
        if dataset.folder_id == anchor_folder_id
        and dataset.latest_version
        and (dataset.get_inherited_metadata_template_fields() or _has_usable_dataset_metadata_template(dataset))
        and not dataset.is_latest_version_inherited_metadata_upgradeable()
    ]
    dataset_metadata_template_fields, _warnings = _get_common_dataset_metadata_template_fields(
        eligible_uploads_datasets
    )
    metadata_template_fields += dataset_metadata_template_fields

    field_map = {
        str(metadata_template_field.pk): metadata_template_field for metadata_template_field in metadata_template_fields
    }

    selected_fields = {}
    for field_payload in field_payloads:
        metadata_template_field_pk = str(field_payload["metadata_template_field_pk"])
        if metadata_template_field_pk not in field_map:
            raise ValidationError(_("You must not edit metadata template fields outside the common bulk fill scope."))

        selected_fields[metadata_template_field_pk] = {
            "metadata_template_field": field_map[metadata_template_field_pk],
            "value": field_payload["value"],
        }

    updated_uploads_datasets_count = 0
    for dataset in uploads_datasets:
        dataset_pk = str(dataset.pk)

        if dataset.folder_id != anchor_folder_id:
            skip_reason_counts[BULK_FILL_SKIP_REASON_NOT_IN_FOLDER_SCOPE] += 1
            continue

        if (
            not dataset.latest_version
            or not dataset.get_inherited_metadata_template_fields()
            and not _has_usable_dataset_metadata_template(dataset)
        ):
            skip_reason_counts[BULK_FILL_SKIP_REASON_NO_INHERITED_TEMPLATE_FIELDS] += 1
            continue

        if base_version_map.get(dataset_pk) != str(dataset.latest_version.pk):
            skip_reason_counts[BULK_FILL_SKIP_REASON_STALE_BASE_VERSION] += 1
            continue

        if dataset.is_latest_version_inherited_metadata_upgradeable():
            skip_reason_counts[BULK_FILL_SKIP_REASON_PENDING_TEMPLATE_UPGRADE] += 1
            continue

        metadata_snapshot, changed = _build_bulk_fill_metadata_snapshot(
            dataset=dataset,
            selected_fields=selected_fields,
        )
        if not changed:
            skip_reason_counts[BULK_FILL_SKIP_REASON_NO_CHANGES] += 1
            continue

        validate_metadata(metadata_snapshot)
        create_uploads_version_with_new_metadata_for_dataset(
            dataset=dataset,
            name=str(_("Bulk filled metadata template fields")),
            metadata_list=metadata_snapshot,
            link_metadata_templates=list(dataset.latest_version.metadata_templates.all()),
            retain_existing_metadata=False,
        )
        updated_uploads_datasets_count += 1

    return {
        "updated_uploads_datasets_count": updated_uploads_datasets_count,
        "skipped_uploads_datasets_count": sum(skip_reason_counts.values()),
        "skip_reasons": get_bulk_fill_skip_reason_payload(skip_reason_counts),
    }
