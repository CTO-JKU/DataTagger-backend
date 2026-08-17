from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from fdm.core.rest.serializers import BaseModelWithByUserSerializer
from fdm.exports.models import ExportJob, MetadataTemplateExportJob
from fdm.exports.services import (
    create_export_job,
    create_metadata_template_export_job,
    get_allowed_export_types,
    get_viewable_export_objects,
    get_viewable_metadata_template_containers,
    normalize_export_type,
)

__all__ = [
    "ExportTypeSerializer",
    "ExportJobCreateSerializer",
    "ExportJobSerializer",
    "MetadataTemplateExportJobCreateSerializer",
    "MetadataTemplateExportJobSerializer",
]


@extend_schema_field(
    {
        "type": "string",
        "enum": get_allowed_export_types(),
    },
)
class ExportTypeSerializer(serializers.CharField):
    pass


class ExportJobCreateSerializer(serializers.Serializer):
    export_type = ExportTypeSerializer(
        required=True,
    )

    uuids = serializers.ListField(
        child=serializers.UUIDField(),
        allow_empty=False,
        required=True,
    )

    metadata_format = serializers.ChoiceField(
        choices=ExportJob.MetadataFormat.choices,
        default=ExportJob.MetadataFormat.JSON,
        required=False,
    )

    include_uploads_version_file = serializers.BooleanField(
        default=True,
        required=False,
    )

    include_uploads_version_file_metadata = serializers.BooleanField(
        default=True,
        required=False,
    )

    def validate_export_type(self, value):
        try:
            normalized_export_type, _ = normalize_export_type(value)
        except DjangoValidationError as e:
            raise serializers.ValidationError(e.messages)

        return normalized_export_type

    def validate_uuids(self, value):
        object_ids = []
        seen_object_ids = set()

        for object_id in value:
            normalized_object_id = str(object_id)
            if normalized_object_id not in seen_object_ids:
                object_ids.append(normalized_object_id)
                seen_object_ids.add(normalized_object_id)

        return object_ids

    def validate(self, attrs):
        request = self.context["request"]

        if not attrs["include_uploads_version_file"] and not attrs["include_uploads_version_file_metadata"]:
            raise serializers.ValidationError(
                {
                    "include_uploads_version_file": _(
                        "At least one of uploads version file or uploads version file metadata must be exported.",
                    ),
                },
            )

        try:
            get_viewable_export_objects(
                export_type=attrs["export_type"],
                object_ids=attrs["uuids"],
                user=request.user,
            )
        except (DjangoValidationError, PermissionDenied) as e:
            raise serializers.ValidationError({"uuids": str(e)})

        return attrs

    def create(self, validated_data):
        request = self.context["request"]

        return create_export_job(
            export_job_id=validated_data.get("export_job_id"),
            export_type=validated_data["export_type"],
            object_ids=validated_data["uuids"],
            metadata_format=validated_data["metadata_format"],
            include_uploads_version_file=validated_data["include_uploads_version_file"],
            include_uploads_version_file_metadata=validated_data["include_uploads_version_file_metadata"],
            user=request.user,
            download_base_url=validated_data.get("download_base_url"),
        )


class ExportJobSerializer(BaseModelWithByUserSerializer):
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = ExportJob
        fields = [
            "pk",
            "export_type",
            "object_ids",
            "metadata_format",
            "include_uploads_version_file",
            "include_uploads_version_file_metadata",
            "status",
            "download_url",
            "expires_at",
            "finished_at",
            "error_message",
            "creation_date",
            "last_modification_date",
            "created_by",
            "last_modified_by",
        ]
        read_only_fields = fields

    def get_download_url(self, obj):
        if obj.status != ExportJob.Status.FINISHED or obj.is_download_expired() or not obj.zip_file:
            return None

        path = reverse("export-download", kwargs={"pk": obj.pk})
        request = self.context.get("request")
        if request:
            return request.build_absolute_uri(path)

        return path


class MetadataTemplateExportJobCreateSerializer(serializers.Serializer):
    metadata_template_containers = serializers.ListField(
        child=serializers.UUIDField(),
        allow_empty=False,
        required=True,
    )

    def validate_metadata_template_containers(self, value):
        object_ids = []
        seen_object_ids = set()

        for object_id in value:
            normalized_object_id = str(object_id)
            if normalized_object_id not in seen_object_ids:
                object_ids.append(normalized_object_id)
                seen_object_ids.add(normalized_object_id)

        return object_ids

    def validate(self, attrs):
        request = self.context["request"]

        try:
            get_viewable_metadata_template_containers(
                object_ids=attrs["metadata_template_containers"],
                user=request.user,
            )
        except (DjangoValidationError, PermissionDenied) as e:
            raise serializers.ValidationError({"metadata_template_containers": str(e)})

        return attrs

    def create(self, validated_data):
        request = self.context["request"]

        return create_metadata_template_export_job(
            export_job_id=validated_data.get("export_job_id"),
            metadata_template_container_ids=validated_data["metadata_template_containers"],
            user=request.user,
            download_base_url=validated_data.get("download_base_url"),
        )


class MetadataTemplateExportJobSerializer(BaseModelWithByUserSerializer):
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = MetadataTemplateExportJob
        fields = [
            "pk",
            "metadata_template_container_ids",
            "status",
            "download_url",
            "expires_at",
            "finished_at",
            "error_message",
            "creation_date",
            "last_modification_date",
            "created_by",
            "last_modified_by",
        ]
        read_only_fields = fields

    def get_download_url(self, obj):
        if obj.status != MetadataTemplateExportJob.Status.FINISHED or obj.is_download_expired() or not obj.zip_file:
            return None

        path = reverse("metadata-template-export-download", kwargs={"pk": obj.pk})
        request = self.context.get("request")
        if request:
            return request.build_absolute_uri(path)

        return path
