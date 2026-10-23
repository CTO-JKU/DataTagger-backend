from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q, Subquery
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import filters, mixins, serializers, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from fdm.core.rest.base import BaseModelViewSet
from fdm.core.rest.mixins import *
from fdm.core.rest.permissions import (
    CanCreateFolderPermission,
    CanCreateFolders,
    CanManageSubfolders,
    CanViewFolder,
    IsFolderAdmin,
    IsFolderAdminForPermission,
)
from fdm.folders.helpers import add_folder_permissions_for_users
from fdm.folders.models import *
from fdm.folders.rest.filter import FolderFilter, FolderPermissionFilter, SubfolderFilter
from fdm.folders.rest.serializers import *
from fdm.metadata.helpers import set_metadata_for_relation
from fdm.metadata.models import MetadataTemplateContainer
from fdm.metadata.rest.serializers import MetadataTemplateContainerSerializer, MetadataTemplateSerializer

__all__ = [
    "FolderViewSet",
    "FolderPermissionViewSet",
    "SubfolderViewSet",
]


class FolderViewSet(
    BaseModelViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    LockStatusMixin,
    LockMixin,
    UnlockMixin,
):
    queryset = Folder.objects.none()

    serializer_class = FolderSerializer

    serializer_action_classes = {
        "list": FolderListSerializer,
        "create": FolderCreateSerializer,
        "update": FolderUpdateSerializer,
        "partial_update": FolderUpdateSerializer,
    }

    throttle_classes = []

    filterset_class = FolderFilter

    filterset_fields = []

    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]

    search_fields = [
        "name",
    ]

    permission_classes_by_action = {
        "create": [IsAuthenticated, CanCreateFolders],
        "list": [IsAuthenticated],
        "retrieve": [IsAuthenticated, CanViewFolder],
        "update": [IsAuthenticated, IsFolderAdmin],
        "partial_update": [IsAuthenticated, IsFolderAdmin],
        "destroy": [IsAuthenticated, IsFolderAdmin],
        "permissions": [IsAuthenticated, IsFolderAdmin],
        "metadata_template_container": [IsAuthenticated, CanViewFolder],
        "metadata_templates": [IsAuthenticated, CanViewFolder],
        "set_metadata_template_container_pool": [IsAuthenticated, IsFolderAdmin],
        "get_metadata_template_container_pool": [IsAuthenticated, CanViewFolder],
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
        return Folder.objects.filter(
            folderpermission__project_membership__member=self.request.user,
        )

    @extend_schema(
        request=FolderCreatePayloadSerializer,
        responses={
            201: FolderSerializer,
        },
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)

        return Response(
            FolderSerializer(serializer.instance).data,
            status=status.HTTP_201_CREATED,
            headers=headers,
        )

    @extend_schema(
        request=FolderUpdatePayloadSerializer,
        responses={
            200: FolderSerializer,
        },
    )
    def update(self, request, *args, **kwargs):
        try:
            partial = kwargs.pop("partial", False)
            instance = self.get_object()
            serializer = self.get_serializer(
                instance,
                data=request.data,
                partial=partial,
            )
            serializer.is_valid(raise_exception=True)
            self.perform_update(serializer)

            return Response(
                FolderSerializer(serializer.instance).data,
                status=status.HTTP_200_OK,
            )
        except ValidationError as e:
            return Response(
                data={
                    "error": "\n".join(e.messages),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

    @extend_schema(
        request=FolderUpdatePayloadSerializer,
        responses={
            200: FolderSerializer,
        },
    )
    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True

        try:
            return self.update(request, *args, **kwargs)
        except ValidationError as e:
            return Response(
                data={
                    "error": "\n".join(e.messages),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

    @extend_schema(
        parameters=[
            OpenApiParameter("ordering", exclude=True),
            OpenApiParameter("search", exclude=True),
        ],
        request=FolderPermissionsActionPayloadSerializer,
        responses={
            200: FolderPermissionSerializer(many=True),
        },
        methods=["PUT"],
    )
    @action(
        detail=True,
        methods=["PUT"],
        url_path="permissions",
        url_name="permissions",
        filterset_class=None,
        pagination_class=None,
    )
    def permissions(self, request, *args, **kwargs):
        serializer = FolderPermissionsActionPayloadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        folder = self.get_object()
        folder_users = request.data.get("folder_users", [])

        add_folder_permissions_for_users(folder, folder_users)

        # Delete folder members which are currently assigned to this folder but are missing from the request
        obsolete_folder_permissions = FolderPermission.objects.filter(
            folder=folder,
        ).exclude(
            project_membership__member__email__in=[user["email"] for user in folder_users],
        )

        for folder_permission in obsolete_folder_permissions:
            folder_permission.delete()

        # Get the updated member list for the response
        folder_permissions = FolderPermission.objects.filter(
            folder=folder,
        ).all()

        return Response(
            data=FolderPermissionSerializer(folder_permissions, many=True).data,
            status=status.HTTP_200_OK,
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
        folder = self.get_object()
        metadata_template_container = folder.get_available_metadata_template_container()

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
        folder = self.get_object()
        metadata_templates = folder.get_available_metadata_templates()

        return Response(
            data=MetadataTemplateSerializer(metadata_templates, many=True).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        parameters=[
            OpenApiParameter("ordering", exclude=True),
            OpenApiParameter("search", exclude=True),
        ],
        request=inline_serializer(
            name="FolderMetadataTemplateContainerPoolActionSerializer",
            fields={
                "metadata_template_container": serializers.ListSerializer(
                    child=serializers.UUIDField(),
                    required=True,
                ),
            },
        ),
        responses={
            200: None,
        },
        methods=["PUT"],
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
        methods=[
            "GET",
            "PUT",
        ],
        url_path="metadata-template-container-pool",
        url_name="metadata-template-container-pool",
        filterset_class=None,
        pagination_class=None,
    )
    def metadata_template_container_pool(self, request, *args, **kwargs):
        instance = self.get_object()

        if request.method == "PUT":
            try:
                metadata_template_container = [
                    MetadataTemplateContainer.objects.get(pk=container_pk)
                    for container_pk in request.data.get("metadata_template_container", [])
                ]
            except MetadataTemplateContainer.DoesNotExist:
                return Response(
                    data={
                        "metadata_template_container": _(
                            "At least one metadata template container provided does not exist.",
                        ),
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )

            try:
                instance.metadata_template_container.set([container for container in metadata_template_container])
            except Exception as e:
                return Response(
                    data={
                        "metadata_template_container": _(
                            f"An error occurred while adding metadata template containers to the pool: {e}",
                        ),
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            return Response(
                status=status.HTTP_200_OK,
            )

        metadata_template_container = instance.metadata_template_container.all()

        # Add the metadata template container of the folder to the pool
        if (
            instance.metadata_template
            and not metadata_template_container.filter(
                pk=instance.metadata_template.metadata_template_container.pk,
            ).exists()
        ):
            metadata_template_container |= MetadataTemplateContainer.objects.filter(
                pk=instance.metadata_template.metadata_template_container.pk,
            )

        # Add the metadata template container of the project to the pool
        if (
            instance.project.metadata_template
            and not metadata_template_container.filter(
                pk=instance.project.metadata_template.metadata_template_container.pk,
            ).exists()
        ):
            metadata_template_container |= MetadataTemplateContainer.objects.filter(
                pk=instance.project.metadata_template.metadata_template_container.pk,
            )

        return Response(
            data=MetadataTemplateContainerSerializer(
                metadata_template_container.order_by("name"),
                many=True,
            ).data,
            status=status.HTTP_200_OK,
        )


@extend_schema(
    parameters=[
        OpenApiParameter("ordering", exclude=True),
        OpenApiParameter("search", exclude=True),
    ],
)
class FolderPermissionViewSet(
    BaseModelViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
):
    queryset = FolderPermission.objects.none()

    serializer_class = FolderPermissionSerializer

    serializer_action_classes = {
        "create": FolderPermissionCreateSerializer,
        "update": FolderPermissionUpdateSerializer,
        "partial_update": FolderPermissionUpdateSerializer,
    }

    throttle_classes = []

    filterset_class = FolderPermissionFilter

    filterset_fields = []

    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]

    search_fields = [
        "project_membership__member__email",
    ]

    pagination_class = None

    ordering_fields = [
        "pk",
        "folder",
        "project_membership",
        "project_membership__member",
        "project_membership__member__email",
        "is_folder_admin",
        "is_metadata_template_admin",
        "can_edit",
    ]

    permission_classes_by_action = {
        "create": [IsAuthenticated, CanCreateFolderPermission],
        "list": [IsAuthenticated, CanViewFolder],
        "retrieve": [IsAuthenticated, CanViewFolder],
        "update": [IsAuthenticated, IsFolderAdminForPermission],
        "partial_update": [IsAuthenticated, IsFolderAdminForPermission],
        "destroy": [IsAuthenticated, IsFolderAdminForPermission],
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
        return FolderPermission.objects.filter(
            folder__pk__in=Subquery(
                FolderPermission.objects.filter(
                    project_membership__member=self.request.user,
                ).values_list(
                    "folder__pk",
                    flat=True,
                ),
            ),
        )

    @extend_schema(
        responses={
            201: FolderPermissionSerializer,
        },
        methods=["POST"],
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)

        return Response(
            FolderPermissionSerializer(serializer.instance).data,
            status=status.HTTP_201_CREATED,
            headers=headers,
        )

    @extend_schema(
        responses={
            200: FolderPermissionSerializer,
        },
        methods=["PUT"],
    )
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @extend_schema(
        responses={
            200: FolderPermissionSerializer,
        },
        methods=["PATCH"],
    )
    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return super().update(request, *args, **kwargs)


@extend_schema(
    parameters=[
        OpenApiParameter("search", exclude=True),
    ],
)
class SubfolderViewSet(
    BaseModelViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
):
    queryset = Subfolder.objects.none()

    serializer_class = SubfolderSerializer

    serializer_action_classes = {
        "create": SubfolderCreateSerializer,
        "update": SubfolderUpdateSerializer,
        "partial_update": SubfolderUpdateSerializer,
    }

    throttle_classes = []

    filterset_class = SubfolderFilter

    filterset_fields = []

    filter_backends = [
        DjangoFilterBackend,
        filters.OrderingFilter,
    ]

    pagination_class = None

    ordering_fields = [
        "name",
        "creation_date",
    ]

    ordering = [
        "name",
    ]

    permission_classes_by_action = {
        "create": [IsAuthenticated, CanManageSubfolders],
        "list": [IsAuthenticated],
        "retrieve": [IsAuthenticated],
        "update": [IsAuthenticated, CanManageSubfolders],
        "partial_update": [IsAuthenticated, CanManageSubfolders],
        "destroy": [IsAuthenticated, CanManageSubfolders],
        "metadata": [IsAuthenticated, CanManageSubfolders],
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
        return (
            Subfolder.objects.filter(
                folder__in=Subquery(
                    FolderPermission.objects.filter(
                        project_membership__member=self.request.user,
                    ).values_list(
                        "folder__pk",
                        flat=True,
                    ),
                ),
            )
            .select_related("parent")
            .annotate(
                datasets_count_annotated=Count(
                    "uploads_dataset",
                    filter=Q(uploads_dataset__publication_date__isnull=False),
                    distinct=True,
                ),
                subfolders_count_annotated=Count("children", distinct=True),
            )
        )

    def _save(self, serializer):
        try:
            serializer.save()
        except ValidationError as e:
            raise serializers.ValidationError(e.message_dict if hasattr(e, "error_dict") else e.messages) from e

    @extend_schema(
        responses={
            201: SubfolderSerializer,
        },
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self._save(serializer)

        return Response(
            SubfolderSerializer(serializer.instance).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        responses={
            200: SubfolderSerializer,
        },
    )
    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self._save(serializer)

        return Response(
            SubfolderSerializer(serializer.instance).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        responses={
            200: SubfolderSerializer,
        },
    )
    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    @extend_schema(
        request=SubfolderMetadataPayloadSerializer,
        responses={
            200: SubfolderSerializer,
        },
    )
    @action(
        detail=True,
        methods=["PUT"],
        url_path="metadata",
        url_name="metadata",
        filterset_class=None,
        pagination_class=None,
    )
    def metadata(self, request, *args, **kwargs):
        """
        Replaces the metadata of a subfolder, in the same format as the metadata of folders and files.
        """
        instance = self.get_object()
        serializer = SubfolderMetadataPayloadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            with transaction.atomic():
                set_metadata_for_relation(
                    metadata_list=serializer.validated_data["metadata"],
                    relation=instance,
                )
        except ValidationError as e:
            raise serializers.ValidationError(
                {"metadata": e.message_dict if hasattr(e, "error_dict") else e.messages},
            ) from e

        return Response(SubfolderSerializer(self.get_object()).data, status=status.HTTP_200_OK)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()

        if instance.has_datasets():
            return Response(
                data={
                    "error": _("Only subfolders without files can be deleted. Please move or delete the files first."),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        instance.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)
