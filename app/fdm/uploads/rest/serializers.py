import logging

from drf_spectacular.utils import extend_schema, extend_schema_field
from rest_framework import serializers

from fdm.core.rest.base import BaseModelSerializer
from fdm.core.rest.serializers import BaseModelWithByUserSerializer
from fdm.folders.rest.serializers import (
    FolderBidirectionalSerializer,
    FolderSearchSerializer,
    FolderSerializer,
    MetadataTemplateGetOrCreateSerializer,
)
from fdm.metadata.enums import MetadataFieldType
from fdm.metadata.rest.serializers import (
    MetadataFieldSerializer,
    MetadataPayloadSerializer,
    MetadataTemplateFieldSerializer,
    MetadataTemplateSerializer,
    MinimalMetadataSerializer,
    MinimalMetadataTemplateSerializer,
)
from fdm.uploads.helpers import create_uploads_version_with_new_metadata_for_dataset
from fdm.uploads.models import *

__all__ = [
    "UploadsVersionStatusSerializer",
    "UploadsVersionsForUploadsDatasetListSerializer",
    "MinimalUploadsVersionFileSerializer",
    "MinimalUploadsVersionFileSearchSerializer",
    "MinimalFlatUploadsVersionSerializer",
    "UploadsVersionsForUploadsDatasetSerializer",
    "MinimalUploadsVersionSerializer",
    "MinimalUploadsDatasetSerializer",
    "UploadsDatasetSerializer",
    "UploadsDatasetCreatePayloadSerializer",
    "UploadsDatasetCreateSerializer",
    "UploadsDatasetUpdatePayloadSerializer",
    "UploadsDatasetUpdateSerializer",
    "UploadsDatasetBulkDeleteSerializer",
    "UploadsDatasetBulkPublishSerializer",
    "UploadsDatasetBulkFillMetadataTemplateFieldsPayloadSerializer",
    "UploadsDatasetBulkFillMetadataTemplateFieldsResponseSerializer",
    "UploadsDatasetBulkFillMetadataTemplateFieldsSubmitPayloadSerializer",
    "UploadsDatasetBulkFillMetadataTemplateFieldsSubmitResponseSerializer",
    "UploadsDatasetListSerializer",
    "MinimalUploadsDatasetSearchSerializer",
    "UploadsVersionPayloadSerializer",
    "UploadsDatasetVersionActionPayloadSerializer",
    "UploadsDatasetUpgradeMetadataPayloadSerializer",
    "UploadsDatasetResolveUpgradeMetadataConflictPayloadSerializer",
    "UploadsVersionSerializer",
    "MinimalUploadsVersionSearchSerializer",
    "UploadsVersionSearchSerializer",
    "UploadsDatasetSearchSerializer",
    "UploadsVersionFileSerializer",
    "UploadsVersionFileSearchSerializer",
    "UploadsVersionDiffValueSerializer",
    "MinimalUploadsVersionFileDiffSerializer",
    "UploadsVersionFileDiffSerializer",
    "MinimalUploadsVersionDiffSerializer",
    "UploadsVersionDiffSerializer",
    "UploadsVersionDiffResponseSerializer",
]

logger = logging.getLogger(__name__)


@extend_schema_field(
    {
        "type": "string",
        "enum": [choice[0] for choice in UploadsVersion.Status.choices],
    },
)
class UploadsVersionStatusSerializer(serializers.Serializer):
    pass


class UploadsVersionsForUploadsDatasetListSerializer(serializers.ListSerializer):
    def to_representation(self, data):
        data = data.order_by(
            "-creation_date",
        )
        return super().to_representation(data)


class MinimalUploadsVersionFileSerializer(BaseModelWithByUserSerializer):
    metadata = MinimalMetadataSerializer(
        many=True,
        read_only=True,
    )

    class Meta:
        model = UploadsVersionFile
        fields = [
            "pk",
            "name",
            "status",
            "storage_relocating",
            "is_published",
            "publication_date",
            "metadata",
            "is_referenced",
        ]


class MinimalUploadsVersionFileSearchSerializer(BaseModelWithByUserSerializer):
    class Meta:
        model = UploadsVersionFile
        fields = [
            "pk",
            "name",
        ]


class MinimalFlatUploadsVersionSerializer(BaseModelSerializer):
    class Meta:
        model = UploadsVersion
        fields = [
            "pk",
            "dataset",
        ]


class _NestedDatasetPkNameSerializer(BaseModelSerializer):
    class Meta:
        model = UploadsDataset
        fields = [
            "pk",
            "name",
        ]


class MinimalFlatUploadsVersionSearchSerializer(BaseModelSerializer):
    dataset = _NestedDatasetPkNameSerializer(read_only=True)

    class Meta:
        model = UploadsVersion
        fields = [
            "pk",
            "name",
            "dataset",
        ]


class UploadsVersionsForUploadsDatasetSerializer(BaseModelSerializer):
    class Meta:
        model = UploadsVersion
        list_serializer_class = UploadsVersionsForUploadsDatasetListSerializer
        fields = [
            "pk",
            "name",
            "creation_date",
            "is_published",
            "publication_date",
        ]


class MinimalUploadsVersionSerializer(BaseModelSerializer):
    version_file = MinimalUploadsVersionFileSerializer(
        read_only=True,
    )

    class Meta:
        model = UploadsVersion
        fields = [
            "pk",
            "name",
            "metadata_is_complete",
            "version_file",
            "creation_date",
        ]


class MinimalUploadsDatasetSerializer(BaseModelSerializer):
    class Meta:
        model = UploadsDataset
        fields = [
            "pk",
            "name",
            "display_name",
        ]


class UploadsDatasetSerializer(BaseModelWithByUserSerializer):
    folder = FolderSerializer(
        required=False,
        allow_null=True,
    )

    metadata_template = MetadataTemplateSerializer(
        required=False,
        allow_null=True,
    )

    uploads_versions = UploadsVersionsForUploadsDatasetSerializer(
        many=True,
        read_only=True,
    )

    latest_version = MinimalUploadsVersionSerializer(
        required=False,
        allow_null=True,
    )

    class Meta:
        model = UploadsDataset
        fields = [
            "pk",
            "name",
            "display_name",
            "folder",
            "metadata_template",
            "uploads_versions",
            "latest_version",
            "is_published",
            "publication_date",
            "deletable_until_date",
            "is_expired",
            "expiry_date",
            "uploads_versions_count",
            "status",
        ]

        read_only_fields = [
            "deletable_until_date",
            "uploads_versions_count",
        ]


class UploadsDatasetCreatePayloadSerializer(serializers.Serializer):
    name = serializers.CharField(
        required=False,
        allow_null=True,
    )

    folder = serializers.UUIDField(
        required=False,
        allow_null=True,
    )

    # metadata_template = MetadataTemplateGetOrCreateSerializer(
    #     required=False,
    #     allow_null=True,
    # )


class UploadsDatasetCreateSerializer(BaseModelWithByUserSerializer):
    folder = FolderBidirectionalSerializer(
        required=False,
        allow_null=True,
    )

    metadata_template = MetadataTemplateGetOrCreateSerializer(
        required=False,
        allow_null=True,
    )

    uploads_versions = UploadsVersionsForUploadsDatasetSerializer(
        many=True,
        read_only=True,
    )

    latest_version = MinimalUploadsVersionSerializer(
        required=False,
        allow_null=True,
    )

    class Meta:
        model = UploadsDataset

        fields = [
            "name",
            "folder",
            "metadata_template",
            "uploads_versions",
            "latest_version",
            "is_published",
            "publication_date",
            "is_expired",
            "expiry_date",
        ]

    def create(self, validated_data):
        # TODO: Creating metadata templates for datasets can only be possible in the future,
        #  when we allow datasets without files
        # metadata_template = validated_data.get("metadata_template", None)
        # metadata_template_payload = self.context["request"].data.get("metadata_template", None)

        uploads_dataset = UploadsDataset.objects.create(**validated_data)

        # If the metadata template payload is of type dict, then it has just been created and must also be linked
        # to the appropriate content type.
        # if isinstance(metadata_template_payload, dict):
        #     metadata_template.metadata_template_container.assigned_to_content_type = uploads_dataset.get_content_type()
        #     metadata_template.metadata_template_container.assigned_to_object_id = uploads_dataset.pk
        #     metadata_template.metadata_template_container.save()

        # When a metadata template is directly assigned to a dataset, create a new version and automatically add all the
        # template fields for the linked metadata template.
        # if metadata_template:
        #     metadata_list = metadata_template.metadata_template_fields.all()
        #     if metadata_list:
        #         try:
        #             create_uploads_version_with_new_metadata_for_dataset(
        #                 dataset=uploads_dataset,
        #                 metadata_list=metadata_list,
        #                 retain_existing_metadata=True,
        #             )
        #         except Exception as e:
        #             logger.error(
        #                 f"Could not apply metadata from metadata template fields to uploads dataset {self}: {e}",
        #             )

        return uploads_dataset


class UploadsDatasetUpdatePayloadSerializer(serializers.Serializer):
    name = serializers.CharField(
        required=False,
        allow_null=True,
    )

    metadata_template = MetadataTemplateGetOrCreateSerializer(
        required=False,
        allow_null=True,
    )


class UploadsDatasetUpdateSerializer(BaseModelWithByUserSerializer):
    folder = FolderBidirectionalSerializer(
        required=False,
        allow_null=True,
    )

    metadata_template = MetadataTemplateGetOrCreateSerializer(
        required=False,
        allow_null=True,
    )

    uploads_versions = UploadsVersionsForUploadsDatasetSerializer(
        many=True,
        read_only=True,
    )

    latest_version = MinimalUploadsVersionSerializer(
        required=False,
        allow_null=True,
    )

    class Meta:
        model = UploadsDataset

        fields = [
            "name",
            "folder",
            "metadata_template",
            "uploads_versions",
            "latest_version",
            "is_published",
            "publication_date",
            "is_expired",
            "expiry_date",
        ]

    def update(self, instance, validated_data):
        metadata_template = validated_data.get("metadata_template", None)
        metadata_template_payload = self.context["request"].data.get("metadata_template", None)

        try:
            validated_data.pop("folder")
        except KeyError:
            pass

        for key, value in validated_data.items():
            setattr(instance, key, value)

        instance.save()

        # If the metadata template payload is of type dict, then it has just been created and must also be linked
        # to the appropriate content type.
        if isinstance(metadata_template_payload, dict):
            metadata_template.metadata_template_container.assigned_to_content_type = instance.get_content_type()
            metadata_template.metadata_template_container.assigned_to_object_id = instance.pk
            metadata_template.metadata_template_container.save()

        # When a metadata template is directly assigned to a dataset, create a new version and automatically add all the
        # template fields for the linked metadata template. For the update process, this should only be invoked when
        # there was no assignment of a metadata template before the update or the metadata template assignment has
        # changed.
        if (
            metadata_template
            and instance.latest_version
            and instance.latest_version.metadata_templates
            and not instance.latest_version.metadata_templates.filter(pk=metadata_template.pk).exists()
        ):
            metadata_list = metadata_template.metadata_template_fields.all()
            if metadata_list:
                try:
                    create_uploads_version_with_new_metadata_for_dataset(
                        dataset=instance,
                        metadata_list=metadata_list,
                        retain_existing_metadata=True,
                        link_currently_active_metadata_templates=True,
                    )
                except Exception as e:
                    logger.error(
                        f"Could not apply metadata from metadata template fields to uploads dataset {self}: {e}",
                    )

        return instance

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return super().update(request, *args, **kwargs)


class UploadsDatasetBulkDeleteSerializer(serializers.Serializer):
    success = serializers.ListSerializer(
        child=serializers.UUIDField(),
        required=True,
    )

    error = serializers.ListSerializer(
        child=serializers.UUIDField(),
        required=True,
    )


class UploadsDatasetBulkPublishErrorSerializer(serializers.Serializer):
    pk = serializers.UUIDField(
        required=True,
    )

    error = serializers.CharField(
        required=True,
    )


class UploadsDatasetBulkPublishSerializer(serializers.Serializer):
    success = serializers.ListSerializer(
        child=serializers.UUIDField(),
        required=True,
    )

    error = UploadsDatasetBulkPublishErrorSerializer(
        many=True,
        required=True,
    )


class UploadsDatasetBulkFillMetadataTemplateFieldsSkipReasonSerializer(serializers.Serializer):
    code = serializers.ChoiceField(
        choices=[
            "PENDING_TEMPLATE_UPGRADE",
            "NOT_IN_FOLDER_SCOPE",
            "NO_INHERITED_TEMPLATE_FIELDS",
            "STALE_BASE_VERSION",
            "NO_CHANGES",
        ],
        required=True,
    )

    count = serializers.IntegerField(
        required=True,
    )


class UploadsDatasetBulkFillMetadataTemplateFieldsWarningSerializer(serializers.Serializer):
    code = serializers.ChoiceField(
        choices=[
            "DIFFERENT_DATASET_METADATA_TEMPLATES",
        ],
        required=True,
    )


class UploadsDatasetBulkFillMetadataTemplateFieldsEligibleSerializer(serializers.Serializer):
    pk = serializers.UUIDField(
        required=True,
    )

    display_name = serializers.CharField(
        required=False,
        allow_null=True,
    )

    base_version_pk = serializers.UUIDField(
        required=True,
        allow_null=True,
    )


class UploadsDatasetBulkFillMetadataTemplateFieldsFieldSerializer(serializers.Serializer):
    pk = serializers.UUIDField(
        required=True,
    )

    metadata_template = MetadataTemplateSerializer(
        required=True,
    )

    field = MetadataFieldSerializer(
        required=False,
        allow_null=True,
    )

    custom_key = serializers.CharField(
        required=True,
    )

    field_type = serializers.ChoiceField(
        choices=MetadataFieldType.choices,
        required=True,
    )

    value = serializers.JSONField(
        required=False,
        allow_null=True,
    )

    config = serializers.JSONField(
        required=True,
    )

    mandatory = serializers.BooleanField(
        required=True,
    )

    previous_version_field = serializers.UUIDField(
        required=False,
        allow_null=True,
    )

    order = serializers.IntegerField(
        required=False,
        allow_null=True,
    )

    various_inputs = serializers.BooleanField(
        required=True,
    )


class UploadsDatasetBulkFillMetadataTemplateFieldsResponseSerializer(serializers.Serializer):
    eligible_uploads_datasets = UploadsDatasetBulkFillMetadataTemplateFieldsEligibleSerializer(
        many=True,
        required=True,
    )

    skipped_uploads_datasets_count = serializers.IntegerField(
        required=True,
    )

    skip_reasons = UploadsDatasetBulkFillMetadataTemplateFieldsSkipReasonSerializer(
        many=True,
        required=True,
    )

    warnings = UploadsDatasetBulkFillMetadataTemplateFieldsWarningSerializer(
        many=True,
        required=True,
    )

    has_skipped_due_to_pending_template_upgrade = serializers.BooleanField(
        required=True,
    )

    fields = UploadsDatasetBulkFillMetadataTemplateFieldsFieldSerializer(
        many=True,
        required=True,
    )


class UploadsDatasetBulkFillMetadataTemplateFieldsPayloadSerializer(serializers.Serializer):
    uploads_datasets = serializers.ListSerializer(
        child=serializers.UUIDField(),
        required=True,
    )


class UploadsDatasetBulkFillMetadataTemplateFieldsSubmitBaseVersionSerializer(serializers.Serializer):
    uploads_dataset_pk = serializers.UUIDField(
        required=True,
    )

    base_version_pk = serializers.UUIDField(
        required=True,
        allow_null=True,
    )


class UploadsDatasetBulkFillMetadataTemplateFieldsSubmitFieldSerializer(serializers.Serializer):
    metadata_template_field_pk = serializers.UUIDField(
        required=True,
    )

    value = serializers.JSONField(
        required=True,
        allow_null=True,
    )


class UploadsDatasetBulkFillMetadataTemplateFieldsSubmitPayloadSerializer(serializers.Serializer):
    targets = UploadsDatasetBulkFillMetadataTemplateFieldsSubmitBaseVersionSerializer(
        many=True,
        required=True,
    )

    fields = UploadsDatasetBulkFillMetadataTemplateFieldsSubmitFieldSerializer(
        many=True,
        required=True,
    )


class UploadsDatasetBulkFillMetadataTemplateFieldsSubmitResponseSerializer(serializers.Serializer):
    updated_uploads_datasets_count = serializers.IntegerField(
        required=True,
    )

    skipped_uploads_datasets_count = serializers.IntegerField(
        required=True,
    )

    skip_reasons = UploadsDatasetBulkFillMetadataTemplateFieldsSkipReasonSerializer(
        many=True,
        required=True,
    )


class UploadsDatasetListSerializer(BaseModelWithByUserSerializer):
    folder = FolderSearchSerializer(
        required=False,
        allow_null=True,
    )

    metadata_template = MetadataTemplateSerializer(
        required=False,
        allow_null=True,
    )

    uploads_versions = UploadsVersionsForUploadsDatasetSerializer(
        many=True,
        read_only=True,
    )

    latest_version = MinimalUploadsVersionSerializer(
        required=False,
        allow_null=True,
    )

    class Meta:
        model = UploadsDataset
        fields = [
            "pk",
            "name",
            "display_name",
            "folder",
            "metadata_template",
            "uploads_versions",
            "latest_version",
            "is_published",
            "publication_date",
            "deletable_until_date",
            "is_expired",
            "expiry_date",
            "uploads_versions_count",
            "has_upgradeable_metadata_templates",
            "metadata_upgrade_status",
        ]


class MinimalUploadsDatasetSearchSerializer(BaseModelWithByUserSerializer):
    class Meta:
        model = UploadsDataset
        fields = [
            "pk",
            "name",
            "display_name",
        ]


class MinimalUploadsDatasetWithFolderSearchSerializer(BaseModelWithByUserSerializer):
    folder = FolderSearchSerializer(
        required=False,
        allow_null=True,
    )

    class Meta:
        model = UploadsDataset
        fields = [
            "pk",
            "name",
            "folder",
        ]


class UploadsVersionPayloadSerializer(serializers.Serializer):
    name = serializers.CharField(
        required=False,
        allow_null=True,
    )

    status = UploadsVersionStatusSerializer(
        required=False,
    )


class UploadsDatasetVersionActionPayloadSerializer(serializers.Serializer):
    name = serializers.CharField(
        required=False,
        allow_null=True,
    )

    metadata = MetadataPayloadSerializer(
        required=False,
    )


class UploadsDatasetUpgradeMetadataConflictValueSerializer(serializers.Serializer):
    field = MetadataFieldSerializer(
        required=False,
        allow_null=True,
    )

    custom_key = serializers.CharField(
        required=False,
        allow_null=True,
    )

    field_type = serializers.ChoiceField(
        choices=MetadataFieldType.choices,
        required=False,
    )

    value = serializers.JSONField(
        required=False,
        allow_null=True,
    )

    config = serializers.JSONField(
        required=False,
        allow_null=True,
    )

    metadata_template_field = MetadataTemplateFieldSerializer(
        required=False,
        allow_null=True,
    )

    previous_version_field = serializers.UUIDField(
        required=False,
        allow_null=True,
    )

    order = serializers.IntegerField(
        required=False,
        allow_null=True,
    )


class UploadsDatasetUpgradeMetadataConflictSerializer(serializers.Serializer):
    conflict_id = serializers.CharField(
        required=True,
    )

    error = serializers.CharField(
        required=True,
    )

    old = UploadsDatasetUpgradeMetadataConflictValueSerializer(
        required=True,
    )

    new = UploadsDatasetUpgradeMetadataConflictValueSerializer(
        required=True,
    )


class UploadsDatasetUpgradeMetadataTemplateVersionSerializer(serializers.Serializer):
    container_pk = serializers.UUIDField(
        required=True,
    )

    old_template_pk = serializers.UUIDField(
        required=False,
        allow_null=True,
    )

    new_template_pk = serializers.UUIDField(
        required=False,
        allow_null=True,
    )


class UploadsDatasetUpgradeMetadataPayloadSerializer(serializers.Serializer):
    base_version_pk = serializers.UUIDField(
        required=True,
        allow_null=True,
    )

    template_versions = UploadsDatasetUpgradeMetadataTemplateVersionSerializer(
        many=True,
        required=True,
    )

    conflicts = UploadsDatasetUpgradeMetadataConflictSerializer(
        many=True,
        required=True,
    )


class UploadsDatasetResolveUpgradeMetadataConflictResolutionSerializer(serializers.Serializer):
    conflict_id = serializers.CharField(
        required=True,
    )

    value = serializers.JSONField(
        required=True,
        allow_null=True,
    )


class UploadsDatasetResolveUpgradeMetadataConflictPayloadSerializer(serializers.Serializer):
    base_version_pk = serializers.UUIDField(
        required=True,
    )

    resolutions = UploadsDatasetResolveUpgradeMetadataConflictResolutionSerializer(
        many=True,
        required=True,
    )


class UploadsVersionSerializer(BaseModelWithByUserSerializer):
    metadata = MinimalMetadataSerializer(
        many=True,
        required=False,
    )

    dataset = MinimalUploadsDatasetSerializer(
        read_only=True,
    )

    version_file = MinimalUploadsVersionFileSerializer(
        read_only=True,
    )

    metadata_templates = MinimalMetadataTemplateSerializer(
        many=True,
        read_only=True,
    )

    restored_uploads_version = MinimalUploadsVersionSerializer(
        allow_null=True,
        read_only=True,
    )

    class Meta:
        model = UploadsVersion
        fields = [
            "pk",
            "name",
            "dataset",
            "version_file",
            "is_published",
            "publication_date",
            "metadata_is_complete",
            "metadata",
            "status",
            "metadata_templates",
            "restored_uploads_version",
        ]

    @extend_schema(
        request=UploadsVersionPayloadSerializer,
    )
    def update(self, instance, validated_data):
        for key in list(validated_data.keys()):
            if key not in ["name", "status"]:
                del validated_data[key]

        return super().update(instance, validated_data)

    @extend_schema(
        request=UploadsVersionPayloadSerializer,
    )
    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return super().update(request, *args, **kwargs)


class MinimalUploadsVersionSearchSerializer(BaseModelWithByUserSerializer):
    version_file = MinimalUploadsVersionFileSearchSerializer(
        required=True,
    )

    class Meta:
        model = UploadsVersion
        fields = [
            "pk",
            "name",
            "version_file",
            "creation_date",
        ]


class UploadsVersionSearchSerializer(BaseModelWithByUserSerializer):
    dataset = MinimalUploadsDatasetWithFolderSearchSerializer()

    version_file = MinimalUploadsVersionFileSearchSerializer(
        required=True,
    )

    class Meta:
        model = UploadsVersion
        fields = [
            "pk",
            "name",
            "dataset",
            "version_file",
        ]


class UploadsDatasetSearchSerializer(BaseModelWithByUserSerializer):
    folder = FolderSearchSerializer(
        required=False,
        allow_null=True,
    )

    latest_version = MinimalUploadsVersionSearchSerializer(
        source="search_latest_version",
        required=False,
        allow_null=True,
    )

    class Meta:
        model = UploadsDataset
        fields = [
            "pk",
            "name",
            "display_name",
            "folder",
            "latest_version",
            "deletable_until_date",
        ]


class UploadsVersionFileSerializer(BaseModelWithByUserSerializer):
    metadata = MinimalMetadataSerializer(
        many=True,
        read_only=True,
    )

    uploads_versions = MinimalFlatUploadsVersionSerializer(
        many=True,
        read_only=True,
    )

    class Meta:
        model = UploadsVersionFile
        fields = [
            "pk",
            "uploads_versions",
            "name",
            "status",
            "storage_relocating",
            "is_published",
            "publication_date",
            "metadata",
            "is_referenced",
        ]


class UploadsVersionFileSearchSerializer(BaseModelWithByUserSerializer):
    uploads_versions = MinimalFlatUploadsVersionSearchSerializer(
        many=True,
        read_only=True,
    )

    class Meta:
        model = UploadsVersionFile
        fields = [
            "pk",
            "uploads_versions",
            "name",
        ]


class UploadsVersionDiffValueSerializer(serializers.Serializer):
    old = MinimalMetadataSerializer(
        allow_null=True,
    )

    new = MinimalMetadataSerializer(
        allow_null=True,
    )


class MinimalUploadsVersionFileDiffSerializer(BaseModelSerializer):
    class Meta:
        model = UploadsVersionFile
        fields = [
            "pk",
            "name",
            "creation_date",
        ]


class UploadsVersionFileDiffSerializer(serializers.Serializer):
    old = MinimalUploadsVersionFileDiffSerializer(
        required=True,
        allow_null=True,
    )

    new = MinimalUploadsVersionFileDiffSerializer(
        required=True,
        allow_null=True,
    )


class MinimalUploadsVersionDiffSerializer(BaseModelSerializer):
    class Meta:
        model = UploadsVersion
        fields = [
            "pk",
            "name",
            "creation_date",
        ]


class UploadsVersionDiffSerializer(serializers.Serializer):
    old = MinimalUploadsVersionDiffSerializer(
        required=True,
        allow_null=False,
    )

    new = MinimalUploadsVersionDiffSerializer(
        required=True,
        allow_null=False,
    )


class UploadsVersionDiffResponseSerializer(serializers.Serializer):
    metadata = serializers.ListSerializer(
        child=UploadsVersionDiffValueSerializer(),
        read_only=True,
        required=False,
    )

    version_file_metadata = serializers.ListSerializer(
        child=UploadsVersionDiffValueSerializer(),
        read_only=True,
        required=False,
    )

    version_file = UploadsVersionFileDiffSerializer(
        required=False,
    )

    version = UploadsVersionDiffSerializer(
        required=True,
    )
