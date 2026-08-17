import io
import json
import zipfile

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from fdm.core.helpers import clean_assigned_content_type, get_content_type_for_model, get_content_type_instance
from fdm.metadata.enums import MetadataFieldType
from fdm.metadata.helpers import (
    check_metadata_template_permissions_for_object,
    get_metadata_structure_for_type,
    validate_metadata,
)
from fdm.metadata.models import MetadataField, MetadataTemplate, MetadataTemplateContainer, MetadataTemplateField


def add_scoped_container_to_pool(container, user) -> bool:
    from fdm.core.rest.permissions import is_folder_admin, is_project_admin
    from fdm.folders.models import Folder
    from fdm.projects.models import Project

    scope = container.assigned_to_content_object

    if isinstance(scope, Project):
        if not is_project_admin(user, scope):
            return False
    elif isinstance(scope, Folder):
        if not is_folder_admin(user, scope):
            return False
    else:
        return False

    scope.metadata_template_container.add(container)
    return True


def import_metadata_template_container_from_json_file(
    *,
    uploaded_file,
    assigned_to_content_type=None,
    assigned_to_object_id=None,
    user,
) -> MetadataTemplateContainer:
    payload = _load_import_payload(uploaded_file)

    if "assigned_to_content_type" in payload or "assigned_to_object_id" in payload:
        raise ValidationError(_("Imported metadata template container JSON must not contain scope fields."))

    content_type = _get_import_content_type(assigned_to_content_type)
    if bool(content_type) ^ bool(assigned_to_object_id):
        raise ValidationError(_("You must either provide a content type and an object id or none of them."))

    _validate_import_scope(content_type, assigned_to_object_id, user)
    metadata_template_fields = payload.get("metadata_template_fields", [])
    _validate_import_metadata_fields(metadata_template_fields)
    validate_metadata(metadata_template_fields)

    with transaction.atomic():
        name = get_available_metadata_template_container_name(
            name=payload.get("name"),
            assigned_to_content_type=content_type,
            assigned_to_object_id=assigned_to_object_id,
        )
        container = MetadataTemplateContainer.objects.create(
            name=name,
            assigned_to_content_type=content_type,
            assigned_to_object_id=assigned_to_object_id,
        )
        metadata_template = MetadataTemplate.objects.create(
            metadata_template_container=container,
        )

        for field_payload in metadata_template_fields:
            _create_imported_metadata_template_field(metadata_template, field_payload)

        add_scoped_container_to_pool(container, user)

    return container


def get_available_metadata_template_container_name(
    *,
    name: str,
    assigned_to_content_type=None,
    assigned_to_object_id=None,
) -> str:
    base_name = str(name or _("Imported metadata template"))
    existing_names = set(
        MetadataTemplateContainer.objects.filter(
            assigned_to_content_type=assigned_to_content_type,
            assigned_to_object_id=assigned_to_object_id,
        ).values_list("name", flat=True),
    )

    if base_name not in existing_names:
        return base_name

    counter = 1
    while True:
        suffix = f" ({counter})"
        candidate = f"{base_name[: MetadataTemplateContainer._meta.get_field('name').max_length - len(suffix)]}{suffix}"
        if candidate not in existing_names:
            return candidate
        counter += 1


def _load_import_payload(uploaded_file) -> dict:
    if not uploaded_file:
        raise ValidationError(_("You must provide a JSON file to import."))

    content = uploaded_file.read()
    if uploaded_file.name.lower().endswith(".zip") or getattr(uploaded_file, "content_type", "") == "application/zip":
        raise ValidationError(_("You must import a single JSON file, not a ZIP file."))

    if zipfile.is_zipfile(io.BytesIO(content)):
        raise ValidationError(_("You must import a single JSON file, not a ZIP file."))

    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValidationError(_("The uploaded file is not valid JSON.")) from e

    if not isinstance(payload, dict):
        raise ValidationError(_("The uploaded JSON must contain one metadata template container."))

    return payload


def _get_import_content_type(assigned_to_content_type):
    if not assigned_to_content_type:
        return None

    return get_content_type_instance(assigned_to_content_type)


def _validate_import_scope(content_type, assigned_to_object_id, user) -> None:
    from fdm.folders.models import Folder
    from fdm.projects.models import Project
    from fdm.uploads.models import UploadsDataset

    if content_type and assigned_to_object_id:
        clean_assigned_content_type(
            assigned_to_content_type=content_type,
            assigned_to_object_id=assigned_to_object_id,
            allowed_content_models=[
                Project,
                Folder,
                UploadsDataset,
            ],
        )

    check_metadata_template_permissions_for_object(
        content_type=get_content_type_for_model(content_type.model_class()) if content_type else None,
        object_id=assigned_to_object_id,
        user=user,
        require_dataset_permissions=True,
    )


def _validate_import_metadata_fields(metadata_template_fields: list) -> None:
    if not isinstance(metadata_template_fields, list):
        raise ValidationError(_("Metadata template fields must be a list."))

    for field_payload in metadata_template_fields:
        if not isinstance(field_payload, dict):
            raise ValidationError(_("Each metadata template field must be an object."))

        field = field_payload.get("field")
        if field:
            try:
                MetadataField.objects.get(pk=field)
            except (MetadataField.DoesNotExist, ValidationError, ValueError) as e:
                raise ValidationError(_("The referenced metadata field does not exist.")) from e


def _create_imported_metadata_template_field(metadata_template, field_payload: dict) -> None:
    field_id = field_payload.get("field") or None
    metadata_field = MetadataField.objects.get(pk=field_id) if field_id else None
    field_type = (
        metadata_field.field_type if metadata_field else field_payload.get("field_type", MetadataFieldType.TEXT)
    )
    value = field_payload.get("value", None)

    MetadataTemplateField.objects.create(
        metadata_template=metadata_template,
        field=metadata_field,
        custom_key=None if metadata_field else field_payload.get("custom_key"),
        field_type=field_type,
        value=get_metadata_structure_for_type(field_type, value),
        config=field_payload.get("config") or {},
        mandatory=field_payload.get("mandatory", False),
        order=field_payload.get("order", 0),
    )
