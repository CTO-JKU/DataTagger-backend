from django.contrib.auth import get_user_model
from django.db import connection
from drf_spectacular.utils import extend_schema_field
from rest_framework import fields, serializers

from fdm.core.helpers import get_content_type_for_object, get_content_type_instance
from fdm.core.rest.base import BaseModelSerializer
from fdm.users.rest.serializers import *

User = get_user_model()


__all__ = [
    "CONTENT_TYPES",
    "METADATA_CONTENT_TYPES",
    "METADATA_TEMPLATE_CONTENT_TYPES",
    "UserResponseSerializer",
    "TokenResponseSerializer",
    "BaseModelWithByUserSerializer",
    "LockStatusSerializer",
    "UploadsDatasetMetadataUpgradeStatusSerializer",
    "ContentTypeSerializer",
    "MetadataContentTypeSerializer",
    "MetadataContentTypePayloadSerializer",
    "MetadataTemplateContentTypeSerializer",
    "MetadataTemplateContentTypePayloadSerializer",
    "CookieAuthTokenSerializer",
    "ContentObjectSerializer",
]


def get_content_type_enum_values(model_filter: list[any] | None = None) -> list[str]:
    if "django_content_type" not in connection.introspection.table_names():
        return []

    from django.contrib.contenttypes.models import ContentType

    if model_filter:
        content_types = [ContentType.objects.get_for_model(model) for model in model_filter]
    else:
        content_types = ContentType.objects.all()

    return [get_content_type_for_object(content_type) for content_type in content_types]


CONTENT_TYPES = get_content_type_enum_values()


def get_metadata_content_type_enum_values() -> list[str]:
    from fdm.folders.models import Folder
    from fdm.projects.models import Project
    from fdm.uploads.models import UploadsVersion, UploadsVersionFile

    return get_content_type_enum_values(
        [
            Folder,
            Project,
            UploadsVersion,
            UploadsVersionFile,
        ],
    )


METADATA_CONTENT_TYPES = get_metadata_content_type_enum_values()


def get_metadata_template_content_type_enum_values() -> list[str]:
    from fdm.folders.models import Folder
    from fdm.projects.models import Project
    from fdm.uploads.models import UploadsDataset

    return get_content_type_enum_values(
        [
            Folder,
            Project,
            UploadsDataset,
        ],
    )


METADATA_TEMPLATE_CONTENT_TYPES = get_metadata_template_content_type_enum_values()


class UserResponseSerializer(UserSerializer):
    """
    Used only for OpenAPI generator
    """

    permissions = fields.ListField(
        child=fields.CharField(),
        read_only=True,
    )


class TokenResponseSerializer(serializers.Serializer):
    """
    Used only for OpenAPI generator
    """

    token = serializers.CharField(read_only=True)

    user = UserResponseSerializer(
        read_only=True,
    )


class BaseModelWithByUserSerializer(BaseModelSerializer):
    created_by = MinimalUserSerializer(
        read_only=True,
    )

    creation_date = serializers.DateTimeField(
        read_only=True,
    )

    last_modified_by = MinimalUserSerializer(
        read_only=True,
    )

    last_modification_date = serializers.DateTimeField(
        read_only=True,
    )

    def get_field_names(self, declared_fields, info):
        """Overrides the default get_field_names method, and adds the primary key, the display (__str__) and
        content_type"""
        field_names = super().get_field_names(declared_fields, info)

        return list(
            set(
                [
                    "created_by",
                    "creation_date",
                    "last_modified_by",
                    "last_modification_date",
                ]
                + field_names,
            ),
        )


class LockStatusSerializer(serializers.Serializer):
    locked = serializers.BooleanField(
        read_only=True,
    )

    locked_by = MinimalUserSerializer(
        allow_null=True,
        read_only=True,
    )

    locked_at = serializers.DateTimeField(
        allow_null=True,
        read_only=True,
    )


class UploadsDatasetMetadataUpgradeStatusSerializer(serializers.Serializer):
    has_upgradeable_metadata_templates = serializers.BooleanField(
        read_only=True,
    )

    metadata_upgrade_status = serializers.CharField(
        read_only=True,
    )


@extend_schema_field(
    {
        "oneOf": [
            {
                "type": "string",
                "enum": CONTENT_TYPES,
            },
            {
                "type": "integer",
            },
        ],
    },
)
class ContentTypeSerializer(serializers.Field):
    def to_representation(self, value):
        return f"{value.app_label}.{value.model}"

    def to_internal_value(self, data):
        if not data:
            return None

        from django.contrib.contenttypes.models import ContentType

        try:
            if isinstance(data, int):
                return ContentType.objects.get(pk=data)
            elif isinstance(data, str):
                return get_content_type_instance(data)
        except (ContentType.DoesNotExist, ValueError) as e:
            raise serializers.ValidationError(str(e))

        return None


@extend_schema_field(
    {
        "type": "string",
        "enum": METADATA_CONTENT_TYPES,
    },
)
class MetadataContentTypeSerializer(ContentTypeSerializer):
    pass


@extend_schema_field(
    {
        "oneOf": [
            {
                "type": "string",
                "enum": METADATA_CONTENT_TYPES,
            },
            {
                "type": "integer",
            },
        ],
    },
)
class MetadataContentTypePayloadSerializer(ContentTypeSerializer):
    pass


@extend_schema_field(
    {
        "type": "string",
        "enum": METADATA_TEMPLATE_CONTENT_TYPES,
    },
)
class MetadataTemplateContentTypeSerializer(ContentTypeSerializer):
    pass


@extend_schema_field(
    {
        "oneOf": [
            {
                "type": "string",
                "enum": METADATA_TEMPLATE_CONTENT_TYPES,
            },
            {
                "type": "integer",
            },
        ],
    },
)
class MetadataTemplateContentTypePayloadSerializer(ContentTypeSerializer):
    pass


class CookieAuthTokenSerializer(serializers.Serializer):
    pass


@extend_schema_field(
    {
        "type": "string",
    },
)
class ContentObjectSerializer(serializers.Field):
    def to_representation(self, value):
        """
        Serialize the content_object (GenericForeignKey).
        """
        if value is None:
            return None

        # Check for local_private_dss_path
        if hasattr(value, "local_private_dss_path"):
            local_path = (
                value.local_private_dss_path()
                if callable(
                    value.local_private_dss_path,
                )
                else value.local_private_dss_path
            )
            return {
                "local_private_dss_path": local_path,
                "string_representation": str(value),
            }
        elif hasattr(value, "__str__"):  # Fallback to string representation
            return str(value)
        else:
            raise NotImplementedError(
                f"Serialization of {type(value).__name__} is not implemented.",
            )
