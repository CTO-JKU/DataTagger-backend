from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from fdm.core.rest.base import BaseModelSerializer
from fdm.core.rest.serializers import BaseModelWithByUserSerializer
from fdm.folders.helpers import add_folder_permissions_for_users
from fdm.folders.models import *
from fdm.metadata.enums import MetadataFieldType
from fdm.metadata.helpers import create_metadata_template_for_object, set_metadata_for_relation
from fdm.metadata.models import Metadata, MetadataField, MetadataTemplate
from fdm.metadata.rest.serializers import (
    MetadataPayloadSerializer,
    MetadataTemplateSerializer,
    MinimalMetadataSerializer,
    MinimalMetadataTemplateSerializer,
)
from fdm.projects.models import Project, ProjectMembership
from fdm.storages.rest.serializers import StorageSerializer

__all__ = [
    "FolderBidirectionalSerializer",
    "MinimalProjectSearchSerializer",
    "MetadataTemplateGetOrCreateSerializer",
    "FolderUsersSerializer",
    "FolderPermissionsActionPayloadSerializer",
    "FolderCreatePayloadSerializer",
    "FolderUpdatePayloadSerializer",
    "FolderListSerializer",
    "FolderCreateSerializer",
    "FolderUpdateSerializer",
    "FolderSerializer",
    "FolderSearchSerializer",
    "FolderPermissionCreateSerializer",
    "FolderPermissionUpdateSerializer",
    "FolderPermissionSerializer",
    "MinimalFolderSerializer",
    "MinimalSubfolderSerializer",
    "SubfolderSerializer",
    "SubfolderCreateSerializer",
    "SubfolderUpdateSerializer",
    "SubfolderMetadataPayloadSerializer",
    "SubfolderSearchSerializer",
]


@extend_schema_field(
    {
        "type": "string",
        "format": "uuid",
    },
)
class FolderBidirectionalSerializer(serializers.Field):
    def to_representation(self, value):
        return FolderSearchSerializer(value).data

    def to_internal_value(self, data):
        if not data:
            return None

        try:
            return Folder.objects.get(pk=data)
        except Folder.DoesNotExist:
            raise serializers.ValidationError("A folder with this primary key does not exist.")


class MinimalProjectSearchSerializer(BaseModelWithByUserSerializer):
    class Meta:
        model = Project

        fields = [
            "pk",
            "name",
        ]


@extend_schema_field(
    {
        "oneOf": [
            {
                "type": "string",
                "format": "uuid",
            },
            {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                    },
                    "metadata_template_fields": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "field": {
                                    "type": "object",
                                    "properties": {
                                        "key": {
                                            "type": "string",
                                            "maxLength": MetadataField._meta.get_field("key").max_length,
                                        },
                                        "field_type": {
                                            "type": "string",
                                            "enum": [choice[0] for choice in MetadataFieldType.choices],
                                        },
                                        "read_only": {
                                            "type": "boolean",
                                        },
                                    },
                                    "required": [
                                        "key",
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
                                "mandatory": {
                                    "type": "boolean",
                                },
                            },
                        },
                    },
                },
                "required": [
                    "name",
                    "metadata_template_fields",
                ],
            },
        ],
    },
)
class MetadataTemplateGetOrCreateSerializer(serializers.Field):
    def to_representation(self, value):
        return MetadataTemplateSerializer(value).data

    def to_internal_value(self, data):
        if isinstance(data, str):
            try:
                return MetadataTemplate.objects.get(pk=data)
            except MetadataTemplate.DoesNotExist:
                raise serializers.ValidationError("A metadata template with this primary key does not exist.")

        if isinstance(data, dict):
            return create_metadata_template_for_object(
                metadata_template_fields=data.pop("metadata_template_fields", []),
                metadata_template_data=data,
            )

        return None


class FolderUsersSerializer(serializers.Serializer):
    email = serializers.EmailField()

    is_folder_admin = serializers.BooleanField()

    is_metadata_template_admin = serializers.BooleanField()

    can_edit = serializers.BooleanField()


class FolderPermissionsActionPayloadSerializer(serializers.Serializer):
    folder_users = FolderUsersSerializer(
        many=True,
        required=True,
    )


class FolderCreatePayloadSerializer(serializers.Serializer):
    name = serializers.CharField(
        required=True,
        allow_null=False,
    )

    description = serializers.JSONField(
        allow_null=False,
        default=dict,
    )

    project = serializers.UUIDField(
        required=False,
        allow_null=True,
    )

    storage = serializers.UUIDField(
        required=False,
        allow_null=True,
    )

    metadata_template = MetadataTemplateGetOrCreateSerializer(
        required=False,
        allow_null=True,
    )

    metadata = MetadataPayloadSerializer(
        required=False,
    )

    folder_users = FolderUsersSerializer(
        many=True,
        required=False,
    )


class FolderUpdatePayloadSerializer(serializers.Serializer):
    name = serializers.CharField(
        required=True,
        allow_null=False,
    )

    description = serializers.JSONField(
        allow_null=False,
        default=dict,
    )

    storage = serializers.UUIDField(
        required=False,
        allow_null=True,
    )

    metadata_template = MetadataTemplateGetOrCreateSerializer(
        required=False,
        allow_null=True,
    )

    metadata = MetadataPayloadSerializer(
        required=False,
    )


class FolderListSerializer(BaseModelWithByUserSerializer):
    project = MinimalProjectSearchSerializer()

    storage = StorageSerializer(
        required=False,
        allow_null=True,
    )

    metadata_template = MinimalMetadataTemplateSerializer(
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Folder
        fields = [
            "pk",
            "name",
            "project",
            "storage",
            "metadata_template",
            "datasets_count",
            "members_count",
            "metadata_templates_count",
        ]


class FolderCreateSerializer(BaseModelWithByUserSerializer):
    metadata_template = MetadataTemplateGetOrCreateSerializer(
        required=False,
        allow_null=True,
    )

    metadata = MinimalMetadataSerializer(
        many=True,
        required=False,
    )

    folder_users = FolderUsersSerializer(
        many=True,
        required=False,
    )

    class Meta:
        model = Folder

        fields = [
            "project",
            "name",
            "description",
            "storage",
            "metadata_template",
            "metadata",
            "folder_users",
        ]

    def create(self, validated_data):
        folder_users = validated_data.pop("folder_users", [])
        metadata_template = validated_data.get("metadata_template", None)
        metadata_template_payload = self.context["request"].data.get("metadata_template", None)
        metadata = None

        if "metadata" in validated_data:
            metadata = self.context["request"].data.get("metadata", [])
            validated_data.pop("metadata", [])

        folder = Folder.objects.create(**validated_data)

        add_folder_permissions_for_users(folder, folder_users)

        if metadata is not None:
            set_metadata_for_relation(
                metadata_list=metadata,
                relation=folder,
            )

        # If the metadata template payload is of type dict then it has just been created and must also be linked
        # to the appropriate content type.
        if isinstance(metadata_template_payload, dict):
            metadata_template.metadata_template_container.assigned_to_content_type = folder.get_content_type()
            metadata_template.metadata_template_container.assigned_to_object_id = folder.pk
            metadata_template.metadata_template_container.save()

        return folder


class FolderUpdateSerializer(BaseModelWithByUserSerializer):
    metadata_template = MetadataTemplateGetOrCreateSerializer(
        required=False,
        allow_null=True,
    )

    metadata = MinimalMetadataSerializer(
        many=True,
        required=False,
    )

    class Meta:
        model = Folder

        fields = [
            "name",
            "description",
            "storage",
            "metadata_template",
            "metadata",
        ]

    def update(self, instance, validated_data):
        metadata_template = validated_data.get("metadata_template", None)
        metadata_template_payload = self.context["request"].data.get("metadata_template", None)
        metadata = None

        if "metadata" in validated_data:
            metadata = self.context["request"].data.get("metadata", [])
            validated_data.pop("metadata", [])

        for key, value in validated_data.items():
            setattr(instance, key, value)

        instance.save()

        if metadata is not None:
            set_metadata_for_relation(
                metadata_list=metadata,
                relation=instance,
            )

        # If the metadata template payload is of type dict then it has just been created and must also be linked
        # to the appropriate content type.
        if isinstance(metadata_template_payload, dict):
            metadata_template.metadata_template_container.assigned_to_content_type = instance.get_content_type()
            metadata_template.metadata_template_container.assigned_to_object_id = instance.pk
            metadata_template.metadata_template_container.save()

        return instance

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return super().update(request, *args, **kwargs)


class FolderSerializer(BaseModelWithByUserSerializer):
    project = MinimalProjectSearchSerializer()

    storage = StorageSerializer()

    metadata_template = MetadataTemplateSerializer(
        required=False,
        allow_null=True,
    )

    metadata = MinimalMetadataSerializer(
        many=True,
        required=False,
    )

    class Meta:
        model = Folder

        fields = [
            "pk",
            "project",
            "name",
            "description",
            "storage",
            "metadata_template",
            "datasets_count",
            "members_count",
            "metadata_templates_count",
            "metadata",
        ]

        read_only_fields = [
            "datasets_count",
            "members_count",
            "metadata_templates_count",
        ]


class FolderSearchSerializer(BaseModelWithByUserSerializer):
    project = MinimalProjectSearchSerializer()

    storage = StorageSerializer()

    class Meta:
        model = Folder
        fields = [
            "pk",
            "project",
            "name",
            "storage",
        ]


class MinimalFolderSerializer(BaseModelSerializer):
    class Meta:
        model = Folder
        fields = [
            "pk",
            "name",
        ]


class FolderPermissionCreateSerializer(BaseModelSerializer):
    from fdm.users.rest.serializers import GetOrCreateUserSerializer

    member = GetOrCreateUserSerializer()

    is_folder_admin = serializers.BooleanField()

    is_metadata_template_admin = serializers.BooleanField()

    can_edit = serializers.BooleanField()

    class Meta:
        model = FolderPermission
        fields = [
            "pk",
            "folder",
            "member",
            "is_folder_admin",
            "is_metadata_template_admin",
            "can_edit",
        ]

    def create(self, validated_data):
        """Create a new folder permission instance."""
        member = validated_data.pop("member")
        folder = validated_data.pop("folder")
        is_folder_admin = validated_data.pop("is_folder_admin")
        is_metadata_template_admin = validated_data.pop("is_metadata_template_admin")
        can_edit = validated_data.pop("can_edit")

        try:
            project_membership = ProjectMembership.objects.get(
                member=member,
                project=folder.project,
            )
        except ProjectMembership.DoesNotExist:
            project_membership = ProjectMembership.objects.create(
                member=member,
                project=folder.project,
            )

        folder_permission = FolderPermission.objects.create(
            folder=folder,
            project_membership=project_membership,
            is_folder_admin=is_folder_admin,
            is_metadata_template_admin=is_metadata_template_admin,
            can_edit=can_edit,
            **validated_data,
        )

        return folder_permission


class FolderPermissionUpdateSerializer(BaseModelSerializer):
    is_folder_admin = serializers.BooleanField()

    is_metadata_template_admin = serializers.BooleanField()

    can_edit = serializers.BooleanField()

    class Meta:
        model = FolderPermission
        fields = [
            "pk",
            "is_folder_admin",
            "is_metadata_template_admin",
            "can_edit",
        ]

    def update(self, instance, validated_data):
        return super().update(instance, validated_data)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return super().update(request, *args, **kwargs)


class FolderPermissionSerializer(BaseModelSerializer):
    from fdm.projects.rest.serializers import ProjectMembershipSerializer

    project_membership = ProjectMembershipSerializer(
        read_only=True,
    )

    class Meta:
        model = FolderPermission
        fields = [
            "pk",
            "folder",
            "project_membership",
            "is_folder_admin",
            "is_metadata_template_admin",
            "can_edit",
        ]


class MinimalSubfolderSerializer(BaseModelSerializer):
    class Meta:
        model = Subfolder
        fields = [
            "pk",
            "name",
        ]


class SubfolderMetadataPayloadSerializer(serializers.Serializer):
    # Same format as the metadata of folders and files: custom_key, field_type, value and config per entry
    metadata = MetadataPayloadSerializer()

    def validate_metadata(self, value):
        keys = []

        for entry in value:
            if not isinstance(entry, dict):
                raise serializers.ValidationError("Invalid metadata entry.")

            key = (entry.get("custom_key") or "").strip()
            if not key:
                raise serializers.ValidationError("Please name every metadata field.")

            keys.append(key.lower())

        if len(set(keys)) != len(keys):
            raise serializers.ValidationError("Each field name can only be used once.")

        return [
            {
                "custom_key": entry["custom_key"].strip(),
                "field_type": entry.get("field_type") or MetadataFieldType.TEXT,
                "value": entry.get("value"),
                "config": entry.get("config") or {},
                "order": order,
            }
            for order, entry in enumerate(value)
        ]


class SubfolderSerializer(BaseModelWithByUserSerializer):
    path = serializers.SerializerMethodField()

    metadata = MinimalMetadataSerializer(
        many=True,
        read_only=True,
    )

    datasets_count = serializers.SerializerMethodField()

    subfolders_count = serializers.SerializerMethodField()

    class Meta:
        model = Subfolder
        fields = [
            "pk",
            "folder",
            "parent",
            "name",
            "path",
            "datasets_count",
            "subfolders_count",
            "metadata",
        ]

    @extend_schema_field(MinimalSubfolderSerializer(many=True))
    def get_path(self, obj):
        return MinimalSubfolderSerializer(obj.get_path(), many=True).data

    @extend_schema_field(serializers.IntegerField())
    def get_datasets_count(self, obj):
        annotated = getattr(obj, "datasets_count_annotated", None)
        if annotated is not None:
            return annotated
        return obj.uploads_dataset.filter(publication_date__isnull=False).count()

    @extend_schema_field(serializers.IntegerField())
    def get_subfolders_count(self, obj):
        annotated = getattr(obj, "subfolders_count_annotated", None)
        if annotated is not None:
            return annotated
        return obj.children.count()


class SubfolderCreateSerializer(BaseModelSerializer):
    class Meta:
        model = Subfolder
        fields = [
            "folder",
            "parent",
            "name",
        ]


class SubfolderUpdateSerializer(BaseModelSerializer):
    class Meta:
        model = Subfolder
        fields = [
            "parent",
            "name",
        ]
        extra_kwargs = {
            "name": {
                "required": False,
            },
        }


class SubfolderSearchSerializer(BaseModelWithByUserSerializer):
    folder = FolderSearchSerializer()

    path = serializers.SerializerMethodField()

    metadata = MinimalMetadataSerializer(
        many=True,
        read_only=True,
    )

    class Meta:
        model = Subfolder
        fields = [
            "pk",
            "name",
            "folder",
            "path",
            "metadata",
        ]

    @extend_schema_field(MinimalSubfolderSerializer(many=True))
    def get_path(self, obj):
        return MinimalSubfolderSerializer(obj.get_path(), many=True).data
