from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import QuerySet, Value
from django.db.models.functions import Coalesce
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from fdm.core.helpers import clean_assigned_content_type
from fdm.core.rest.base import BaseModelSerializer
from fdm.core.rest.serializers import (
    BaseModelWithByUserSerializer,
    MetadataContentTypeSerializer,
    MetadataTemplateContentTypePayloadSerializer,
    MetadataTemplateContentTypeSerializer,
)
from fdm.folders.models import Folder
from fdm.metadata.enums import MetadataFieldType
from fdm.metadata.helpers import check_metadata_template_permissions_for_object, get_metadata_value_for_type
from fdm.metadata.models import *
from fdm.projects.models import Project
from fdm.uploads.models import UploadsDataset

__all__ = [
    "SortedMetadataListSerializer",
    "SortedMetadataTemplateFieldsListSerializer",
    "MetadataFieldSerializer",
    "MetadataConfigFieldSerializer",
    "MinimalMetadataTemplateContainerSerializer",
    "MetadataTemplateSearchSerializer",
    "MetadataTemplateVersionSearchSerializer",
    "MetadataTemplateListSerializer",
    "MetadataTemplateSerializer",
    "MinimalMetadataTemplateSerializer",
    "MetadataTemplateUpdateSerializer",
    "MetadataTemplateFieldSerializer",
    "MetadataTemplateFieldNestedSerializer",
    "MetadataSerializer",
    "MinimalMetadataSerializer",
    "MetadataTemplatesForMetadataTemplateContainerListSerializer",
    "MetadataTemplatesForMetadataTemplateContainerSerializer",
    "MetadataTemplateContainerSerializer",
    "MetadataTemplateContainerCreateSerializer",
    "MetadataTemplateContainerCloneActionPayloadSerializer",
    "MetadataTemplateContainerUpdateSerializer",
    "MetadataTemplateFieldPayloadSerializerField",
    "MetadataTemplateFieldsPayloadSerializer",
    "MetadataTemplateContainerVersionActionPayloadSerializer",
    "MetadataPayloadSerializer",
    "MetadataPayloadSerializerField",
    "MetadataUpgradePayloadSerializer",
    "MetadataUpgradePayloadSerializerField",
    "MetadataTemplateDiffValueSerializer",
    "MinimalMetadataTemplateDiffSerializer",
    "MetadataTemplateDiffSerializer",
    "MetadataTemplateDiffResponseSerializer",
    "MetadataTagSerializer",
]

METADATA_VALUE_SCHEMA = {
    "oneOf": [
        {
            "type": "string",
            "nullable": True,
        },
        {
            "type": "array",
            "items": {
                "type": "string",
            },
            "nullable": True,
        },
        {
            "type": "object",
            "nullable": True,
        },
    ],
}


class SortedMetadataListSerializer(serializers.ListSerializer):
    def to_representation(self, data):
        if isinstance(data, QuerySet):
            data = data.order_by(
                Coalesce("order", Value(0)),
                "custom_key",
            )
        elif hasattr(data, "all"):
            data = data.all().order_by(
                Coalesce("order", Value(0)),
                "custom_key",
            )
        return super().to_representation(data)


class SortedMetadataTemplateFieldsListSerializer(serializers.ListSerializer):
    def to_representation(self, data):
        if isinstance(data, QuerySet):
            data = data.order_by(
                Coalesce("order", Value(0)),
                "custom_key",
            )
        elif hasattr(data, "all"):
            data = data.all().order_by(
                Coalesce("order", Value(0)),
                "custom_key",
            )
        return super().to_representation(data)


class MetadataFieldSerializer(BaseModelSerializer):
    class Meta:
        model = MetadataField
        fields = [
            "pk",
            "key",
            "field_type",
            "read_only",
        ]


@extend_schema_field(
    {
        "type": "object",
    },
)
class MetadataConfigFieldSerializer(serializers.Serializer):
    pass


class MinimalMetadataTemplateContainerSerializer(BaseModelSerializer):
    assigned_to_content_type = MetadataTemplateContentTypeSerializer(
        required=False,
        allow_null=True,
    )

    assigned_to_content_object_name = serializers.SerializerMethodField()

    @staticmethod
    def get_assigned_to_content_object_name(obj) -> str | None:
        if obj.assigned_to_content_object:
            return obj.assigned_to_content_object.name

        return None

    project = serializers.SerializerMethodField()

    @staticmethod
    @extend_schema_field(
        {
            "type": "object",
            "nullable": True,
            "properties": {
                "pk": {
                    "type": "string",
                    "format": "uuid",
                },
                "name": {
                    "type": "string",
                },
            },
        },
    )
    def get_project(obj):
        from django.contrib.contenttypes.models import ContentType

        from fdm.projects.rest.serializers import MinimalProjectSerializer

        if obj.assigned_to_content_type == ContentType.objects.get_for_model(UploadsDataset):
            try:
                uploads_dataset = UploadsDataset.objects.get(pk=obj.assigned_to_object_id)
                return MinimalProjectSerializer(uploads_dataset.folder.project).data if uploads_dataset.folder else None
            except Project.DoesNotExist:
                return None
        elif obj.assigned_to_content_type == ContentType.objects.get_for_model(Folder):
            try:
                folder = Folder.objects.get(pk=obj.assigned_to_object_id)
                return MinimalProjectSerializer(folder.project).data
            except Folder.DoesNotExist:
                return None
        elif obj.assigned_to_content_type == ContentType.objects.get_for_model(Project):
            try:
                project = Project.objects.get(pk=obj.assigned_to_object_id)
                return MinimalProjectSerializer(project).data
            except Project.DoesNotExist:
                return None

        return None

    class Meta:
        model = MetadataTemplateContainer
        fields = [
            "pk",
            "name",
            "assigned_to_content_type",
            "assigned_to_object_id",
            "assigned_to_content_object_name",
            "project",
        ]


class MetadataTemplateSerializer(BaseModelWithByUserSerializer):
    metadata_template_container = MinimalMetadataTemplateContainerSerializer()

    class Meta:
        model = MetadataTemplate
        fields = [
            "pk",
            "name",
            "metadata_template_container",
        ]


class MetadataTemplateListSerializer(MetadataTemplateSerializer):
    name = serializers.SerializerMethodField()

    @staticmethod
    def get_name(obj):
        if obj.name:
            return obj.name
        if obj.metadata_template_container:
            return obj.metadata_template_container.name
        return None


class MetadataTemplateSearchSerializer(BaseModelSerializer):
    name = serializers.SerializerMethodField()
    template_type = serializers.SerializerMethodField()
    assigned_to = serializers.SerializerMethodField()
    field_count = serializers.SerializerMethodField()
    latest_template = serializers.SerializerMethodField()

    @staticmethod
    def get_name(obj):
        return getattr(obj, "search_template_name", None) or obj.name

    @staticmethod
    def _resolve_template_type(obj) -> str:
        from django.contrib.contenttypes.models import ContentType

        if not obj.assigned_to_content_type_id or not obj.assigned_to_object_id:
            return "global"

        if obj.assigned_to_content_type == ContentType.objects.get_for_model(UploadsDataset):
            return "dataset"
        if obj.assigned_to_content_type == ContentType.objects.get_for_model(Folder):
            return "folder"
        if obj.assigned_to_content_type == ContentType.objects.get_for_model(Project):
            return "project"
        return obj.assigned_to_content_type.model

    def get_template_type(self, obj):
        return getattr(obj, "search_template_type", self._resolve_template_type(obj))

    @staticmethod
    @extend_schema_field(
        {
            "type": "object",
            "nullable": True,
            "properties": {
                "result_type": {
                    "type": "string",
                },
                "pk": {
                    "type": "string",
                    "format": "uuid",
                },
                "name": {
                    "type": "string",
                },
            },
        },
    )
    def get_assigned_to(obj):
        assigned_to = getattr(obj, "search_assigned_to_summary", None)
        if assigned_to is not None:
            return assigned_to

        if not obj.assigned_to_content_type_id or not obj.assigned_to_object_id:
            return None

        assigned_object = obj.assigned_to_content_object
        if assigned_object is None:
            return None

        return {
            "result_type": MetadataTemplateSearchSerializer._resolve_template_type(obj),
            "pk": str(assigned_object.pk),
            "name": getattr(assigned_object, "display_name", None) or assigned_object.name,
        }

    def get_field_count(self, obj):
        if hasattr(obj, "search_field_count"):
            return obj.search_field_count
        latest_template = getattr(obj, "search_latest_template", None) or obj.latest_metadata_template
        if latest_template is None:
            return 0
        return len(latest_template.metadata_template_fields.all())

    @staticmethod
    @extend_schema_field(
        {
            "type": "object",
            "nullable": True,
            "properties": {
                "pk": {
                    "type": "string",
                    "format": "uuid",
                },
                "name": {
                    "type": "string",
                    "nullable": True,
                },
            },
        },
    )
    def get_latest_template(obj):
        latest_template = getattr(obj, "search_latest_template", None) or obj.latest_metadata_template
        if latest_template is None:
            return None
        return {
            "pk": str(latest_template.pk),
            "name": latest_template.name
            or getattr(obj, "search_latest_template_name", None)
            or getattr(obj, "search_template_name", None)
            or obj.name,
        }

    class Meta:
        model = MetadataTemplateContainer
        fields = [
            "pk",
            "name",
            "template_type",
            "assigned_to",
            "field_count",
            "latest_template",
        ]


class MetadataTemplateVersionSearchSerializer(BaseModelSerializer):
    name = serializers.SerializerMethodField()
    template = serializers.SerializerMethodField()
    template_type = serializers.SerializerMethodField()
    assigned_to = serializers.SerializerMethodField()
    field_count = serializers.SerializerMethodField()

    @staticmethod
    def get_name(obj):
        return getattr(obj, "search_template_version_name", None) or obj.name or obj.metadata_template_container.name

    @staticmethod
    @extend_schema_field(
        {
            "type": "object",
            "properties": {
                "pk": {
                    "type": "string",
                    "format": "uuid",
                },
                "name": {
                    "type": "string",
                    "nullable": True,
                },
            },
        },
    )
    def get_template(obj):
        container = obj.metadata_template_container
        return {
            "pk": str(container.pk),
            "name": getattr(container, "search_template_name", None) or container.name,
        }

    @staticmethod
    def get_template_type(obj):
        return getattr(
            obj,
            "search_template_type",
            MetadataTemplateSearchSerializer._resolve_template_type(obj.metadata_template_container),
        )

    @staticmethod
    @extend_schema_field(
        {
            "type": "object",
            "nullable": True,
            "properties": {
                "result_type": {
                    "type": "string",
                },
                "pk": {
                    "type": "string",
                    "format": "uuid",
                },
                "name": {
                    "type": "string",
                },
            },
        },
    )
    def get_assigned_to(obj):
        assigned_to = getattr(obj, "search_assigned_to_summary", None)
        if assigned_to is not None:
            return assigned_to
        return MetadataTemplateSearchSerializer.get_assigned_to(obj.metadata_template_container)

    @staticmethod
    def get_field_count(obj):
        if hasattr(obj, "search_field_count"):
            return obj.search_field_count
        return len(obj.metadata_template_fields.all())

    class Meta:
        model = MetadataTemplate
        fields = [
            "pk",
            "name",
            "template",
            "template_type",
            "assigned_to",
            "field_count",
        ]


class MinimalMetadataTemplateSerializer(BaseModelSerializer):
    class Meta:
        model = MetadataTemplate
        fields = [
            "pk",
            "name",
        ]


class MetadataTemplateUpdateSerializer(BaseModelSerializer):
    class Meta:
        model = MetadataTemplate
        fields = [
            "pk",
            "name",
        ]

    def update(self, instance, validated_data):
        for key, value in validated_data.items():
            setattr(instance, key, value)

        instance.save()

        return instance


class MetadataTemplateFieldSerializer(BaseModelSerializer):
    metadata_template = MetadataTemplateSerializer(
        required=True,
        allow_null=False,
    )

    value = serializers.SerializerMethodField()

    @staticmethod
    @extend_schema_field(METADATA_VALUE_SCHEMA)
    def get_value(obj) -> str | list[str] | dict[str, Any] | None:
        return get_metadata_value_for_type(obj.field_type, obj.value)

    class Meta:
        model = MetadataTemplateField
        list_serializer_class = SortedMetadataTemplateFieldsListSerializer
        fields = [
            "pk",
            "metadata_template",
            "field",
            "custom_key",
            "field_type",
            "value",
            "config",
            "mandatory",
            "previous_version_field",
            "order",
        ]


class MetadataTemplateFieldNestedSerializer(BaseModelSerializer):
    field = MetadataFieldSerializer(
        required=False,
    )

    value = serializers.SerializerMethodField()

    @staticmethod
    @extend_schema_field(METADATA_VALUE_SCHEMA)
    def get_value(obj) -> str | list[str] | dict[str, Any] | None:
        return get_metadata_value_for_type(obj.field_type, obj.value)

    class Meta:
        model = MetadataTemplateField
        list_serializer_class = SortedMetadataTemplateFieldsListSerializer
        fields = [
            "pk",
            "field",
            "custom_key",
            "field_type",
            "value",
            "config",
            "mandatory",
            "previous_version_field",
            "order",
        ]


class MetadataSerializer(BaseModelSerializer):
    assigned_to_content_type = MetadataContentTypeSerializer(
        required=False,
        allow_null=True,
    )

    field = MetadataFieldSerializer(
        read_only=True,
    )

    config = MetadataConfigFieldSerializer(
        required=False,
        allow_null=True,
    )

    value = serializers.SerializerMethodField()

    @staticmethod
    @extend_schema_field(METADATA_VALUE_SCHEMA)
    def get_value(obj) -> str | list[str] | dict[str, Any] | None:
        return get_metadata_value_for_type(obj.field_type, obj.value)

    metadata_template = serializers.SerializerMethodField()

    @staticmethod
    @extend_schema_field(
        MetadataTemplateSerializer(
            required=False,
            allow_null=True,
        ),
    )
    def get_metadata_template(obj):
        if obj.metadata_template_field:
            metadata_template_field = MetadataTemplateField.objects.get(pk=obj.metadata_template_field.pk)

            try:
                return MetadataTemplateSerializer(metadata_template_field.metadata_template).data
            except MetadataTemplateField.DoesNotExist:
                return None

        return None

    mandatory = serializers.SerializerMethodField()

    @staticmethod
    def get_mandatory(obj) -> bool:
        if obj.metadata_template_field:
            return obj.metadata_template_field.mandatory

        return False

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if data.get("order") is None and self.context.get("normalize_order", True):
            data["order"] = 0
        return data

    class Meta:
        model = Metadata
        list_serializer_class = SortedMetadataListSerializer
        fields = [
            "pk",
            "field",
            "custom_key",
            "field_type",
            "read_only",
            "value",
            "config",
            "previous_version_field",
            "metadata_template_field",
            "mandatory",
            "metadata_template",
            "order",
            "assigned_to_content_type",
            "assigned_to_object_id",
        ]


class MinimalMetadataSerializer(BaseModelSerializer):
    field = MetadataFieldSerializer(
        read_only=True,
    )

    value = serializers.SerializerMethodField()

    @staticmethod
    @extend_schema_field(METADATA_VALUE_SCHEMA)
    def get_value(obj) -> str | list[str] | dict[str, Any] | None:
        return get_metadata_value_for_type(obj.field_type, obj.value)

    metadata_template = serializers.SerializerMethodField()

    @staticmethod
    @extend_schema_field(
        MetadataTemplateSerializer(
            required=False,
            allow_null=True,
        ),
    )
    def get_metadata_template(obj):
        if obj.metadata_template_field:
            metadata_template_field = MetadataTemplateField.objects.get(pk=obj.metadata_template_field.pk)

            try:
                return MetadataTemplateSerializer(metadata_template_field.metadata_template).data
            except MetadataTemplateField.DoesNotExist:
                return None

        return None

    mandatory = serializers.SerializerMethodField()

    @staticmethod
    def get_mandatory(obj) -> bool:
        if obj.metadata_template_field:
            return obj.metadata_template_field.mandatory

        return False

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if data.get("order") is None and self.context.get("normalize_order", True):
            data["order"] = 0
        return data

    class Meta:
        model = Metadata
        list_serializer_class = SortedMetadataListSerializer
        fields = [
            "pk",
            "field",
            "custom_key",
            "field_type",
            "read_only",
            "value",
            "config",
            "previous_version_field",
            "metadata_template_field",
            "mandatory",
            "metadata_template",
            "order",
        ]


class MetadataTemplatesForMetadataTemplateContainerListSerializer(serializers.ListSerializer):
    def to_representation(self, data):
        data = data.order_by(
            "-creation_date",
        )
        return super().to_representation(data)


class MetadataTemplatesForMetadataTemplateContainerSerializer(BaseModelSerializer):
    class Meta:
        model = MetadataTemplate
        list_serializer_class = MetadataTemplatesForMetadataTemplateContainerListSerializer
        fields = [
            "pk",
            "name",
            "creation_date",
        ]


class MetadataTemplateContainerSerializer(BaseModelWithByUserSerializer):
    metadata_templates = MetadataTemplatesForMetadataTemplateContainerSerializer(
        many=True,
        read_only=True,
    )

    latest_metadata_template = MinimalMetadataTemplateSerializer(
        required=False,
        allow_null=True,
    )

    assigned_to_content_type = MetadataTemplateContentTypeSerializer(
        required=False,
        allow_null=True,
    )

    assigned_to_content_object_name = serializers.SerializerMethodField()

    @staticmethod
    def get_assigned_to_content_object_name(obj) -> str | None:
        if obj.assigned_to_content_object:
            return obj.assigned_to_content_object.name

        return None

    project = serializers.SerializerMethodField()

    @staticmethod
    @extend_schema_field(
        {
            "type": "object",
            "nullable": True,
            "properties": {
                "pk": {
                    "type": "string",
                    "format": "uuid",
                },
                "name": {
                    "type": "string",
                },
            },
        },
    )
    def get_project(obj):
        from django.contrib.contenttypes.models import ContentType

        from fdm.projects.rest.serializers import MinimalProjectSerializer

        if obj.assigned_to_content_type == ContentType.objects.get_for_model(UploadsDataset):
            try:
                uploads_dataset = UploadsDataset.objects.get(pk=obj.assigned_to_object_id)
                return MinimalProjectSerializer(uploads_dataset.folder.project).data if uploads_dataset.folder else None
            except Project.DoesNotExist:
                return None
        elif obj.assigned_to_content_type == ContentType.objects.get_for_model(Folder):
            try:
                folder = Folder.objects.get(pk=obj.assigned_to_object_id)
                return MinimalProjectSerializer(folder.project).data
            except Folder.DoesNotExist:
                return None
        elif obj.assigned_to_content_type == ContentType.objects.get_for_model(Project):
            try:
                project = Project.objects.get(pk=obj.assigned_to_object_id)
                return MinimalProjectSerializer(project).data
            except Project.DoesNotExist:
                return None

        return None

    class Meta:
        model = MetadataTemplateContainer
        fields = [
            "pk",
            "name",
            "metadata_templates",
            "latest_metadata_template",
            "assigned_to_content_type",
            "assigned_to_object_id",
            "assigned_to_content_object_name",
            "project",
        ]


class MetadataTemplateContainerCreateSerializer(BaseModelSerializer):
    assigned_to_content_type = MetadataTemplateContentTypePayloadSerializer(
        required=False,
        allow_null=True,
    )

    metadata_template_fields = MetadataTemplateFieldNestedSerializer(
        many=True,
        required=False,
    )

    class Meta:
        model = MetadataTemplateContainer
        fields = [
            "pk",
            "name",
            "assigned_to_content_type",
            "assigned_to_object_id",
            "metadata_template_fields",
        ]

    def create(self, validated_data):
        assigned_to_content_type = validated_data.get("assigned_to_content_type", None)
        assigned_to_object_id = validated_data.get("assigned_to_object_id", None)
        metadata_template_fields = self.context["request"].data.get("metadata_template_fields", [])
        validated_data.pop("metadata_template_fields", [])

        if bool(assigned_to_content_type) ^ bool(assigned_to_object_id):
            raise ValueError(_("You must either provide a content type and an object id or none of them."))

        check_metadata_template_permissions_for_object(
            content_type=assigned_to_content_type,
            object_id=assigned_to_object_id,
            user=self.context["request"].user,
        )

        metadata_template_container = MetadataTemplateContainer.objects.create(**validated_data)

        metadata_template = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container,
        )

        for metadata_template_field in metadata_template_fields:
            value = metadata_template_field.pop("value", None)
            order = metadata_template_field.pop("order", 0)
            field = MetadataTemplateField.objects.create(
                metadata_template=metadata_template,
                order=order,
                **metadata_template_field,
            )
            field.set_value(value)

        return metadata_template_container


class MetadataTemplateContainerCloneActionPayloadSerializer(serializers.Serializer):
    assigned_to_content_type = MetadataTemplateContentTypePayloadSerializer(
        required=False,
        allow_null=True,
    )

    assigned_to_object_id = serializers.UUIDField(
        required=False,
        allow_null=True,
    )

    @property
    def has_clone_target(self):
        initial_data = self.initial_data if isinstance(self.initial_data, dict) else {}
        return "assigned_to_content_type" in initial_data or "assigned_to_object_id" in initial_data

    def validate(self, attrs):
        initial_data = self.initial_data if isinstance(self.initial_data, dict) else {}
        has_content_type = "assigned_to_content_type" in initial_data
        has_object_id = "assigned_to_object_id" in initial_data

        if has_content_type != has_object_id:
            raise serializers.ValidationError(
                _("You must either provide a content type and an object id or none of them."),
            )

        if not has_content_type and not has_object_id:
            return attrs

        assigned_to_content_type = attrs.get("assigned_to_content_type", None)
        assigned_to_object_id = attrs.get("assigned_to_object_id", None)

        try:
            clean_assigned_content_type(
                assigned_to_content_type=assigned_to_content_type,
                assigned_to_object_id=assigned_to_object_id,
                allowed_content_models=[
                    Project,
                    Folder,
                    UploadsDataset,
                ],
            )
        except DjangoValidationError as e:
            raise serializers.ValidationError(str(e))

        return attrs


class MetadataTemplateContainerUpdateSerializer(BaseModelSerializer):
    class Meta:
        model = MetadataTemplateContainer
        fields = [
            "pk",
            "name",
        ]

    def update(self, instance, validated_data):
        for key, value in validated_data.items():
            setattr(instance, key, value)

        instance.save()

        return instance


@extend_schema_field(
    {
        "type": "object",
        "properties": {
            "field": {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {
                            "key": {
                                "type": "string",
                            },
                            "field_type": {
                                "type": "string",
                                "enum": [choice[0] for choice in MetadataFieldType.choices],
                            },
                        },
                        "required": [
                            "key",
                            "field_type",
                        ],
                    },
                    {
                        "type": "string",
                        "nullable": True,
                    },
                ],
            },
            "custom_key": {
                "type": "string",
                "nullable": True,
                "maxLength": Metadata._meta.get_field("custom_key").max_length,
            },
            "field_type": {
                "type": "string",
                "enum": [choice[0] for choice in MetadataFieldType.choices],
            },
            "value": {
                "oneOf": [
                    {
                        "type": "string",
                        "nullable": True,
                    },
                    {
                        "type": "array",
                        "items": {
                            "type": "string",
                        },
                        "nullable": True,
                    },
                    {
                        "type": "object",
                        "nullable": True,
                    },
                ],
            },
            "config": {
                "type": "object",
                "nullable": True,
            },
            "previous_version_field": {
                "type": "string",
                "format": "uuid",
                "nullable": True,
            },
        },
    },
)
class MetadataTemplateFieldPayloadSerializerField(serializers.Field):
    def to_representation(self, value):
        return value

    def to_internal_value(self, data):
        return data


class MetadataTemplateFieldsPayloadSerializer(serializers.ListSerializer):
    child = MetadataTemplateFieldPayloadSerializerField()


class MetadataTemplateContainerVersionActionPayloadSerializer(serializers.Serializer):
    name = serializers.CharField(
        required=False,
        allow_null=True,
    )

    metadata_template_fields = MetadataTemplateFieldsPayloadSerializer(
        required=False,
    )


@extend_schema_field(
    {
        "type": "object",
        "properties": {
            "field": {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {
                            "key": {
                                "type": "string",
                            },
                            "field_type": {
                                "type": "string",
                                "enum": [choice[0] for choice in MetadataFieldType.choices],
                            },
                        },
                        "required": [
                            "key",
                            "field_type",
                        ],
                    },
                    {
                        "type": "string",
                        "nullable": True,
                    },
                ],
            },
            "custom_key": {
                "type": "string",
                "nullable": True,
                "maxLength": Metadata._meta.get_field("custom_key").max_length,
            },
            "field_type": {
                "type": "string",
                "enum": [choice[0] for choice in MetadataFieldType.choices],
            },
            "value": {
                "oneOf": [
                    {
                        "type": "string",
                        "nullable": True,
                    },
                    {
                        "type": "array",
                        "items": {
                            "type": "string",
                        },
                        "nullable": True,
                    },
                    {
                        "type": "object",
                        "nullable": True,
                    },
                ],
            },
            "config": {
                "type": "object",
                "nullable": True,
            },
            "metadata_template_field": {
                "type": "string",
                "format": "uuid",
                "nullable": True,
            },
            "order": {
                "type": "integer",
                "nullable": True,
            },
        },
    },
)
class MetadataPayloadSerializerField(serializers.Field):
    def to_representation(self, value):
        return value

    def to_internal_value(self, data):
        return data


class MetadataPayloadSerializer(serializers.ListSerializer):
    child = MetadataPayloadSerializerField()


@extend_schema_field(
    {
        "type": "object",
        "properties": {
            "field": {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {
                            "key": {
                                "type": "string",
                            },
                            "field_type": {
                                "type": "string",
                                "enum": [choice[0] for choice in MetadataFieldType.choices],
                            },
                        },
                        "required": [
                            "key",
                            "field_type",
                        ],
                    },
                    {
                        "type": "string",
                        "nullable": True,
                    },
                ],
            },
            "custom_key": {
                "type": "string",
                "nullable": True,
                "maxLength": Metadata._meta.get_field("custom_key").max_length,
            },
            "field_type": {
                "type": "string",
                "enum": [choice[0] for choice in MetadataFieldType.choices],
            },
            "value": {
                "oneOf": [
                    {
                        "type": "string",
                        "nullable": True,
                    },
                    {
                        "type": "array",
                        "items": {
                            "type": "string",
                        },
                        "nullable": True,
                    },
                    {
                        "type": "object",
                        "nullable": True,
                    },
                ],
            },
            "config": {
                "type": "object",
                "nullable": True,
            },
            "metadata_template_field": {
                "type": "string",
                "format": "uuid",
                "nullable": True,
            },
            "previous_version_field": {
                "type": "string",
                "format": "uuid",
                "nullable": True,
            },
            "order": {
                "type": "integer",
                "nullable": True,
            },
        },
    },
)
class MetadataUpgradePayloadSerializerField(serializers.Field):
    def to_representation(self, value):
        return value

    def to_internal_value(self, data):
        return data


class MetadataUpgradePayloadSerializer(serializers.ListSerializer):
    child = MetadataUpgradePayloadSerializerField()


class MetadataTemplateDiffValueSerializer(serializers.Serializer):
    old = MetadataTemplateFieldSerializer(
        allow_null=True,
    )

    new = MetadataTemplateFieldSerializer(
        allow_null=True,
    )


class MinimalMetadataTemplateDiffSerializer(BaseModelSerializer):
    class Meta:
        model = MetadataTemplate
        fields = [
            "pk",
            "name",
            "creation_date",
        ]


class MetadataTemplateDiffSerializer(serializers.Serializer):
    old = MinimalMetadataTemplateDiffSerializer(
        required=True,
        allow_null=False,
    )

    new = MinimalMetadataTemplateDiffSerializer(
        required=True,
        allow_null=False,
    )


class MetadataTemplateDiffResponseSerializer(serializers.Serializer):
    fields = serializers.ListSerializer(
        child=MetadataTemplateDiffValueSerializer(),
        read_only=True,
        required=False,
    )

    version = MetadataTemplateDiffSerializer(
        required=True,
    )


class MetadataTagSerializer(BaseModelSerializer):
    class Meta:
        model = MetadataTag
        fields = [
            "pk",
            "folder",
            "value",
        ]
        read_only_fields = fields
