import logging
import uuid
from collections import defaultdict

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q, Subquery
from django.utils.translation import gettext_lazy as _
from django_userforeignkey.request import get_current_user
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import filters, mixins, serializers, status
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from waffle.mixins import WaffleSwitchMixin

from fdm.core.helpers import get_content_type_for_model, get_content_type_instance, version_diff_sort_key
from fdm.core.rest.base import BaseModelViewSet
from fdm.core.rest.mixins import LockMixin, LockStatusMixin, UnlockMixin
from fdm.core.rest.permissions import (
    CanEditMetadataTemplate,
    CanEditMetadataTemplateContainer,
    can_edit_in_folder,
    is_folder_metadata_template_admin,
    is_project_metadata_template_admin,
)
from fdm.core.rest.serializers import (
    METADATA_CONTENT_TYPES,
    METADATA_TEMPLATE_CONTENT_TYPES,
    MetadataTemplateContentTypePayloadSerializer,
)
from fdm.metadata.helpers import check_metadata_template_permissions_for_object, validate_metadata
from fdm.metadata.models import *
from fdm.metadata.rest.filter import ContentTypeFilter
from fdm.metadata.rest.serializers import *
from fdm.metadata.services import add_scoped_container_to_pool, import_metadata_template_container_from_json_file
from fdm.uploads.helpers import create_uploads_version_with_new_metadata_for_dataset
from fdm.uploads.models import UploadsDataset

__all__ = [
    "MetadataFieldViewSet",
    "MetadataViewSet",
    "MetadataTagViewSet",
    "MetadataTemplateContainerViewSet",
    "MetadataTemplateViewSet",
    "MetadataTemplateFieldViewSet",
]

logger = logging.getLogger(__name__)


class MetadataFieldViewSet(WaffleSwitchMixin, BaseModelViewSet, mixins.ListModelMixin, mixins.RetrieveModelMixin):
    waffle_switch = "metadata_field_switch"

    queryset = MetadataField.objects.none()

    serializer_class = MetadataFieldSerializer

    throttle_classes = []

    filterset_class = []

    filterset_fields = [
        "field_type",
        "read_only",
    ]

    search_fields = [
        "key",
    ]

    def get_queryset(self):
        """
        Gets the queryset for the view set
        :return:
        """
        return MetadataField.objects.all()


class MetadataViewSet(
    BaseModelViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
):
    queryset = Metadata.objects.none()

    serializer_class = MetadataSerializer

    throttle_classes = []

    filterset_class = []

    filterset_fields = [
        "assigned_to_content_type",
        "assigned_to_object_id",
    ]

    filter_backends = [
        ContentTypeFilter,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]

    search_fields = []

    pagination_class = None

    def get_queryset(self):
        """
        Gets the queryset for the view set
        :return:
        """
        return Metadata.objects.order_by(
            "order",
            "custom_key",
        )

    @extend_schema(
        parameters=[
            OpenApiParameter("limit", int),
            OpenApiParameter("offset", int),
            OpenApiParameter(
                name="assigned_to_content_type",
                type=str,
                enum=METADATA_CONTENT_TYPES,
                default="",
                required=False,
            ),
            OpenApiParameter("assigned_to_object_id", uuid.UUID),
        ],
        responses={
            200: MetadataSerializer(many=True),
        },
        methods=["GET"],
    )
    def list(self, request, *args, **kwargs):
        assigned_to_content_type = request.query_params.get("assigned_to_content_type", None)
        assigned_to_object_id = request.query_params.get("assigned_to_object_id", None)

        # Prevent this non-paginated list endpoint from returning every metadata entry without filtering it
        if not assigned_to_content_type and not assigned_to_object_id:
            return Response(
                MetadataSerializer([], many=True).data,
                status=status.HTTP_200_OK,
            )

        return super().list(request, *args, **kwargs)

    @extend_schema(
        parameters=[
            OpenApiParameter("ordering", exclude=True),
            OpenApiParameter("search", exclude=True),
        ],
        request=inline_serializer(
            name="MetadataBulkActionSerializer",
            fields={
                "metadata": MetadataPayloadSerializer(
                    required=True,
                ),
                "uploads_datasets": serializers.ListSerializer(
                    child=serializers.UUIDField(),
                    required=True,
                ),
            },
        ),
        description=_("Adds metadata for multiple uploads datasets in bulk."),
        responses={
            201: None,
        },
        methods=["POST"],
    )
    @action(
        detail=False,
        methods=["POST"],
        url_path="bulk-add-to-uploads-datasets",
        url_name="bulk-add-to-uploads-datasets",
        filterset_class=None,
        pagination_class=None,
    )
    def metadata(self, request, *args, **kwargs):
        # Get all uploads datasets the metadata should be added to
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

        if any([not dataset.is_published() and dataset.created_by != request.user for dataset in uploads_datasets]):
            return Response(
                data={
                    "uploads_datasets": _(
                        "You must not edit any unpublished uploads datasets you haven't created yourself.",
                    ),
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        if any(
            [
                dataset.is_published()
                and not can_edit_in_folder(
                    user=request.user,
                    folder_pk=dataset.folder.pk,
                )
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

        metadata_list = request.data.get("metadata", [])
        if not len(metadata_list):
            return Response(
                data={
                    "metadata": _("You must provide at least one metadata."),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Pre-check if metadata are valid
        try:
            validate_metadata(metadata_list)
        except ValidationError as e:
            return Response(
                data={
                    "metadata": str(e),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        for dataset in uploads_datasets:
            try:
                create_uploads_version_with_new_metadata_for_dataset(
                    dataset=dataset,
                    metadata_list=metadata_list,
                    retain_existing_metadata=True,
                )
            except Exception as e:
                logger.error(f"Could not apply new metadata to uploads dataset {dataset}: {e}")

        return Response(
            status=status.HTTP_201_CREATED,
        )


class MetadataTemplateContainerViewSet(
    WaffleSwitchMixin,
    BaseModelViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    LockStatusMixin,
    LockMixin,
    UnlockMixin,
):
    waffle_switch = "metadata_template_container_switch"

    queryset = MetadataTemplateContainer.objects.none()

    serializer_class = MetadataTemplateContainerSerializer

    serializer_action_classes = {
        "create": MetadataTemplateContainerCreateSerializer,
        "update": MetadataTemplateContainerUpdateSerializer,
        "partial_update": MetadataTemplateContainerUpdateSerializer,
    }

    throttle_classes = []

    filterset_class = []

    filterset_fields = [
        "assigned_to_content_type",
        "assigned_to_object_id",
    ]

    filter_backends = [
        ContentTypeFilter,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]

    search_fields = [
        "name",
    ]

    permission_classes_by_action = {
        "create": [IsAuthenticated],
        "list": [IsAuthenticated],
        "retrieve": [IsAuthenticated],
        "update": [IsAuthenticated, CanEditMetadataTemplateContainer],
        "partial_update": [IsAuthenticated, CanEditMetadataTemplateContainer],
        "version": [IsAuthenticated, CanEditMetadataTemplateContainer],
        "restore": [IsAuthenticated, CanEditMetadataTemplateContainer],
        "clone": [IsAuthenticated],
        "import_metadata_template_container": [IsAuthenticated],
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
        from django.contrib.contenttypes.models import ContentType

        from fdm.folders.models import Folder, FolderPermission
        from fdm.projects.models import Project, ProjectMembership

        user = get_current_user()

        # Global metadata templates
        query = Q(
            assigned_to_content_type__isnull=True,
            assigned_to_object_id__isnull=True,
        )

        if user.is_authenticated:
            # Project metadata templates
            query |= Q(
                assigned_to_content_type=ContentType.objects.get_for_model(Project),
                assigned_to_object_id__in=Subquery(
                    ProjectMembership.objects.filter(
                        member=self.request.user,
                    ).values_list(
                        "project__pk",
                        flat=True,
                    ),
                ),
            )

            # Folder metadata templates
            query |= Q(
                assigned_to_content_type=ContentType.objects.get_for_model(Folder),
                assigned_to_object_id__in=Subquery(
                    FolderPermission.objects.filter(
                        project_membership__member=self.request.user,
                    ).values_list(
                        "folder__pk",
                        flat=True,
                    ),
                ),
            )

            # Uploads dataset metadata templates
            query |= Q(
                assigned_to_content_type=ContentType.objects.get_for_model(UploadsDataset),
                assigned_to_object_id__in=Subquery(
                    UploadsDataset.objects.filter(
                        Q(
                            publication_date__isnull=True,
                            created_by=self.request.user,
                        )
                        | Q(
                            folder__folderpermission__project_membership__member=self.request.user,
                        ),
                    ).values_list(
                        "pk",
                        flat=True,
                    ),
                ),
            )

            return MetadataTemplateContainer.objects.filter(query).distinct()

        return MetadataTemplateContainer.objects.none()

    @extend_schema(
        parameters=[
            OpenApiParameter("limit", int),
            OpenApiParameter("offset", int),
            OpenApiParameter(
                name="assigned_to_content_type",
                type=str,
                enum=METADATA_TEMPLATE_CONTENT_TYPES,
                default="",
                required=False,
            ),
            OpenApiParameter("assigned_to_object_id", uuid.UUID),
        ],
        responses={
            200: MetadataTemplateContainerSerializer(many=True),
        },
        methods=["GET"],
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="skip_pool_add",
                type=bool,
                location=OpenApiParameter.QUERY,
                required=False,
                default=False,
                description=(
                    "When true, suppress the automatic addition of a scoped template container "
                    "to its project's or folder's pool. Defaults to false, meaning scoped "
                    "creates are auto-pooled for callers with pool-edit permission."
                ),
            ),
        ],
        responses={
            201: MetadataTemplateContainerSerializer,
        },
        methods=["POST"],
    )
    def create(self, request, *args, **kwargs):
        from fdm.folders.models import Folder
        from fdm.projects.models import Project

        metadata_template_fields = None

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Permission check for global metadata templates
        if not request.data.get("assigned_to_content_type") and not request.data.get("assigned_to_object_id"):
            if not request.user.is_global_metadata_template_admin:
                return Response(
                    data={
                        "assigned_to_object_id": _(
                            "You do not have permission to create global metadata templates.",
                        ),
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

        # Permission check for the linked metadata template type
        else:
            content_type_instance = get_content_type_instance(request.data.get("assigned_to_content_type"))
            model_class = content_type_instance.model_class()

            if get_content_type_for_model(model_class) == get_content_type_for_model(Project):
                if not is_project_metadata_template_admin(request.user, request.data.get("assigned_to_object_id")):
                    return Response(
                        data={
                            "assigned_to_object_id": _(
                                "You do not have permission to create a metadata template for this project.",
                            ),
                        },
                        status=status.HTTP_403_FORBIDDEN,
                    )
            elif get_content_type_for_model(model_class) == get_content_type_for_model(Folder):
                if not is_folder_metadata_template_admin(request.user, request.data.get("assigned_to_object_id")):
                    return Response(
                        data={
                            "assigned_to_object_id": _(
                                "You do not have permission to create a metadata template for this folder.",
                            ),
                        },
                        status=status.HTTP_403_FORBIDDEN,
                    )

        if "metadata_template_fields" in request.data:
            metadata_template_fields = request.data.get("metadata_template_fields", [])

        # Pre-check if metadata fields are valid
        if metadata_template_fields is not None:
            try:
                validate_metadata(metadata_template_fields)
            except ValidationError as e:
                return Response(
                    data={
                        "metadata_template_fields": str(e),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        skip_pool_add = request.query_params.get("skip_pool_add", "").lower() == "true"

        with transaction.atomic():
            self.perform_create(serializer)
            if not skip_pool_add:
                add_scoped_container_to_pool(serializer.instance, request.user)

        headers = self.get_success_headers(serializer.data)

        return Response(
            MetadataTemplateContainerSerializer(serializer.instance).data,
            status=status.HTTP_201_CREATED,
            headers=headers,
        )

    @extend_schema(
        responses={
            200: MetadataTemplateContainerSerializer,
        },
        methods=["PUT"],
    )
    def update(self, request, *args, **kwargs):
        from fdm.folders.models import Folder
        from fdm.projects.models import Project

        instance = self.get_object()

        # Permission check for global metadata templates
        if not instance.assigned_to_content_type and not instance.assigned_to_object_id:
            if not request.user.is_global_metadata_template_admin:
                return Response(
                    data={
                        "assigned_to_object_id": _(
                            "You do not have permission to edit global metadata templates.",
                        ),
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

        # Permission check for the linked metadata template type
        else:
            content_type_instance = get_content_type_instance(instance.assigned_to_content_type)
            model_class = content_type_instance.model_class()

            if (
                get_content_type_for_model(model_class) == get_content_type_for_model(Project)
                and not is_project_metadata_template_admin(request.user, instance.assigned_to_object_id)
                or get_content_type_for_model(model_class) == get_content_type_for_model(Folder)
                and not is_folder_metadata_template_admin(request.user, instance.assigned_to_object_id)
            ):
                return Response(
                    data={
                        "assigned_to_object_id": _(
                            "You do not have permission to edit this metadata template.",
                        ),
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

        return super().update(request, *args, **kwargs)

    @extend_schema(
        responses={
            200: MetadataTemplateContainerSerializer,
        },
        methods=["PATCH"],
    )
    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return super().update(request, *args, **kwargs)

    @extend_schema(
        request=MetadataTemplateContainerVersionActionPayloadSerializer,
        responses={
            201: MetadataTemplateSerializer,
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
        from fdm.folders.models import Folder
        from fdm.projects.models import Project

        metadata_template_container = self.get_object()
        latest_metadata_template = metadata_template_container.latest_metadata_template
        metadata_template_fields = None

        serializer = MetadataTemplateContainerVersionActionPayloadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if metadata_template_container.locked and not metadata_template_container.is_locked_by_myself():
            return Response(
                data={
                    "lock": _("You must not edit an element which has been locked by another user."),
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        if not latest_metadata_template:
            return Response(
                data={
                    "latest_metadata_template": _(
                        "You must create at least one metadata template first before you can make requests to this endpoint.",
                    ),
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # Permission check for global metadata templates
        if (
            not metadata_template_container.assigned_to_content_type
            and not metadata_template_container.assigned_to_object_id
        ):
            if not request.user.is_global_metadata_template_admin:
                return Response(
                    data={
                        "assigned_to_object_id": _(
                            "You do not have permission to create global metadata templates.",
                        ),
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

        # Permission check for the linked metadata template type
        else:
            content_type_instance = get_content_type_instance(metadata_template_container.assigned_to_content_type)
            model_class = content_type_instance.model_class()

            if get_content_type_for_model(model_class) == get_content_type_for_model(Project):
                if not is_project_metadata_template_admin(
                    request.user,
                    metadata_template_container.assigned_to_object_id,
                ):
                    return Response(
                        data={
                            "assigned_to_object_id": _(
                                "You do not have permission to create a metadata template for this project.",
                            ),
                        },
                        status=status.HTTP_403_FORBIDDEN,
                    )
            elif get_content_type_for_model(model_class) == get_content_type_for_model(Folder):
                if not is_folder_metadata_template_admin(
                    request.user,
                    metadata_template_container.assigned_to_object_id,
                ):
                    return Response(
                        data={
                            "assigned_to_object_id": _(
                                "You do not have permission to create a metadata template for this folder.",
                            ),
                        },
                        status=status.HTTP_403_FORBIDDEN,
                    )

        if "metadata_template_fields" in request.data:
            metadata_template_fields = request.data.get("metadata_template_fields", [])

        # Pre-check if metadata fields are valid
        if metadata_template_fields is not None:
            try:
                validate_metadata(metadata_template_fields)
            except ValidationError as e:
                return Response(
                    data={
                        "metadata_template_fields": str(e),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        metadata_template = MetadataTemplate.objects.create(
            name=serializer.data.get("name", None),
            metadata_template_container=metadata_template_container,
        )

        if metadata_template_fields is not None:
            try:
                for metadata_template_field in metadata_template_fields:
                    value = metadata_template_field.pop("value", None)
                    order = metadata_template_field.pop("order", 0)
                    previous_version_field = metadata_template_field.pop("previous_version_field", None)
                    field = MetadataTemplateField.objects.create(
                        metadata_template=metadata_template,
                        previous_version_field_id=(
                            previous_version_field.pk
                            if isinstance(previous_version_field, MetadataTemplateField)
                            else previous_version_field
                        ),
                        order=order,
                        **metadata_template_field,
                    )
                    field.set_value(value)
            except ValidationError as e:
                return Response(
                    data={
                        "metadata_template_fields": str(e),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        return Response(
            data=MetadataTemplateSerializer(metadata_template).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        request=inline_serializer(
            name="MetadataTemplateContainerRestoreVersionActionSerializer",
            fields={
                "metadata_template": serializers.UUIDField(required=True),
            },
        ),
        responses={
            201: MetadataTemplateSerializer,
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
        metadata_template_container = self.get_object()

        try:
            metadata_template = metadata_template_container.restore_version(
                metadata_template=request.data.get("metadata_template", None),
            )
        except Exception as e:
            return Response(
                data={
                    "metadata_template": str(e),
                },
                status=status.HTTP_403_FORBIDDEN if isinstance(e, PermissionDenied) else status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            data=MetadataTemplateSerializer(metadata_template).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        request=MetadataTemplateContainerCloneActionPayloadSerializer,
        responses={
            201: MetadataTemplateContainerSerializer,
        },
        methods=["POST"],
    )
    @action(
        detail=True,
        methods=["POST"],
        url_path="clone",
        url_name="clone",
        throttle_classes=[],
    )
    def clone(self, request, *args, **kwargs):
        metadata_template_container = self.get_object()
        metadata_template = metadata_template_container.latest_metadata_template

        serializer = MetadataTemplateContainerCloneActionPayloadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if not metadata_template:
            raise PermissionDenied(
                _(
                    "A metadata template container must have at least one metadata template before you can clone it.",
                ),
            )

        if serializer.has_clone_target:
            assigned_to_content_type = serializer.validated_data.get("assigned_to_content_type", None)
            assigned_to_object_id = serializer.validated_data.get("assigned_to_object_id", None)
        else:
            assigned_to_content_type = metadata_template_container.assigned_to_content_type
            assigned_to_object_id = metadata_template_container.assigned_to_object_id

        try:
            check_metadata_template_permissions_for_object(
                content_type=assigned_to_content_type,
                object_id=assigned_to_object_id,
                user=request.user,
                require_dataset_permissions=True,
            )

            skip_pool_add = request.query_params.get("skip_pool_add", "").lower() == "true"

            with transaction.atomic():
                new_metadata_template_container = MetadataTemplateContainer.objects.create(
                    name=_("Copy of {name}").format(
                        name=metadata_template_container.name,
                    ),
                    assigned_to_content_type=assigned_to_content_type,
                    assigned_to_object_id=assigned_to_object_id,
                )

                new_metadata_template = MetadataTemplate.objects.create(
                    metadata_template_container=new_metadata_template_container,
                )

                for metadata_template_field in metadata_template.metadata_template_fields.all():
                    MetadataTemplateField.objects.create(
                        metadata_template=new_metadata_template,
                        field=metadata_template_field.field,
                        custom_key=metadata_template_field.custom_key,
                        field_type=metadata_template_field.field_type,
                        config=metadata_template_field.config,
                        mandatory=metadata_template_field.mandatory,
                        order=metadata_template_field.order,
                        previous_version_field=None,
                        value=metadata_template_field.value,
                    )

                if not skip_pool_add:
                    add_scoped_container_to_pool(new_metadata_template_container, request.user)
        except Exception as e:
            return Response(
                data={
                    "error": str(e),
                },
                status=status.HTTP_403_FORBIDDEN if isinstance(e, PermissionDenied) else status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            data=MetadataTemplateContainerSerializer(new_metadata_template_container).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        request=inline_serializer(
            name="MetadataTemplateContainerImportActionSerializer",
            fields={
                "file": serializers.FileField(required=True),
                "assigned_to_content_type": MetadataTemplateContentTypePayloadSerializer(
                    required=False,
                    allow_null=True,
                ),
                "assigned_to_object_id": serializers.UUIDField(
                    required=False,
                    allow_null=True,
                ),
            },
        ),
        responses={
            201: MetadataTemplateContainerSerializer,
        },
        methods=["POST"],
    )
    @action(
        detail=False,
        methods=["POST"],
        url_path="import",
        url_name="import",
        parser_classes=[MultiPartParser, FormParser],
        filterset_class=None,
        pagination_class=None,
    )
    def import_metadata_template_container(self, request, *args, **kwargs):
        try:
            metadata_template_container = import_metadata_template_container_from_json_file(
                uploaded_file=request.FILES.get("file"),
                assigned_to_content_type=request.data.get("assigned_to_content_type"),
                assigned_to_object_id=request.data.get("assigned_to_object_id"),
                user=request.user,
            )
        except Exception as e:
            return Response(
                data={
                    "error": str(e),
                },
                status=status.HTTP_403_FORBIDDEN if isinstance(e, PermissionDenied) else status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            data=MetadataTemplateContainerSerializer(metadata_template_container).data,
            status=status.HTTP_201_CREATED,
        )


class MetadataTemplateViewSet(
    WaffleSwitchMixin,
    BaseModelViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    LockStatusMixin,
    LockMixin,
    UnlockMixin,
):
    waffle_switch = "metadata_template_switch"

    queryset = MetadataTemplate.objects.none()

    serializer_class = MetadataTemplateSerializer

    serializer_action_classes = {
        "list": MetadataTemplateListSerializer,
        "update": MetadataTemplateUpdateSerializer,
        "partial_update": MetadataTemplateUpdateSerializer,
    }

    throttle_classes = []

    filterset_class = []

    filterset_fields = [
        "metadata_template_container",
    ]

    search_fields = [
        "name",
        "metadata_template_container__name",
    ]

    permission_classes_by_action = {
        "list": [IsAuthenticated],
        "retrieve": [IsAuthenticated],
        "update": [IsAuthenticated, CanEditMetadataTemplate],
        "partial_update": [IsAuthenticated, CanEditMetadataTemplate],
    }

    def get_serializer_class(self):
        try:
            return self.serializer_action_classes[self.action]
        except (KeyError, AttributeError):
            return super().get_serializer_class()

    def get_permissions(self):
        return [permission() for permission in self.permission_classes_by_action.get(self.action, [])]

    @extend_schema(
        parameters=[
            OpenApiParameter("limit", int),
            OpenApiParameter("offset", int),
        ],
        responses={
            200: MetadataTemplateSerializer(many=True),
        },
        methods=["GET"],
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        """
        Gets the queryset for the view set
        :return:
        """
        from django.contrib.contenttypes.models import ContentType

        from fdm.folders.models import Folder, FolderPermission
        from fdm.projects.models import Project, ProjectMembership

        user = get_current_user()

        # Global metadata templates
        query = Q(
            metadata_template_container__assigned_to_content_type__isnull=True,
            metadata_template_container__assigned_to_object_id__isnull=True,
        )

        if user.is_authenticated:
            # Project metadata templates
            query |= Q(
                metadata_template_container__assigned_to_content_type=ContentType.objects.get_for_model(Project),
                metadata_template_container__assigned_to_object_id__in=Subquery(
                    ProjectMembership.objects.filter(
                        member=user,
                    ).values_list(
                        "project__pk",
                        flat=True,
                    ),
                ),
            )

            # Folder metadata templates
            query |= Q(
                metadata_template_container__assigned_to_content_type=ContentType.objects.get_for_model(Folder),
                metadata_template_container__assigned_to_object_id__in=Subquery(
                    FolderPermission.objects.filter(
                        project_membership__member=user,
                    ).values_list(
                        "folder__pk",
                        flat=True,
                    ),
                ),
            )

            # Uploads dataset metadata templates
            query |= Q(
                metadata_template_container__assigned_to_content_type=ContentType.objects.get_for_model(UploadsDataset),
                metadata_template_container__assigned_to_object_id__in=Subquery(
                    UploadsDataset.objects.filter(
                        Q(
                            publication_date__isnull=True,
                            created_by=user,
                        )
                        | Q(
                            folder__folderpermission__project_membership__member=user,
                        ),
                    ).values_list(
                        "pk",
                        flat=True,
                    ),
                ),
            )

            return MetadataTemplate.objects.filter(query).distinct()

        return MetadataTemplate.objects.none()

    @extend_schema(
        responses={
            200: MetadataTemplateSerializer,
        },
        methods=["PUT"],
    )
    def update(self, request, *args, **kwargs):
        from fdm.folders.models import Folder
        from fdm.projects.models import Project

        instance = self.get_object()

        # Permission check for global metadata templates
        if not instance.assigned_to_content_type and not instance.assigned_to_object_id:
            if not request.user.is_global_metadata_template_admin:
                return Response(
                    data={
                        "assigned_to_object_id": _(
                            "You do not have permission to edit global metadata templates.",
                        ),
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

        # Permission check for the linked metadata template type
        else:
            content_type_instance = get_content_type_instance(instance.assigned_to_content_type)
            model_class = content_type_instance.model_class()

            if (
                get_content_type_for_model(model_class) == get_content_type_for_model(Project)
                and not is_project_metadata_template_admin(request.user, instance.assigned_to_object_id)
                or get_content_type_for_model(model_class) == get_content_type_for_model(Folder)
                and not is_folder_metadata_template_admin(request.user, instance.assigned_to_object_id)
            ):
                return Response(
                    data={
                        "assigned_to_object_id": _(
                            "You do not have permission to edit this metadata template.",
                        ),
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

        return super().update(request, *args, **kwargs)

    @extend_schema(
        responses={
            200: MetadataTemplateSerializer,
        },
        methods=["PATCH"],
    )
    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return super().update(request, *args, **kwargs)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="compare",
                type=uuid.UUID,
                location=OpenApiParameter.QUERY,
                description="UUID of the metadata template to compare with",
                required=True,
            ),
        ],
        responses={
            200: MetadataTemplateDiffResponseSerializer,
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
                    "compare": _("You must provide a metadata template to compare to."),
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
            compare_instance = MetadataTemplate.objects.get(pk=compare_uuid)
        except MetadataTemplate.DoesNotExist:
            return Response(
                data={
                    "compare": _("A metadata template with this primary key does not exist."),
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        if instance.pk == compare_instance.pk:
            return Response(
                data={
                    "compare": _("You must compare two different metadata templates."),
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
            data=MetadataTemplateDiffResponseSerializer(diff).data,
            status=status.HTTP_200_OK,
        )

    def compare_instances(self, new_instance, old_instance):
        diff = {}

        fields_diff = self.compare_metadata_template_fields(
            old_instance.metadata_template_fields.all(),
            new_instance.metadata_template_fields.all(),
        )

        if fields_diff:
            diff["fields"] = fields_diff

        return diff

    def compare_metadata_template_fields(self, old_metadata_template_fields, new_metadata_template_fields):
        diff = []

        # 1. All fields in the new metadata template that are not linked to a previous field are new fields
        for field in new_metadata_template_fields.filter(previous_version_field__isnull=True):
            diff.append(
                {
                    "old": None,
                    "new": field,
                },
            )

        # 2. All fields in the new metadata template that are linked to a previous field are changed fields
        old_fields_to_ignore = []
        for field in new_metadata_template_fields.filter(previous_version_field__isnull=False):
            matched_version_of_past_field = False

            # First, we need to find the related field in the old metadata template fields
            for old_field in old_metadata_template_fields:
                # If we found our match, we must compare the attributes to see if it really is a change
                if field.is_version_of_past_field(old_field):
                    matched_version_of_past_field = True
                    old_fields_to_ignore.append(old_field.pk)

                    if (
                        old_field.field != field.field
                        or old_field.custom_key != field.custom_key
                        or old_field.field_type != field.field_type
                        or old_field.get_value() != field.get_value()
                        or old_field.config != field.config
                        or old_field.mandatory != field.mandatory
                        or old_field.order != field.order
                    ):
                        diff.append(
                            {
                                "old": old_field,
                                "new": field,
                            },
                        )

                    break

            # If we found no match, we consider it a new field despite having a previous version
            if not matched_version_of_past_field:
                diff.append(
                    {
                        "old": None,
                        "new": field,
                    },
                )

        # 3. We have to remove all fields from the old metadata template that are previous versions of new fields.
        #    The rest of the fields are fields that were removed.
        for field in old_metadata_template_fields.exclude(pk__in=old_fields_to_ignore):
            diff.append(
                {
                    "old": field,
                    "new": None,
                },
            )

        return sorted(diff, key=version_diff_sort_key) if diff else None


class MetadataTemplateFieldViewSet(
    WaffleSwitchMixin,
    BaseModelViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
):
    waffle_switch = "metadata_template_field_switch"

    queryset = MetadataTemplateField.objects.none()

    serializer_class = MetadataTemplateFieldSerializer

    throttle_classes = []

    filterset_class = []

    filterset_fields = [
        "metadata_template",
        "field",
    ]

    search_fields = [
        "custom_key",
    ]

    def get_queryset(self):
        """
        Gets the queryset for the view set
        :return:
        """
        return MetadataTemplateField.objects.order_by(
            "order",
            "custom_key",
        )


class MetadataTagViewSet(
    BaseModelViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
):
    queryset = MetadataTag.objects.none()

    serializer_class = MetadataTagSerializer

    throttle_classes = []

    filterset_class = []

    filterset_fields = [
        "folder",
    ]

    search_fields = [
        "value",
    ]

    def get_queryset(self):
        return MetadataTag.objects.filter(
            folder__folderpermission__project_membership__member=self.request.user,
        ).distinct()

    @extend_schema(
        parameters=[
            OpenApiParameter("limit", int),
            OpenApiParameter("offset", int),
            OpenApiParameter("folder", uuid.UUID, required=True),
        ],
        responses={
            200: MetadataTagSerializer(many=True),
        },
        methods=["GET"],
    )
    def list(self, request, *args, **kwargs):
        folder = request.query_params.get("folder", None)

        if not folder:
            return Response(
                MetadataTagSerializer([], many=True).data,
                status=status.HTTP_200_OK,
            )

        return super().list(request, *args, **kwargs)
