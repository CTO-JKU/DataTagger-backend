import re
from datetime import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils.translation import gettext_lazy as _

from fdm.core.helpers import check_empty_value, get_content_type_for_model, get_content_type_for_object
from fdm.core.rest.permissions import is_folder_metadata_template_admin, is_project_metadata_template_admin
from fdm.folders.models import Folder
from fdm.metadata.enums import MetadataFieldType
from fdm.metadata.models import (
    Metadata,
    MetadataField,
    MetadataTemplate,
    MetadataTemplateContainer,
    MetadataTemplateField,
)
from fdm.projects.models import Project

__all__ = [
    "get_metadata_structure_for_type",
    "get_metadata_value_for_type",
    "convert_metadata_value",
    "set_metadata",
    "validate_metadata",
    "delete_metadata_for_relation",
    "set_metadata_for_relation",
    "check_metadata_template_permissions_for_object",
    "create_metadata_template_for_object",
    "validate_metadata_integer",
    "validate_metadata_decimal",
    "validate_metadata_datetime_format",
    "validate_metadata_datetime",
    "validate_metadata_date",
    "validate_metadata_time",
    "validate_metadata_selection",
    "validate_metadata_multi_selection",
    "validate_metadata_wysiwyg",
    "validate_metadata_value",
]


def get_metadata_structure_for_type(field_type, value):
    value = check_empty_value(value)

    if field_type in [
        MetadataFieldType.INTEGER,
        MetadataFieldType.DECIMAL,
        MetadataFieldType.DATETIME,
        MetadataFieldType.DATE,
        MetadataFieldType.TIME,
        MetadataFieldType.TEXT,
        MetadataFieldType.SELECTION,
    ]:
        return dict(
            value=str(value) if value is not None else None,
        )
    elif field_type in [
        MetadataFieldType.MULTI_SELECTION,
        MetadataFieldType.TAG,
    ]:
        if value is None:
            normalized_value = None
        elif isinstance(value, list):
            normalized_value = [str(v) for v in value]
        else:
            normalized_value = [str(value)]

        return dict(
            value=normalized_value,
        )
    elif field_type == MetadataFieldType.WYSIWYG:
        return dict() if value is None else value

    return value


def get_metadata_value_for_type(field_type, value):
    if value is None:
        return None

    if field_type in [
        MetadataFieldType.INTEGER,
        MetadataFieldType.DECIMAL,
        MetadataFieldType.DATETIME,
        MetadataFieldType.DATE,
        MetadataFieldType.TIME,
        MetadataFieldType.TEXT,
        MetadataFieldType.SELECTION,
        MetadataFieldType.MULTI_SELECTION,
        MetadataFieldType.TAG,
    ]:
        return value.get("value")
    elif field_type == MetadataFieldType.WYSIWYG:
        return value

    return value


def convert_metadata_value(value, from_field_type, to_field_type):
    if value is None:
        return None

    if from_field_type == to_field_type:
        return value

    multi_value_selection_types = [
        MetadataFieldType.MULTI_SELECTION,
        MetadataFieldType.TAG,
    ]
    all_selection_types = [
        MetadataFieldType.SELECTION,
        *multi_value_selection_types,
    ]

    if to_field_type == MetadataFieldType.SELECTION:
        if from_field_type == MetadataFieldType.WYSIWYG:
            raise ValidationError(_("You cannot convert WYSIWYG editor values to this field type."))

        if from_field_type in multi_value_selection_types:
            if isinstance(value, list):
                if len(value) == 0:
                    return None
                if len(value) > 1:
                    raise ValidationError(_("You cannot convert multiple selected values to a single selection value."))

                return str(value[0]) if value[0] is not None else None

            return str(value)

        return str(value)

    if to_field_type in multi_value_selection_types:
        if from_field_type == MetadataFieldType.WYSIWYG:
            raise ValidationError(_("You cannot convert WYSIWYG editor values to this field type."))

        if from_field_type in multi_value_selection_types:
            if isinstance(value, list):
                return [str(v) for v in value]
            return [str(value)]

        return [str(value)]

    if from_field_type in all_selection_types:
        raise ValidationError(_("You cannot convert selection values to this field type."))

    if to_field_type in [
        MetadataFieldType.INTEGER,
        MetadataFieldType.DECIMAL,
        MetadataFieldType.DATETIME,
        MetadataFieldType.DATE,
        MetadataFieldType.TIME,
    ]:
        converted_value = value

        if from_field_type == MetadataFieldType.DATETIME:
            if to_field_type == MetadataFieldType.DATE:
                converted_value = value.split(" ")[0]
            elif to_field_type == MetadataFieldType.TIME:
                converted_value = value.split(" ")[1]
        elif from_field_type == MetadataFieldType.DATE:
            if to_field_type == MetadataFieldType.DATETIME:
                converted_value = f"{value} 00:00:00"

        if to_field_type == MetadataFieldType.INTEGER:
            validate_metadata_integer(value=converted_value)
        elif to_field_type == MetadataFieldType.DECIMAL:
            validate_metadata_decimal(value=converted_value)
        elif to_field_type == MetadataFieldType.DATETIME:
            validate_metadata_datetime(value=converted_value)
        elif to_field_type == MetadataFieldType.DATE:
            validate_metadata_date(value=converted_value)
        elif to_field_type == MetadataFieldType.TIME:
            validate_metadata_time(value=converted_value)

        return converted_value

    return value


def set_metadata(
    custom_key=None,
    value=None,
    config=None,
    previous_version_field=None,
    metadata_template_field=None,
    order=None,
    field_type=None,
    field=None,
    read_only=False,
    assigned_to_content_type=None,
    assigned_to_object_id=None,
):
    if field and custom_key:
        raise ValidationError(_("You must not link a metadata field and declare a custom key together."))

    if not field and not custom_key:
        raise ValidationError(_("You must either link a metadata field or declare a custom key."))

    field_type = field_type if field_type else field.field_type if field else None or MetadataFieldType.TEXT

    from fdm.metadata.choice_values import canonicalize_choice_field

    value, config = canonicalize_choice_field(
        field_type=field_type,
        value=value,
        config=config or {},
    )
    value = check_empty_value(value)

    if isinstance(metadata_template_field, str):
        try:
            metadata_template_field = MetadataTemplateField.objects.get(pk=metadata_template_field)
        except MetadataTemplateField.DoesNotExist:
            raise ValidationError(_("The metadata template field does not exist."))

    return Metadata.objects.create(
        assigned_to_content_type=assigned_to_content_type,
        assigned_to_object_id=assigned_to_object_id,
        field_id=field.pk if isinstance(field, MetadataField) else field,
        custom_key=custom_key,
        field_type=field_type,
        value=get_metadata_structure_for_type(
            field_type=field_type,
            value=value,
        ),
        config=config,
        previous_version_field_id=(
            previous_version_field.pk if isinstance(previous_version_field, Metadata) else previous_version_field
        ),
        metadata_template_field=metadata_template_field,
        order=order,
        read_only=read_only,
    )


def validate_metadata(
    metadata_list: list[dict | Metadata | MetadataTemplateField],
) -> None:
    for metadata in metadata_list:
        if isinstance(metadata, Metadata) or isinstance(metadata, MetadataTemplateField):
            field = metadata.field
            custom_key = metadata.custom_key
            field_type = metadata.field_type
            value = metadata.get_value()
            config = metadata.config
        else:
            field = metadata.get("field", None)
            custom_key = metadata.get("custom_key", None)
            field_type = metadata.get("field_type", None)
            value = metadata.get("value", None)
            config = metadata.get("config", {})

        if isinstance(field, dict):
            try:
                field = MetadataField.objects.get(key=field["key"])
                field_type = field.field_type
                custom_key = field.key
            except MetadataField.DoesNotExist:
                custom_key = field["key"]
                field_type = field["field_type"]
        elif isinstance(field, str):
            try:
                field = MetadataField.objects.get(pk=field)
                field_type = field.field_type
                custom_key = field.key
            except MetadataField.DoesNotExist:
                field_type = None
                custom_key = field

        from fdm.metadata.choice_values import canonicalize_choice_field

        value, config = canonicalize_choice_field(
            field_type=field_type,
            value=value,
            config=config or {},
        )
        value = check_empty_value(value)

        if isinstance(metadata, MetadataTemplateField):
            ValidationModel = MetadataTemplateField
        else:
            ValidationModel = Metadata

        ValidationModel(
            custom_key=custom_key,
            field_type=field_type,
            value=get_metadata_structure_for_type(
                field_type=field_type,
                value=value,
            ),
            config=config or {},
        ).clean()


def delete_metadata_for_relation(relation: any) -> None:
    Metadata.objects.filter(
        assigned_to_content_type=relation.get_content_type(),
        assigned_to_object_id=relation.pk,
    ).delete()


def set_metadata_for_relation(
    metadata_list: list[dict | Metadata | MetadataTemplateField],
    relation: any,
    retain_existing_metadata: bool = False,
    set_pk_as_previous_version_field: bool = False,
    order_offset: int = 0,
) -> None:
    metadata_template_field = None
    previous_version_field = None

    if not retain_existing_metadata:
        delete_metadata_for_relation(relation=relation)

    for metadata in metadata_list:
        # A metadata field can either be an existing pk or an object to create a new metadata field on the fly
        if isinstance(metadata, Metadata) or isinstance(metadata, MetadataTemplateField):
            field = metadata.field
            custom_key = metadata.custom_key
            field_type = metadata.field_type
            value = metadata.get_value()
            config = metadata.config
            order = metadata.order

            if isinstance(metadata, Metadata):
                metadata_template_field = metadata.metadata_template_field
                previous_version_field = (
                    metadata.pk if set_pk_as_previous_version_field else metadata.previous_version_field
                )
            elif isinstance(metadata, MetadataTemplateField):
                metadata_template_field = metadata
        else:
            field = metadata.get("field", None)
            custom_key = metadata.get("custom_key", None)
            field_type = metadata.get("field_type", None)
            value = metadata.get("value", None)
            config = metadata.get("config", {})
            previous_version_field = (
                metadata.get("pk", None)
                if set_pk_as_previous_version_field
                else metadata.get("previous_version_field", None)
            )
            metadata_template_field = metadata.get("metadata_template_field", None)
            order = metadata.get("order")

        if isinstance(field, dict):
            try:
                field = MetadataField.objects.create(
                    key=field["key"],
                    field_type=field["field_type"],
                )
            except ValidationError:
                field = MetadataField.objects.get(key=field["key"])

            field_type = field.field_type

        if metadata_template_field and isinstance(metadata_template_field, str):
            metadata_template_field = MetadataTemplateField.objects.get(pk=metadata_template_field)

        value = check_empty_value(value)

        if order is None:
            order_value = order_offset if order_offset else None
        else:
            order_value = order + order_offset

        set_metadata(
            field=field.pk if isinstance(field, MetadataField) else field,
            field_type=field_type or MetadataFieldType.TEXT,
            custom_key=custom_key,
            value=value,
            config=config,
            previous_version_field=previous_version_field,
            metadata_template_field=metadata_template_field,
            order=order_value,
            assigned_to_content_type=relation.get_content_type(),
            assigned_to_object_id=relation.pk,
        )


def check_metadata_template_permissions_for_object(
    content_type=None,
    object_id=None,
    user=None,
    require_dataset_permissions=False,
):
    if user is None:
        return

    if content_type and hasattr(content_type, "app_label") and hasattr(content_type, "model"):
        content_type = get_content_type_for_object(content_type)

    # Permission check for global metadata templates
    if not content_type and not object_id:
        if not getattr(user, "is_global_metadata_template_admin", False):
            raise PermissionDenied(_("You don't have global metadata template admin permissions."))
        return

    # Permission check for the linked metadata template type
    if content_type == get_content_type_for_model(Project) and not is_project_metadata_template_admin(user, object_id):
        raise PermissionDenied(_("You don't have permissions for this project metadata template."))
    elif content_type == get_content_type_for_model(Folder) and not is_folder_metadata_template_admin(user, object_id):
        raise PermissionDenied(_("You don't have permissions for this folder metadata template."))
    else:
        from fdm.folders.models import FolderPermission
        from fdm.uploads.models import UploadsDataset

        if content_type != get_content_type_for_model(UploadsDataset) or not require_dataset_permissions:
            return

        try:
            dataset = UploadsDataset.objects.get(pk=object_id)
        except UploadsDataset.DoesNotExist:
            raise PermissionDenied(_("You don't have permissions for this dataset metadata template."))

        if not dataset.folder_id:
            raise PermissionDenied(_("You don't have permissions for this dataset metadata template."))

        if not FolderPermission.objects.filter(
            folder=dataset.folder,
            project_membership__member=user,
            can_edit=True,
            is_metadata_template_admin=True,
        ).exists():
            raise PermissionDenied(_("You don't have permissions for this dataset metadata template."))


def create_metadata_template_for_object(
    metadata_template_data: any,
    metadata_template_fields: list,
    assigned_to_object: any = None,
    user: any = None,
) -> MetadataTemplate | None:
    if metadata_template_data is None:
        return None

    if assigned_to_object:
        check_metadata_template_permissions_for_object(
            content_type=get_content_type_for_object(assigned_to_object.get_content_type()),
            object_id=assigned_to_object.pk,
            user=user,
        )

    metadata_template_container = MetadataTemplateContainer.objects.create(
        assigned_to_content_type=assigned_to_object.get_content_type() if assigned_to_object else None,
        assigned_to_object_id=assigned_to_object.pk if assigned_to_object else None,
        **metadata_template_data,
    )

    metadata_template = MetadataTemplate.objects.create(
        metadata_template_container=metadata_template_container,
    )

    if metadata_template_fields:
        for metadata_template_field in metadata_template_fields:
            value = metadata_template_field.pop("value", None)
            order = metadata_template_field.pop("order", 0)
            field = MetadataTemplateField.objects.create(
                metadata_template=metadata_template,
                order=order,
                **metadata_template_field,
            )
            field.set_value(value)

    return metadata_template


def validate_metadata_integer(value: any):
    try:
        if not re.match(r"^[+-]?\d+([eE][+]?\d+)?$", str(value)):
            raise ValueError
    except ValueError:
        raise ValidationError(
            _("Metadata value for {key} must be an integer."),
        )


def validate_metadata_decimal(value: any):
    try:
        if not re.match(r"^[-+]?[0-9]*\.?[0-9]+([eE][-+]?[0-9]+)?$", str(value)):
            raise ValueError
    except ValueError:
        raise ValidationError(
            _("Metadata value for {key} must be a decimal numeral."),
        )


def validate_metadata_datetime_format(value: any, datetime_format: str, error_msg: str):
    try:
        if not isinstance(value, str):
            raise ValueError

        datetime.strptime(value, datetime_format)
    except ValueError as e:
        raise ValidationError(
            error_msg if error_msg else str(e),
        )


def validate_metadata_datetime(value: any):
    validate_metadata_datetime_format(
        value=value,
        datetime_format="%Y-%m-%d %H:%M:%S",
        error_msg=_(
            "Metadata value for {key} must be in YYYY-MM-DD HH:MM:SS format and also be a valid date and time.",
        ),
    )


def validate_metadata_date(value: any):
    validate_metadata_datetime_format(
        value=value,
        datetime_format="%Y-%m-%d",
        error_msg=_("Metadata value for {key} must be in YYYY-MM-DD format and also be a valid date."),
    )


def validate_metadata_time(value: any):
    validate_metadata_datetime_format(
        value=value,
        datetime_format="%H:%M:%S",
        error_msg=_("Metadata value for {key} must be in HH:MM:SS format and also be a valid time."),
    )


def validate_metadata_selection(value: any, config: dict):
    try:
        options = config.get("options")

        if not isinstance(options, list):
            raise ValueError

        if value not in options:
            raise ValueError
    except ValueError:
        raise ValidationError(
            _("Metadata value for {key} must be included in the list of options."),
        )


def validate_metadata_multi_selection(value: any, config: dict):
    try:
        options = config.get("options")

        if not isinstance(options, list):
            raise ValueError

        if not isinstance(value, list):
            raise ValueError

        if not all(selected_option in options for selected_option in value):
            raise ValueError
    except ValueError:
        raise ValidationError(
            _("Metadata value for {key} must be included in the list of options."),
        )


def validate_metadata_wysiwyg(value: any):
    try:
        if not isinstance(value, dict):
            raise ValueError
    except ValueError:
        raise ValidationError(
            _("Metadata value for {key} must be a dict."),
        )


def validate_metadata_value(
    field: MetadataField | MetadataTemplateField,
    field_type: str,
    custom_key: str | None,
    value: any,
    config: dict,
    raise_exception: bool = False,
) -> bool:
    try:
        if field_type == MetadataFieldType.INTEGER:
            validate_metadata_integer(value=value)
        elif field_type == MetadataFieldType.DECIMAL:
            validate_metadata_decimal(value=value)
        elif field_type == MetadataFieldType.DATETIME:
            validate_metadata_datetime(value=value)
        elif field_type == MetadataFieldType.DATE:
            validate_metadata_date(value=value)
        elif field_type == MetadataFieldType.TIME:
            validate_metadata_time(value=value)
        elif field_type == MetadataFieldType.SELECTION:
            validate_metadata_selection(
                value=value,
                config=config,
            )
        elif field_type in [
            MetadataFieldType.MULTI_SELECTION,
            MetadataFieldType.TAG,
        ]:
            validate_metadata_multi_selection(
                value=value,
                config=config,
            )
        elif field_type == MetadataFieldType.WYSIWYG:
            validate_metadata_wysiwyg(value=value)
    except ValueError as e:
        if raise_exception:
            raise ValidationError(
                str(e).format(key=field.key if field else custom_key),
            )

        return False

    return True
