import uuid

from django.contrib.contenttypes.models import ContentType
from django.db.models import Q, Subquery
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import filters, mixins, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from fdm.core.rest.base import BaseModelViewSet
from fdm.core.rest.permissions import can_edit_in_folder
from fdm.core.rest.serializers import METADATA_TEMPLATE_CONTENT_TYPES
from fdm.file_parser.models import FileParserConfiguration
from fdm.file_parser.registry import get_available_parser_definitions
from fdm.file_parser.rest.serializers import FileParserAvailableTypeSerializer, FileParserConfigurationSerializer
from fdm.file_parser.tasks import schedule_existing_dataset_files_for_configuration
from fdm.folders.models import Folder, FolderPermission
from fdm.metadata.rest.filter import ContentTypeFilter
from fdm.projects.models import Project, ProjectMembership
from fdm.uploads.models import UploadsDataset

__all__ = [
    "FileParserConfigurationViewSet",
]


def can_manage_parser_scope(user, content_type: ContentType, object_id) -> bool:
    if content_type == ContentType.objects.get_for_model(Project):
        return ProjectMembership.objects.filter(
            member=user,
            project_id=object_id,
            is_project_admin=True,
        ).exists()

    if content_type == ContentType.objects.get_for_model(Folder):
        return FolderPermission.objects.filter(
            project_membership__member=user,
            folder_id=object_id,
            is_folder_admin=True,
        ).exists()

    if content_type == ContentType.objects.get_for_model(UploadsDataset):
        try:
            dataset = UploadsDataset.objects.select_related("folder").get(pk=object_id)
        except UploadsDataset.DoesNotExist:
            return False

        if dataset.folder:
            return can_edit_in_folder(user, dataset.folder.pk)

        return dataset.created_by == user

    return False


class FileParserConfigurationViewSet(
    BaseModelViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
):
    queryset = FileParserConfiguration.objects.none()

    serializer_class = FileParserConfigurationSerializer

    permission_classes = [
        IsAuthenticated,
    ]

    filterset_fields = [
        "parser_type",
        "enabled",
    ]

    filter_backends = [
        ContentTypeFilter,
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]

    search_fields = [
        "output_metadata_key",
    ]

    ordering_fields = [
        "parser_type",
        "output_metadata_key",
        "creation_date",
    ]

    @extend_schema(
        parameters=[
            OpenApiParameter("limit", int),
            OpenApiParameter("offset", int),
            OpenApiParameter(
                name="parser_type",
                type=str,
                enum=[choice[0] for choice in FileParserConfiguration._meta.get_field("parser_type").choices],
                required=False,
            ),
            OpenApiParameter(
                name="assigned_to_content_type",
                type=str,
                enum=METADATA_TEMPLATE_CONTENT_TYPES,
                required=False,
            ),
            OpenApiParameter("assigned_to_object_id", uuid.UUID),
            OpenApiParameter("enabled", bool),
        ],
        responses={
            200: FileParserConfigurationSerializer(many=True),
        },
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        user = self.request.user

        project_content_type = ContentType.objects.get_for_model(Project)
        folder_content_type = ContentType.objects.get_for_model(Folder)
        dataset_content_type = ContentType.objects.get_for_model(UploadsDataset)

        return FileParserConfiguration.objects.filter(
            Q(
                assigned_to_content_type=project_content_type,
                assigned_to_object_id__in=Subquery(
                    ProjectMembership.objects.filter(
                        member=user,
                    ).values_list("project_id", flat=True),
                ),
            )
            | Q(
                assigned_to_content_type=folder_content_type,
                assigned_to_object_id__in=Subquery(
                    FolderPermission.objects.filter(
                        project_membership__member=user,
                    ).values_list("folder_id", flat=True),
                ),
            )
            | Q(
                assigned_to_content_type=dataset_content_type,
                assigned_to_object_id__in=Subquery(
                    UploadsDataset.objects.filter(
                        Q(created_by=user, folder__isnull=True)
                        | Q(folder__folderpermission__project_membership__member=user),
                    ).values_list("pk", flat=True),
                ),
            )
        ).distinct()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if not can_manage_parser_scope(
            request.user,
            serializer.validated_data["assigned_to_content_type"],
            serializer.validated_data["assigned_to_object_id"],
        ):
            return Response(status=status.HTTP_403_FORBIDDEN)

        self.perform_create(serializer)
        schedule_existing_dataset_files_for_configuration(serializer.instance)
        headers = self.get_success_headers(serializer.data)

        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)

        content_type = serializer.validated_data.get("assigned_to_content_type", instance.assigned_to_content_type)
        object_id = serializer.validated_data.get("assigned_to_object_id", instance.assigned_to_object_id)

        if not can_manage_parser_scope(request.user, content_type, object_id):
            return Response(status=status.HTTP_403_FORBIDDEN)

        self.perform_update(serializer)

        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()

        if not can_manage_parser_scope(
            request.user,
            instance.assigned_to_content_type,
            instance.assigned_to_object_id,
        ):
            return Response(status=status.HTTP_403_FORBIDDEN)

        return super().destroy(request, *args, **kwargs)

    @action(
        detail=False,
        methods=["get"],
        url_path="available-types",
    )
    def available_types(self, request, *args, **kwargs):
        serializer = FileParserAvailableTypeSerializer(
            [definition.serialize() for definition in get_available_parser_definitions()],
            many=True,
        )

        return Response(serializer.data)
