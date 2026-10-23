import logging
import mimetypes
import os
import re
import uuid
from pathlib import Path
from wsgiref.handlers import format_date_time

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import FileResponse, HttpResponse, HttpResponseBadRequest, HttpResponseNotFound
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from django_userforeignkey.request import get_current_user
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema, inline_serializer
from rest_framework import filters, mixins, serializers, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from fdm.core.helpers import version_diff_sort_key
from fdm.core.rest.base import BaseModelViewSet
from fdm.core.rest.mixins import *
from fdm.core.rest.permissions import (
    CanCreateDataset,
    CanCreateDatasetVersion,
    CanDeleteDataset,
    CanEditInFolder,
    can_edit_in_folder,
    can_view_in_folder,
)
from fdm.file_parser.models import FileParser, MimeTypeParser
from fdm.file_parser.registry import get_configurable_parser_types
from fdm.file_parser.rest.serializers import FileParserProcessingSerializer
from fdm.file_parser.tasks import (
    check_configured_file_parsers,
    dispatch_uploaded_file_processing,
    prepare_uploaded_file_processing,
    schedule_configured_file_parsers_for_file,
)
from fdm.folders.models import Folder, Subfolder
from fdm.metadata.helpers import get_metadata_structure_for_type, set_metadata, validate_metadata
from fdm.metadata.models import MetadataTemplateContainer
from fdm.metadata.rest.serializers import MetadataTemplateContainerSerializer, MetadataTemplateSerializer
from fdm.rest_framework_tus.views import UploadViewSet as BaseTusUploadViewSet
from fdm.uploads.helpers import (
    create_uploads_version_with_new_file_for_dataset,
    create_uploads_version_with_new_metadata_for_dataset,
    get_bulk_fill_metadata_template_fields_context,
    get_bulk_fill_skip_reason_payload,
    get_bulk_fill_uploads_datasets,
    submit_bulk_fill_metadata_template_fields,
)
from fdm.uploads.models import *
from fdm.uploads.rest.filter import UploadsDatasetMetadataIsCompleteRestFilter
from fdm.uploads.rest.serializers import *
from fdm.uploads.tasks import schedule_dataset_metadata_template_upgrades

__all__ = [
    "UploadsDatasetViewSet",
    "UploadsVersionViewSet",
    "UploadsVersionFileViewSet",
    "TusUploadViewSet",
]

logger = logging.getLogger(__name__)


class UploadsDatasetViewSet(
    BaseModelViewSet,
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    LockStatusMixin,
    LockMixin,
    UnlockMixin,
    UploadsDatasetMetadataUpgradeStatusMixin,
):
    queryset = UploadsDataset.objects.none()

    serializer_class = UploadsDatasetSerializer

    serializer_action_classes = {
        "list": UploadsDatasetListSerializer,
        "create": UploadsDatasetCreateSerializer,
        "update": UploadsDatasetUpdateSerializer,
        "partial_update": UploadsDatasetUpdateSerializer,
    }

    throttle_classes = []

    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]

    filterset_class = UploadsDatasetMetadataIsCompleteRestFilter

    filterset_fields = [
        "folder",
        "created_by",
        "last_modified_by",
    ]

    search_fields = [
        # Dataset fields
        "name",
        # Version fields
        "uploads_versions__name",
        "uploads_versions__metadata__custom_key",
        "uploads_versions__metadata__value",
        # Version file fields
        "uploads_versions__version_file__metadata__custom_key",
        "uploads_versions__version_file__metadata__value",
    ]

    ordering_fields = [
        "display_name",
        "creation_date",
        "expiry_date",
    ]

    permission_classes_by_action = {
        "create": [IsAuthenticated, CanCreateDataset],
        "list": [IsAuthenticated],
        "retrieve": [IsAuthenticated],
        "destroy": [IsAuthenticated, CanDeleteDataset],
        "bulk_delete": [IsAuthenticated],
        "version": [IsAuthenticated, CanCreateDatasetVersion],
        "restore": [IsAuthenticated, CanCreateDatasetVersion],
        "publish": [IsAuthenticated, CanEditInFolder],
    }

    def get_serializer_class(self):
        try:
            return self.serializer_action_classes[self.action]
        except (KeyError, AttributeError):
            return super().get_serializer_class()

    def get_permissions(self):
        return [permission() for permission in self.permission_classes_by_action.get(self.action, [])]

    def get_queryset(self):
        """
        Gets the queryset for the view set
        :return:
        """
        queryset = UploadsDataset.objects.all()
        folder_pk = self.request.query_params.get("folder", None)

        # Detail view of a dataset object
        if "pk" in self.kwargs:
            return queryset.my_viewable(view_type="detail")

        # Datasets in a specific folder
        if folder_pk:
            return queryset.folder_viewable(folder_pk=folder_pk)

        # Datasets in "My drafts"
        return queryset.my_viewable()

    @extend_schema(
        request=UploadsDatasetCreatePayloadSerializer,
        responses={
            201: UploadsDatasetSerializer,
        },
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)

        return Response(
            UploadsDatasetSerializer(serializer.instance).data,
            status=status.HTTP_201_CREATED,
            headers=headers,
        )

    @extend_schema(
        request=UploadsDatasetUpdatePayloadSerializer,
        responses={
            200: UploadsDatasetSerializer,
        },
    )
    def update(self, request, *args, **kwargs):
        try:
            return super().update(request, *args, **kwargs)
        except ValidationError as e:
            return Response(
                data={
                    "error": "\n".join(e.messages),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

    @extend_schema(
        request=UploadsDatasetUpdatePayloadSerializer,
        responses={
            200: UploadsDatasetSerializer,
        },
    )
    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True

        try:
            return super().update(request, *args, **kwargs)
        except ValidationError as e:
            return Response(
                data={
                    "error": "\n".join(e.messages),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    @extend_schema(
        parameters=[
            OpenApiParameter("ordering", exclude=True),
            OpenApiParameter("search", exclude=True),
        ],
        request=inline_serializer(
            name="UploadsDatasetBulkDeleteActionSerializer",
            fields={
                "uploads_datasets": serializers.ListSerializer(
                    child=serializers.UUIDField(),
                    required=True,
                ),
            },
        ),
        description=_("Deletes multiple uploads datasets in bulk."),
        responses={
            200: UploadsDatasetBulkDeleteSerializer,
        },
        methods=["POST"],
    )
    @action(
        detail=False,
        methods=["POST"],
        url_path="bulk-delete",
        url_name="bulk-delete",
        filterset_class=None,
        pagination_class=None,
    )
    def bulk_delete(self, request, *args, **kwargs):
        # Get all uploads datasets which should be deleted
        try:
            uploads_datasets = [
                UploadsDataset.objects.get(pk=uploads_dataset_pk)
                for uploads_dataset_pk in request.data.get("uploads_datasets", [])
            ]
        except UploadsDataset.DoesNotExist:
            return Response(
                data={
                    "uploads_datasets": _("At least one uploads dataset provided does not exist."),
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        if not len(uploads_datasets):
            return Response(
                data={
                    "uploads_datasets": _("You must provide at least one uploads dataset."),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        results = {
            "success": [],
            "error": [],
        }

        for dataset in uploads_datasets:
            dataset_pk = dataset.pk

            try:
                if dataset.created_by != request.user:
                    raise PermissionDenied(
                        _("You must not delete any uploads datasets you haven't created yourself."),
                    )

                if dataset.publication_date and (
                    not dataset.deletable_until_date
                    or (dataset.deletable_until_date and timezone.now() > dataset.deletable_until_date)
                ):
                    raise PermissionDenied(
                        _("You must not delete an uploads dataset outside the deletable window."),
                    )

                with transaction.atomic():
                    dataset.delete()
                results["success"].append(dataset_pk)
            except PermissionDenied as e:
                logger.info(f"Denied bulk delete for uploads dataset {dataset}: {e}")
                results["error"].append(dataset_pk)
            except Exception as e:
                logger.error(f"Could not delete uploads dataset {dataset}: {e}")
                results["error"].append(dataset_pk)

        return Response(
            data=UploadsDatasetBulkDeleteSerializer(results).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        parameters=[
            OpenApiParameter("ordering", exclude=True),
            OpenApiParameter("search", exclude=True),
        ],
        request=inline_serializer(
            name="UploadsDatasetBulkPublishActionSerializer",
            fields={
                "uploads_datasets": serializers.ListSerializer(
                    child=serializers.UUIDField(),
                    required=True,
                ),
                "folder": serializers.UUIDField(
                    required=True,
                ),
                "subfolder": serializers.UUIDField(
                    required=False,
                    allow_null=True,
                ),
            },
        ),
        description=_("Publishes multiple uploads datasets in bulk into a folder."),
        responses={
            201: UploadsDatasetBulkPublishSerializer,
            207: UploadsDatasetBulkPublishSerializer,
        },
        methods=["POST"],
    )
    @action(
        detail=False,
        methods=["POST"],
        url_path="bulk-publish",
        url_name="bulk-publish",
        filterset_class=None,
        pagination_class=None,
    )
    def bulk_publish(self, request, *args, **kwargs):
        # Get all uploads datasets which should be published
        try:
            uploads_datasets = [
                UploadsDataset.objects.get(pk=uploads_dataset_pk)
                for uploads_dataset_pk in request.data.get("uploads_datasets", [])
            ]
        except UploadsDataset.DoesNotExist:
            return Response(
                data={
                    "uploads_datasets": _("At least one uploads dataset provided does not exist."),
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        if not len(uploads_datasets):
            return Response(
                data={
                    "uploads_datasets": _("You must provide at least one uploads dataset."),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            folder = Folder.objects.get(pk=request.data.get("folder"))
        except Folder.DoesNotExist:
            return Response(
                data={
                    "folder": _("A folder with this primary key does not exist."),
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        if not can_edit_in_folder(
            user=request.user,
            folder_pk=folder.pk,
        ):
            return Response(
                data={
                    "folder": _(
                        "You must not publish any uploads datasets into a folder you haven't got the permission to.",
                    ),
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        subfolder_pk = request.data.get("subfolder", None)

        try:
            is_valid_subfolder = not subfolder_pk or Subfolder.objects.filter(pk=subfolder_pk, folder=folder).exists()
        except (ValueError, ValidationError):
            is_valid_subfolder = False

        if not is_valid_subfolder:
            return Response(
                data={
                    "subfolder": _("The subfolder must belong to the selected folder."),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        results = {
            "success": [],
            "error": [],
        }

        for dataset in uploads_datasets:
            try:
                dataset.publish(folder=folder, subfolder=subfolder_pk)
                results["success"].append(dataset.pk)
            except Exception as e:
                logger.error(
                    "Could not publish uploads dataset '%s' into folder '%s': %s",
                    dataset.pk,
                    folder.pk,
                    e,
                )

                results["error"].append(
                    {
                        "pk": dataset.pk,
                        "error": str(e),
                    },
                )

        return Response(
            data=UploadsDatasetBulkPublishSerializer(results).data,
            status=status.HTTP_201_CREATED if not results["error"] else status.HTTP_207_MULTI_STATUS,
        )

    @extend_schema(
        request=inline_serializer(
            name="UploadsDatasetPublishActionSerializer",
            fields={
                "folder": serializers.UUIDField(
                    required=False,
                    allow_null=False,
                ),
                "subfolder": serializers.UUIDField(
                    required=False,
                    allow_null=True,
                ),
            },
        ),
        responses={
            201: UploadsDatasetSerializer,
        },
    )
    @action(
        detail=True,
        methods=["POST"],
        url_path="publish",
        url_name="publish",
        throttle_classes=[],
    )
    def publish(self, request, *args, **kwargs):
        dataset = self.get_object()

        try:
            dataset.publish(request.data.get("folder", None), request.data.get("subfolder", None))
        except ValidationError as e:
            return Response(
                data={
                    "error": "\n".join(e.messages),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except PermissionDenied as e:
            return Response(
                data={
                    "error": str(e),
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        return Response(
            data=UploadsDatasetSerializer(dataset).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        request=UploadsDatasetVersionActionPayloadSerializer,
        responses={
            201: UploadsVersionSerializer,
        },
        methods=["POST"],
    )
    @action(
        detail=True,
        methods=["POST"],
        url_path="version",
        url_name="version",
        filterset_class=None,
        pagination_class=None,
    )
    def version(self, request, *args, **kwargs):
        serializer = UploadsDatasetVersionActionPayloadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        dataset = self.get_object()
        latest_version = dataset.latest_version
        metadata_list = None

        if dataset.locked and not dataset.is_locked_by_myself():
            return Response(
                data={
                    "lock": _("You must not edit an element which has been locked by another user."),
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        if not latest_version:
            return Response(
                data={
                    "latest_version": _(
                        "You must create at least one version first before you can make requests to this endpoint.",
                    ),
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # TODO: In the future it should be possible to have versions without files. We'll need to change this then.
        if not latest_version.version_file:
            return Response(
                data={
                    "version_file": _("The latest version has no file attached to it. Please upload a file first."),
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        if "metadata" in request.data:
            metadata_list = request.data.pop("metadata", [])

        # Pre-check if metadata are valid
        if metadata_list is not None:
            try:
                validate_metadata(metadata_list)
            except ValidationError as e:
                return Response(
                    data={
                        "metadata": str(e),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        uploads_version = create_uploads_version_with_new_metadata_for_dataset(
            dataset=dataset,
            name=serializer.data.get("name", None),
            metadata_list=metadata_list,
            retain_existing_metadata=False,
        )

        return Response(
            data=UploadsVersionSerializer(uploads_version).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        request=inline_serializer(
            name="UploadsDatasetFileActionSerializer",
            fields={
                "file": serializers.FileField(required=True),
            },
        ),
        responses={
            201: UploadsVersionSerializer,
        },
        methods=["POST"],
    )
    @action(
        detail=True,
        methods=["POST"],
        url_path="file",
        url_name="file",
        throttle_classes=[],
    )
    def file(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        dataset = self.get_object()
        if dataset.locked and not dataset.is_locked_by_myself():
            return Response(
                data={
                    "lock": _("You must not edit an element which has been locked by another user."),
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        uploaded_file = request.data.get("file")
        if not uploaded_file:
            return Response(
                data={
                    "file": _(
                        "You must provide a file to upload.",
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        uploads_version = create_uploads_version_with_new_file_for_dataset(
            dataset=dataset,
            name=_("File upload of {file}").format(
                file=uploaded_file.name,
            ),
            uploaded_file=uploaded_file,
            original_file_path=request.headers.get("Original-File-Path", None),
        )

        return Response(
            data=UploadsVersionSerializer(uploads_version).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        request=inline_serializer(
            name="UploadsDatasetRestoreUploadsVersionActionSerializer",
            fields={
                "uploads_version": serializers.UUIDField(required=True),
            },
        ),
        responses={
            201: UploadsVersionSerializer,
        },
        methods=["POST"],
    )
    @action(
        detail=True,
        methods=["POST"],
        url_path="restore",
        url_name="restore",
        throttle_classes=[],
    )
    def restore(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        dataset = self.get_object()

        try:
            uploads_version = dataset.restore_version(
                uploads_version=request.data.get("uploads_version", None),
            )
        except Exception as e:
            return Response(
                data={
                    "uploads_version": str(e),
                },
                status=status.HTTP_403_FORBIDDEN if isinstance(e, PermissionDenied) else status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            data=UploadsVersionSerializer(uploads_version).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        description="Create a reference to an existing file on the storage without uploading it.",
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Path to an existing file on the storage that should be referenced.",
                    },
                },
                "required": ["filepath"],
            },
        },
        responses={
            200: UploadsDatasetSerializer,
            400: OpenApiTypes.OBJECT,
        },
        examples=[
            OpenApiExample(
                "Successful Reference",
                value={
                    "filepath": "/path/to/existing/file.csv",
                },
                request_only=True,
            ),
            OpenApiExample(
                "Error Response",
                value={
                    "filepath": "No filepath provided.",
                },
                response_only=True,
                status_codes=["400"],
            ),
        ],
    )
    @action(
        detail=True,
        methods=["POST"],
        url_path="reference",
        url_name="reference",
        throttle_classes=[],
    )
    def reference(self, request, *args, **kwargs) -> Response:
        """
        Create a reference file for an existing file on the storage.
        This endpoint allows adding files to a dataset by referencing an existing file path.
        """
        dataset = self.get_object()

        # Check if filepath was provided
        filepath = request.data.get("filepath")
        if not filepath:
            return Response(
                {"filepath": _("No filepath provided.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        storage = dataset.folder.storage

        full_filepath = os.path.join(
            settings.PRIVATE_DSS_MOUNT_PATH,
            storage.local_private_dss_path,
            filepath.lstrip("/"),
        )
        logger.info(f"full_filepath: {full_filepath}")

        if not os.path.exists(full_filepath):
            return Response(
                {"filepath": _("File not found at path: {full_filepath}").format(full_filepath=full_filepath)},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            # Create a new UploadsVersionFile that references the existing file
            with open(full_filepath, "rb"):
                # Directly create and save the UploadsVersionFile instance
                file = UploadsVersionFile.objects.create(
                    uploaded_file=None,
                    status=UploadsVersionFile.Status.SCHEDULED,
                    storage_relocating=UploadsVersionFile.Status.FINISHED,
                    is_referenced=True,
                )

            file.uploaded_file.name = str(full_filepath)
            file.publication_date = timezone.now()
            file.save()

            # Set metadata for the original file path
            set_metadata(
                assigned_to_content_type=file.get_content_type(),
                assigned_to_object_id=file.pk,
                custom_key="ORIGINAL_FILE_PATH",
                value=filepath,
                read_only=True,
            )

            file_name = Path(filepath).name
            set_metadata(
                assigned_to_content_type=file.get_content_type(),
                assigned_to_object_id=file.pk,
                custom_key="ORIGINAL_FILE_NAME",
                value=file_name,
                read_only=True,
            )

            # Create the UploadsVersion with the file reference
            uploads_version = UploadsVersion.objects.create(
                version_file=file,
                dataset=dataset,
            )

            # If the dataset is already published, publish this version too
            if dataset.is_published() and not uploads_version.is_published():
                uploads_version.publish()

            prepare_uploaded_file_processing(uploads_version.version_file)
            transaction.on_commit(lambda: dispatch_uploaded_file_processing(str(uploads_version.version_file.pk)))

            # Return the serialized dataset
            serializer = self.get_serializer(uploads_version.dataset)
            return Response(serializer.data)

        except Exception as e:
            return Response(
                {"filepath": _("Failed to create reference file: {e}").format(e=str(e))},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @extend_schema(
        responses={
            200: MetadataTemplateContainerSerializer(many=True),
        },
        methods=["GET"],
    )
    @action(
        detail=True,
        methods=["GET"],
        url_path="metadata-template-container",
        url_name="metadata-template-container",
        filterset_class=None,
        pagination_class=None,
    )
    def metadata_template_container(self, request, *args, **kwargs):
        uploads_dataset = self.get_object()
        metadata_template_container = uploads_dataset.get_available_metadata_template_container()

        return Response(
            data=MetadataTemplateContainerSerializer(metadata_template_container, many=True).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        responses={
            200: MetadataTemplateSerializer(many=True),
        },
        methods=["GET"],
    )
    @action(
        detail=True,
        methods=["GET"],
        url_path="metadata-templates",
        url_name="metadata-templates",
        filterset_class=None,
        pagination_class=None,
    )
    def metadata_templates(self, request, *args, **kwargs):
        uploads_dataset = self.get_object()
        metadata_templates = uploads_dataset.get_available_metadata_templates()

        return Response(
            data=MetadataTemplateSerializer(metadata_templates, many=True).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        parameters=[
            OpenApiParameter("ordering", exclude=True),
            OpenApiParameter("search", exclude=True),
        ],
        responses={
            200: MetadataTemplateContainerSerializer(many=True),
        },
        methods=["GET"],
    )
    @action(
        detail=True,
        methods=["GET"],
        url_path="metadata-template-container-pool",
        url_name="metadata-template-container-pool",
        filterset_class=None,
        pagination_class=None,
    )
    def metadata_template_container_pool(self, request, *args, **kwargs):
        instance = self.get_object()

        if not instance.folder:
            return Response(
                data=[],
                status=status.HTTP_200_OK,
            )

        metadata_template_container = instance.folder.metadata_template_container.all()

        # Add the metadata template container of the dataset to the pool
        if (
            instance.metadata_template
            and not metadata_template_container.filter(
                pk=instance.metadata_template.metadata_template_container.pk,
            ).exists()
        ):
            metadata_template_container |= MetadataTemplateContainer.objects.filter(
                pk=instance.metadata_template.metadata_template_container.pk,
            )

        # Add the metadata template container of the folder to the pool
        if (
            instance.folder.metadata_template
            and not metadata_template_container.filter(
                pk=instance.folder.metadata_template.metadata_template_container.pk,
            ).exists()
        ):
            metadata_template_container |= MetadataTemplateContainer.objects.filter(
                pk=instance.folder.metadata_template.metadata_template_container.pk,
            )

        # Add the metadata template container of the project to the pool
        if (
            instance.folder.project.metadata_template
            and not metadata_template_container.filter(
                pk=instance.folder.project.metadata_template.metadata_template_container.pk,
            ).exists()
        ):
            metadata_template_container |= MetadataTemplateContainer.objects.filter(
                pk=instance.folder.project.metadata_template.metadata_template_container.pk,
            )

        return Response(
            data=MetadataTemplateContainerSerializer(
                metadata_template_container.order_by("name"),
                many=True,
            ).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        request=inline_serializer(
            name="UploadsDatasetUpgradeMetadataActionSerializer",
            fields={},
        ),
        responses={
            201: UploadsDatasetSerializer,
        },
    )
    @action(
        detail=True,
        methods=["POST"],
        url_path="upgrade-metadata",
        url_name="upgrade-metadata",
        throttle_classes=[],
    )
    def upgrade_metadata(self, request, *args, **kwargs):
        dataset = self.get_object()

        if dataset.locked and not dataset.is_locked_by_myself():
            return Response(
                data={
                    "lock": _("You must not edit an element which has been locked by another user."),
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        dataset.metadata_upgrade_status = UploadsDataset.Status.IN_PROGRESS
        dataset.save()

        success, error = dataset.upgrade_latest_version_metadata()

        if not success:
            dataset.metadata_upgrade_status = UploadsDataset.Status.ERROR
            dataset.save()
            return Response(
                data={
                    "error": error,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        dataset.metadata_upgrade_status = UploadsDataset.Status.FINISHED
        dataset.has_upgradeable_metadata_templates = dataset.is_latest_version_upgradeable()
        dataset.save()

        return Response(
            data=UploadsDatasetSerializer(dataset).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        request=UploadsDatasetResolveUpgradeMetadataConflictPayloadSerializer,
        responses={
            201: UploadsDatasetSerializer,
        },
    )
    @action(
        detail=True,
        methods=["POST"],
        url_path="resolve-upgrade-metadata-conflict",
        url_name="resolve-upgrade-metadata-conflict",
        throttle_classes=[],
    )
    def resolve_upgrade_metadata_conflict(self, request, *args, **kwargs):
        dataset = self.get_object()

        serializer = UploadsDatasetResolveUpgradeMetadataConflictPayloadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        request_payload = serializer.validated_data

        if dataset.locked and not dataset.is_locked_by_myself():
            return Response(
                data={
                    "lock": _("You must not edit an element which has been locked by another user."),
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        latest_version = dataset.latest_version
        if not latest_version:
            return Response(
                data={
                    "error": _("You can't upgrade a dataset which is not upgradeable."),
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        base_version_pk = str(request_payload.get("base_version_pk"))
        latest_version_pk = str(latest_version.pk)
        if base_version_pk != latest_version_pk:
            return Response(
                data={
                    "error": _("The upgrade base version is outdated. Please refresh the upgrade payload."),
                },
                status=status.HTTP_409_CONFLICT,
            )

        if not dataset.is_latest_version_upgradeable():
            return Response(
                data={
                    "error": _("You can't upgrade a dataset which is not upgradeable."),
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            (
                upgraded_metadata_list,
                upgraded_metadata_templates_list,
                conflicts,
                _template_versions,
                _computed_base_pk,
            ) = dataset.get_upgraded_latest_version_metadata_with_conflicts(
                max_upgrade=False,
            )

            conflict_map = {}
            for conflict in conflicts:
                conflict_map[conflict["conflict_id"]] = conflict

            resolution_map = {}
            duplicate_conflict_ids = []
            for resolution in request_payload.get("resolutions", []):
                conflict_id = resolution.get("conflict_id")
                if conflict_id in resolution_map:
                    duplicate_conflict_ids.append(conflict_id)
                    continue

                resolution_map[conflict_id] = resolution.get("value")

            if duplicate_conflict_ids:
                return Response(
                    data={
                        "error": _("You provided duplicate conflict ids in the resolutions payload."),
                        "duplicate_conflicts": sorted(set(duplicate_conflict_ids)),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            missing_conflict_ids = sorted(
                [conflict_id for conflict_id in conflict_map if conflict_id not in resolution_map],
            )
            if missing_conflict_ids:
                return Response(
                    data={
                        "error": _("You must provide a resolution for each conflict."),
                        "missing_conflicts": missing_conflict_ids,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            unknown_conflict_ids = sorted(
                [conflict_id for conflict_id in resolution_map if conflict_id not in conflict_map],
            )
            if unknown_conflict_ids:
                return Response(
                    data={
                        "error": _("You provided unknown conflict ids."),
                        "unknown_conflicts": unknown_conflict_ids,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            for conflict_id, resolution_value in resolution_map.items():
                conflicting_metadata = conflict_map[conflict_id]["metadata"]
                conflicting_metadata.value = get_metadata_structure_for_type(
                    conflicting_metadata.field_type,
                    resolution_value,
                )

            validate_metadata(upgraded_metadata_list)

            create_uploads_version_with_new_metadata_for_dataset(
                dataset=dataset,
                name=str(_("Manually upgraded metadata by resolving conflicts")),
                metadata_list=upgraded_metadata_list,
                link_metadata_templates=upgraded_metadata_templates_list,
                retain_existing_metadata=False,
            )
        except ValidationError as e:
            return Response(
                data={
                    "error": ", ".join(e.messages),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        dataset.metadata_upgrade_status = UploadsDataset.Status.FINISHED
        dataset.has_upgradeable_metadata_templates = dataset.is_latest_version_upgradeable()
        dataset.save()

        return Response(
            data=UploadsDatasetSerializer(dataset).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        request=inline_serializer(
            name="UploadsDatasetUpgradeMetadataPayloadActionSerializer",
            fields={},
        ),
        responses={
            200: UploadsDatasetUpgradeMetadataPayloadSerializer,
        },
    )
    @action(
        detail=True,
        methods=["GET"],
        url_path="upgrade-metadata-payload",
        url_name="upgrade-metadata-payload",
        throttle_classes=[],
    )
    def upgrade_metadata_payload(self, request, *args, **kwargs):
        dataset = self.get_object()
        try:
            (
                _upgraded_metadata_list,
                _upgraded_metadata_templates_list,
                conflicts,
                template_versions,
                base_version_pk,
            ) = dataset.get_upgraded_latest_version_metadata_with_conflicts(
                max_upgrade=False,
            )
        except ValidationError as e:
            return Response(
                data={
                    "error": ", ".join(e.messages),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        conflict_payload = []
        for conflict in conflicts:
            conflict_payload.append(
                {
                    "conflict_id": conflict["conflict_id"],
                    "error": conflict["error"],
                    "old": conflict["old"],
                    "new": conflict["new"],
                },
            )

        return Response(
            data=UploadsDatasetUpgradeMetadataPayloadSerializer(
                {
                    "base_version_pk": base_version_pk,
                    "template_versions": template_versions,
                    "conflicts": conflict_payload,
                },
            ).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        request=inline_serializer(
            name="UploadsDatasetBulkUpgradeMetadataActionSerializer",
            fields={
                "uploads_datasets": serializers.ListSerializer(
                    child=serializers.UUIDField(),
                    required=True,
                ),
            },
        ),
        description=_("Triggers multiple metadata upgrade processes for uploads datasets in bulk."),
        responses={
            201: None,
        },
    )
    @action(
        detail=False,
        methods=["POST"],
        url_path="bulk-upgrade-metadata",
        url_name="bulk-upgrade-metadata",
        throttle_classes=[],
    )
    def bulk_upgrade_metadata(self, request, *args, **kwargs):
        # Get all uploads datasets the upgrade should be triggered on
        try:
            uploads_datasets = [
                UploadsDataset.objects.get(pk=uploads_dataset_pk)
                for uploads_dataset_pk in request.data.get("uploads_datasets", [])
            ]
        except UploadsDataset.DoesNotExist:
            return Response(
                data={
                    "uploads_datasets": _("At least one uploads dataset provided does not exist."),
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        if not len(uploads_datasets):
            return Response(
                data={
                    "uploads_datasets": _("You must provide at least one uploads dataset."),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if any(
            [
                (
                    dataset.is_published()
                    and not can_edit_in_folder(
                        user=request.user,
                        folder_pk=dataset.folder.pk if dataset.folder else None,
                    )
                )
                or (not dataset.is_published() and dataset.created_by != request.user)
                for dataset in uploads_datasets
            ],
        ):
            return Response(
                data={
                    "uploads_datasets": _(
                        "You must not edit any published uploads datasets in a folder you haven't got the permission to.",
                    ),
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        upgradeable_datasets = []

        for dataset in uploads_datasets:
            if dataset.is_latest_version_upgradeable():
                upgradeable_datasets.append(dataset.pk)

        try:
            with transaction.atomic():
                schedule_dataset_metadata_template_upgrades(upgradeable_datasets)
        except Exception as e:
            return Response(
                {
                    "uploads_datasets": f"Could not trigger a bulk metadata upgrade: {e}",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        request=UploadsDatasetBulkFillMetadataTemplateFieldsPayloadSerializer,
        responses={
            200: UploadsDatasetBulkFillMetadataTemplateFieldsResponseSerializer,
        },
        description=_("Returns inherited metadata template fields for bulk filling across uploads datasets."),
    )
    @action(
        detail=False,
        methods=["POST"],
        url_path="bulk-fill-metadata-template-fields",
        url_name="bulk-fill-metadata-template-fields",
        filterset_class=None,
        pagination_class=None,
    )
    def bulk_fill_metadata_template_fields(self, request, *args, **kwargs):
        serializer = UploadsDatasetBulkFillMetadataTemplateFieldsPayloadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        uploads_dataset_pks = serializer.validated_data.get("uploads_datasets", [])
        if not uploads_dataset_pks:
            return Response(
                data={
                    "uploads_datasets": _("You must provide at least one uploads dataset."),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            uploads_datasets = get_bulk_fill_uploads_datasets(
                uploads_dataset_pks=uploads_dataset_pks,
                user=request.user,
            )
        except UploadsDataset.DoesNotExist:
            return Response(
                data={
                    "targets": _("At least one uploads dataset provided does not exist."),
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        except PermissionDenied as e:
            return Response(
                data={
                    "targets": str(e),
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        context = get_bulk_fill_metadata_template_fields_context(uploads_datasets)
        response_payload = {
            "eligible_uploads_datasets": context["eligible_uploads_datasets_payload"],
            "skipped_uploads_datasets_count": sum(context["skip_reason_counts"].values()),
            "skip_reasons": get_bulk_fill_skip_reason_payload(context["skip_reason_counts"]),
            "warnings": [
                {
                    "code": warning,
                }
                for warning in context["warnings"]
            ],
            "has_skipped_due_to_pending_template_upgrade": bool(
                context["skip_reason_counts"]["PENDING_TEMPLATE_UPGRADE"],
            ),
            "fields": context["fields_payload"],
        }

        return Response(
            data=UploadsDatasetBulkFillMetadataTemplateFieldsResponseSerializer(response_payload).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        request=UploadsDatasetBulkFillMetadataTemplateFieldsSubmitPayloadSerializer,
        responses={
            200: UploadsDatasetBulkFillMetadataTemplateFieldsSubmitResponseSerializer,
        },
        description=_("Applies inherited metadata template field values in bulk to uploads datasets."),
    )
    @action(
        detail=False,
        methods=["POST"],
        url_path="bulk-fill-metadata-template-fields-submit",
        url_name="bulk-fill-metadata-template-fields-submit",
        filterset_class=None,
        pagination_class=None,
    )
    def bulk_fill_metadata_template_fields_submit(self, request, *args, **kwargs):
        serializer = UploadsDatasetBulkFillMetadataTemplateFieldsSubmitPayloadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        request_payload = serializer.validated_data
        targets = request_payload.get("targets", [])
        uploads_dataset_pks = [target["uploads_dataset_pk"] for target in targets]
        if not targets:
            return Response(
                data={
                    "targets": _("You must provide at least one target."),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            uploads_datasets = get_bulk_fill_uploads_datasets(
                uploads_dataset_pks=uploads_dataset_pks,
                user=request.user,
            )
        except UploadsDataset.DoesNotExist:
            return Response(
                data={
                    "uploads_datasets": _("At least one uploads dataset provided does not exist."),
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        except PermissionDenied as e:
            return Response(
                data={
                    "uploads_datasets": str(e),
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        duplicate_base_version_pks = []
        base_version_map = {}
        for base_version in targets:
            uploads_dataset_pk = str(base_version["uploads_dataset_pk"])
            if uploads_dataset_pk in base_version_map:
                duplicate_base_version_pks.append(uploads_dataset_pk)
                continue

            base_version_map[uploads_dataset_pk] = str(base_version["base_version_pk"])

        if duplicate_base_version_pks:
            return Response(
                data={
                    "error": _("You provided duplicate uploads dataset pks in the targets payload."),
                    "duplicate_uploads_datasets": sorted(set(duplicate_base_version_pks)),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        duplicate_field_pks = []
        deduped_fields = []
        seen_field_pks = set()
        for field in request_payload.get("fields", []):
            metadata_template_field_pk = str(field["metadata_template_field_pk"])
            if metadata_template_field_pk in seen_field_pks:
                duplicate_field_pks.append(metadata_template_field_pk)
                continue

            seen_field_pks.add(metadata_template_field_pk)
            deduped_fields.append(field)

        if duplicate_field_pks:
            return Response(
                data={
                    "error": _("You provided duplicate metadata template fields in the fields payload."),
                    "duplicate_metadata_template_fields": sorted(set(duplicate_field_pks)),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            response_payload = submit_bulk_fill_metadata_template_fields(
                uploads_datasets=uploads_datasets,
                base_version_map=base_version_map,
                field_payloads=deduped_fields,
            )
        except ValidationError as e:
            return Response(
                data={
                    "error": "\n".join(e.messages),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            data=UploadsDatasetBulkFillMetadataTemplateFieldsSubmitResponseSerializer(response_payload).data,
            status=status.HTTP_200_OK,
        )


def file_iterator(file_path, chunk_size=8192):
    """
    A generator that reads the file in chunks for streaming.
    """
    with open(file_path, "rb") as file:
        while True:
            chunk = file.read(chunk_size)
            if not chunk:
                break
            yield chunk


class UploadsVersionViewSet(
    BaseModelViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    LockStatusMixin,
):
    queryset = UploadsVersion.objects.none()

    serializer_class = UploadsVersionSerializer

    throttle_classes = []

    filterset_class = []

    filterset_fields = [
        "dataset",
        "metadata_is_complete",
        "version_file",
        "version_file__status",
        "created_by",
        "last_modified_by",
        "dataset__folder",
    ]

    search_fields = [
        "name",
        "version_file__uploaded_file",
    ]

    permission_classes_by_action = {
        "list": [IsAuthenticated],
        "retrieve": [IsAuthenticated],
        "update": [IsAuthenticated],
        "partial_update": [IsAuthenticated],
    }

    def get_permissions(self):
        return [permission() for permission in self.permission_classes_by_action.get(self.action, [])]

    def get_queryset(self):
        """
        Gets the queryset for the view set, with filtering by dataset that respects permissions
        :return: A filtered queryset
        """
        queryset = UploadsVersion.objects.all()

        # Detail view of an uploads version object
        if "pk" in self.kwargs:
            return queryset.my_viewable(view_type="detail")

        # Filter by dataset if provided
        dataset_pk = self.request.query_params.get("dataset", None)
        if dataset_pk:
            # Get the dataset
            try:
                dataset = UploadsDataset.objects.get(id=dataset_pk)
                # Check if user has permission to view this dataset
                user = get_current_user()
                if user.is_authenticated:
                    # Check folder permissions if dataset has a folder
                    if dataset.folder:
                        if can_view_in_folder(user, dataset.folder.pk):
                            return queryset.filter(dataset=dataset_pk).order_by("-creation_date")
                    # If no folder, user must be the creator
                    elif dataset.created_by == user:
                        return queryset.filter(dataset=dataset_pk).order_by("-creation_date")
            except UploadsDataset.DoesNotExist:
                pass

            # Return empty queryset if no permissions or dataset not found
            return queryset.none()

        # Filter by folder if provided
        folder_pk = self.request.query_params.get("dataset__folder", None)
        if folder_pk:
            return queryset.folder_viewable(folder_pk=folder_pk)

        # Return empty queryset if no filters provided
        return queryset.none()

    @extend_schema(
        request=UploadsVersionPayloadSerializer,
        responses={
            200: UploadsVersionSerializer,
        },
    )
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @extend_schema(
        request=UploadsVersionPayloadSerializer,
        responses={
            200: UploadsVersionSerializer,
        },
    )
    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return super().update(request, *args, **kwargs)

    @action(
        detail=True,
        methods=[
            "GET",
            "HEAD",
        ],
        url_path="download",
        url_name="download",
        throttle_classes=[],
    )
    def download(self, request, pk=None):
        """
        Serves a file as a response to GET requests and provides file metadata for HEAD requests.
        """
        instance = self.get_object()
        metadata = instance.version_file.metadata

        file_name = instance.version_file.name
        file_path = os.path.join(instance.version_file.absolute_path, file_name)

        checksum_sha256 = metadata.filter(custom_key="CHECKSUM_SHA256").first()
        original_filename = metadata.filter(custom_key="ORIGINAL_FILE_NAME").first()

        mime_type = metadata.filter(custom_key="MIME_TYPE").first()
        if not mime_type:
            mime_type = MimeTypeParser(instance.version_file).parse()
        else:
            mime_type = mime_type.get_value()

        try:
            # Handle HEAD requests for file metadata
            if request.method == "HEAD":
                # Creates a response with file metadata for HEAD requests, mimicking S3 HEAD responses.
                if not os.path.exists(file_path):
                    return HttpResponseNotFound(_("The requested file does not exist."))

                file_size = os.path.getsize(file_path)
                last_modified_time = os.path.getmtime(file_path)
                last_modified_gmt = format_date_time(last_modified_time)

                response = HttpResponse(status=status.HTTP_200_OK)
                response["File-Size"] = str(file_size)
                response["File-Last-Modified"] = last_modified_gmt
                response["File-Accept-Ranges"] = "bytes"  # Indicates support for range requests. Not yet implemented!
                response["File-Mime-Type"] = mime_type
                response["File-Checksum-SHA256"] = checksum_sha256.get_value() if checksum_sha256 else ""
                response["ETag"] = checksum_sha256.get_value() if checksum_sha256 else ""

                return response

            # Handle GET requests for file download
            if os.path.exists(file_path):
                extension = mimetypes.guess_extension(mime_type)
                suggestion = instance.dataset.name or original_filename.get_value() or file_name

                # This regex operation ensures that:
                # 1. all file extensions get removed, e.g. picture.jpg.jpg
                # 2. all file extensions are found regardless of case, e.g. picture.jpg.Jpg.JPG
                if extension is not None:
                    while re.findall(f"{extension}$", suggestion, flags=re.IGNORECASE):
                        suggestion = re.sub(f"{extension}$", "", suggestion, flags=re.IGNORECASE)

                return FileResponse(
                    open(file_path, "rb"),
                    as_attachment=bool(request.query_params.get("as_attachment")),
                    filename=f"{suggestion}{extension or ''}",
                )
            else:
                return HttpResponseNotFound(_("The requested file does not exist."))
        except Exception as e:
            logger.error(e)
            return HttpResponseBadRequest()

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="compare",
                type=uuid.UUID,
                location=OpenApiParameter.QUERY,
                description="UUID of the uploads version to compare with",
                required=True,
            ),
        ],
        responses={
            200: UploadsVersionDiffResponseSerializer,
        },
    )
    @action(
        detail=True,
        methods=[
            "GET",
        ],
        url_path="diff",
        url_name="diff",
        throttle_classes=[],
    )
    def diff(self, request, *args, **kwargs):
        instance = self.get_object()
        compare = request.query_params.get("compare")

        if not compare:
            return Response(
                data={
                    "compare": _("You must provide an uploads version to compare to."),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            compare_uuid = uuid.UUID(compare)
        except ValueError:
            return Response(
                data={
                    "compare": _("Invalid UUID format for compare parameter."),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            compare_instance = UploadsVersion.objects.get(pk=compare_uuid)
        except UploadsVersion.DoesNotExist:
            return Response(
                data={
                    "compare": _("An uploads version with this primary key does not exist."),
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        if instance.pk == compare_instance.pk:
            return Response(
                data={
                    "compare": _("You must compare two different uploads versions."),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        old_instance, new_instance = sorted((instance, compare_instance), key=lambda x: x.creation_date)

        diff = self.compare_instances(new_instance, old_instance)

        diff["version"] = {
            "old": old_instance,
            "new": new_instance,
        }

        return Response(
            data=UploadsVersionDiffResponseSerializer(
                diff,
                context={
                    "normalize_order": False,
                },
            ).data,
            status=status.HTTP_200_OK,
        )

    def compare_instances(self, new_instance, old_instance):
        diff = {}

        metadata_diff = self.compare_metadata(
            old_instance.metadata.all(),
            new_instance.metadata.all(),
        )

        if metadata_diff:
            diff["metadata"] = metadata_diff

        file_diff = self.compare_files(
            old_instance.version_file,
            new_instance.version_file,
        )

        if file_diff:
            diff["version_file"] = file_diff

            version_file_metadata = self.compare_file_metadata(
                old_instance.version_file.metadata.all(),
                new_instance.version_file.metadata.all(),
            )

            if version_file_metadata:
                diff["version_file_metadata"] = version_file_metadata

        return diff

    def compare_metadata(self, old_metadata, new_metadata):
        diff = []

        # 1. All metadata in the new uploads version that is not linked to a previous field is new metadata
        for metadata in new_metadata.filter(previous_version_field__isnull=True):
            diff.append(
                {
                    "old": None,
                    "new": metadata,
                },
            )

        # 2. All metadata in the new uploads version that is linked to a previous field is changed metadata
        old_metadata_to_ignore = []
        for metadata in new_metadata.filter(previous_version_field__isnull=False):
            matched_version_of_past_metadata = False

            # First, we need to find the related field in the old uploads version metadata
            for old_metadata_instance in old_metadata:
                # If we found our match, we must compare the attributes to see if it really is a change
                if metadata.is_version_of_past_field(old_metadata_instance):
                    matched_version_of_past_metadata = True
                    old_metadata_to_ignore.append(old_metadata_instance.pk)

                    if (
                        old_metadata_instance.field != metadata.field
                        or old_metadata_instance.custom_key != metadata.custom_key
                        or old_metadata_instance.field_type != metadata.field_type
                        or old_metadata_instance.get_value() != metadata.get_value()
                        or old_metadata_instance.config != metadata.config
                        or old_metadata_instance.order != metadata.order
                    ):
                        diff.append(
                            {
                                "old": old_metadata_instance,
                                "new": metadata,
                            },
                        )

                    break

            # If we found no match, we consider it a new field despite having a previous version
            if not matched_version_of_past_metadata:
                diff.append(
                    {
                        "old": None,
                        "new": metadata,
                    },
                )

        # 3. We have to remove all metadata from the old uploads version that is previous versions of new metadata.
        #    The rest of the metadata is metadata that was removed.
        for metadata in old_metadata.exclude(pk__in=old_metadata_to_ignore):
            diff.append(
                {
                    "old": metadata,
                    "new": None,
                },
            )

        return sorted(diff, key=version_diff_sort_key) if diff else None

    def group_by_key(self, items, attribute):
        groups = {}

        for item in items:
            key = getattr(item, attribute, None)
            if key not in groups:
                groups[key] = []
            groups[key].append(item)

        return groups

    def compare_file_metadata(self, old_metadata, new_metadata):
        diff = []

        old_groups = self.group_by_key(old_metadata, "custom_key")
        new_groups = self.group_by_key(new_metadata, "custom_key")
        all_keys = set(old_groups.keys()) | set(new_groups.keys())

        for key in all_keys:
            old_instances = old_groups.get(key, [])
            new_instances = new_groups.get(key, [])

            matched_old_instances = set()
            matched_new_instances = set()

            # Check for exact matches which have not been changed
            for old, old_metadata_instance in enumerate(old_instances):
                for new, new_metadata_instance in enumerate(new_instances):
                    if new in matched_new_instances:
                        continue

                    if (
                        old_metadata_instance.field == new_metadata_instance.field
                        and old_metadata_instance.custom_key == new_metadata_instance.custom_key
                        and old_metadata_instance.field_type == new_metadata_instance.field_type
                        and old_metadata_instance.get_value() == new_metadata_instance.get_value()
                    ):
                        matched_old_instances.add(old)
                        matched_new_instances.add(new)
                        break

            # Check for changes
            for old, old_metadata_instance in enumerate(old_instances):
                if old in matched_old_instances:
                    continue

                for new, new_metadata_instance in enumerate(new_instances):
                    if new in matched_new_instances:
                        continue

                    diff.append(
                        {
                            "old": old_metadata_instance,
                            "new": new_metadata_instance,
                        },
                    )

                    matched_old_instances.add(old)
                    matched_new_instances.add(new)
                    break

            # Check for deleted items
            for old, old_metadata_instance in enumerate(old_instances):
                if old not in matched_old_instances:
                    diff.append(
                        {
                            "old": old_metadata_instance,
                            "new": None,
                        },
                    )

            # Check for added items
            for new, new_metadata_instance in enumerate(new_instances):
                if new not in matched_new_instances:
                    diff.append(
                        {
                            "old": None,
                            "new": new_metadata_instance,
                        },
                    )

        return sorted(diff, key=version_diff_sort_key) if diff else None

    def compare_files(self, old_file, new_file):
        if old_file.pk == new_file.pk:
            return None

        return {
            "old": old_file,
            "new": new_file,
        }


class UploadsVersionFileViewSet(BaseModelViewSet, mixins.RetrieveModelMixin):
    queryset = UploadsVersionFile.objects.none()

    serializer_class = UploadsVersionFileSerializer

    throttle_classes = []

    filterset_class = []

    filterset_fields = [
        "status",
        "created_by",
        "last_modified_by",
    ]

    search_fields = [
        "uploaded_file",
    ]

    def get_queryset(self):
        """
        Gets the queryset for the view set
        :return:
        """
        return UploadsVersionFile.objects.all()

    def _get_dataset_for_file(self, file):
        uploads_version = (
            file.uploads_versions.select_related(
                "dataset",
                "dataset__folder",
            )
            .order_by("creation_date")
            .first()
        )

        return uploads_version.dataset if uploads_version else None

    def _can_view_file(self, user, file) -> bool:
        dataset = self._get_dataset_for_file(file)
        if not dataset:
            return file.created_by == user

        if dataset.folder:
            return can_view_in_folder(user, dataset.folder.pk)

        return dataset.created_by == user

    def _can_edit_file(self, user, file) -> bool:
        dataset = self._get_dataset_for_file(file)
        if not dataset:
            return file.created_by == user

        if dataset.folder:
            return can_edit_in_folder(user, dataset.folder.pk)

        return dataset.created_by == user

    @action(
        detail=True,
        methods=["get"],
        url_path="parser-processing",
    )
    def parser_processing(self, request, *args, **kwargs):
        file = self.get_object()
        if not self._can_view_file(request.user, file):
            return Response(status=status.HTTP_403_FORBIDDEN)

        queryset = FileParser.objects.filter(
            file=file,
            parser_type__in=get_configurable_parser_types(),
        ).order_by("creation_date")
        serializer = FileParserProcessingSerializer(queryset, many=True)

        return Response(serializer.data)

    @action(
        detail=True,
        methods=["post"],
        url_path="retrigger-parser-processing",
    )
    def retrigger_parser_processing(self, request, *args, **kwargs):
        file = self.get_object()
        if not self._can_edit_file(request.user, file):
            return Response(status=status.HTTP_403_FORBIDDEN)

        actions = schedule_configured_file_parsers_for_file(file=file, retry_errors=True)
        if any(action["action"] in ["scheduled", "retried"] for action in actions):
            file.status = UploadsVersionFile.Status.SCHEDULED
            file.save(update_fields=["status"])
            transaction.on_commit(lambda: dispatch_uploaded_file_processing(str(file.pk)))

        return Response(actions)

    @action(
        detail=True,
        methods=["post"],
        url_path="retry-processing",
    )
    def retry_processing(self, request, *args, **kwargs):
        file = self.get_object()
        if not self._can_edit_file(request.user, file):
            return Response(status=status.HTTP_403_FORBIDDEN)

        with transaction.atomic():
            file = UploadsVersionFile.objects.select_for_update().get(pk=file.pk)
            if file.status in [UploadsVersionFile.Status.SCHEDULED, UploadsVersionFile.Status.IN_PROGRESS]:
                return Response(self.get_serializer(file).data, status=status.HTTP_202_ACCEPTED)
            if file.status != UploadsVersionFile.Status.ERROR:
                return Response(
                    {"status": _("Only failed uploaded file processing can be retried.")},
                    status=status.HTTP_409_CONFLICT,
                )

            file.file_parser.filter(
                status__in=[FileParser.Status.ERROR, FileParser.Status.IN_PROGRESS],
            ).update(status=FileParser.Status.SCHEDULED)
            file.status = UploadsVersionFile.Status.SCHEDULED
            file.processing_lease_token = None
            file.processing_lease_expires_at = None
            file.processing_heartbeat_at = None
            file.save(
                update_fields=[
                    "status",
                    "processing_lease_token",
                    "processing_lease_expires_at",
                    "processing_heartbeat_at",
                ],
            )
            transaction.on_commit(lambda: dispatch_uploaded_file_processing(str(file.pk)))

        return Response(self.get_serializer(file).data, status=status.HTTP_202_ACCEPTED)


class TusUploadViewSet(BaseModelViewSet, BaseTusUploadViewSet):
    throttle_classes = []

    filterset_class = []

    filterset_fields = []

    search_fields = []

    pagination_class = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dataset = None

    def create(self, request, *args, **kwargs):
        try:
            self.dataset = UploadsDataset.objects.get(pk=kwargs.get("dataset_pk", None))
        except UploadsDataset.DoesNotExist:
            return HttpResponse(
                _("A dataset with this primary key does not exist."),
                status=status.HTTP_404_NOT_FOUND,
            )

        return super().create(request, *args, **kwargs)

    def get_success_headers(self, data):
        try:
            url = reverse(
                "uploads-dataset-detail",
                kwargs={
                    "pk": self.dataset.pk,
                },
            )

            return {
                "Location": f"{url}tus/{data['guid']}/",
            }
        except (TypeError, KeyError):
            return {}
