import os
import uuid

from django.db import transaction
from django.http import FileResponse, HttpResponseBadRequest, HttpResponseNotFound
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, status
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from fdm.core.rest.base import BaseModelViewSet
from fdm.exports.models import ExportJob, MetadataTemplateExportJob
from fdm.exports.rest.filter import ExportJobFilter
from fdm.exports.rest.serializers import (
    ExportJobCreateSerializer,
    ExportJobSerializer,
    MetadataTemplateExportJobCreateSerializer,
    MetadataTemplateExportJobSerializer,
)
from fdm.exports.services import get_allowed_export_types
from fdm.exports.tasks import build_export_zip, build_metadata_template_export_zip

__all__ = [
    "ExportJobViewSet",
    "MetadataTemplateExportJobViewSet",
]


class ExportJobViewSet(
    BaseModelViewSet,
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
):
    queryset = ExportJob.objects.none()
    serializer_class = ExportJobSerializer
    filterset_class = ExportJobFilter
    ordering_fields = [
        "expires_at",
        "finished_at",
    ]

    serializer_action_classes = {
        "create": ExportJobCreateSerializer,
    }

    permission_classes_by_action = {
        "create": [IsAuthenticated],
        "list": [IsAuthenticated],
        "retrieve": [IsAuthenticated],
        "download": [AllowAny],
    }

    def get_serializer_class(self):
        try:
            return self.serializer_action_classes[self.action]
        except (KeyError, AttributeError):
            return super().get_serializer_class()

    def get_permissions(self):
        return [permission() for permission in self.permission_classes_by_action.get(self.action, [])]

    def get_queryset(self):
        if self.request.user and self.request.user.is_authenticated:
            return ExportJob.objects.filter(created_by=self.request.user).order_by("-creation_date")

        return ExportJob.objects.none()

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "status",
                str,
                enum=[choice[0] for choice in ExportJob.Status.choices],
            ),
            OpenApiParameter(
                "export_type",
                str,
                enum=get_allowed_export_types(),
            ),
            OpenApiParameter(
                "metadata_format",
                str,
                enum=[choice[0] for choice in ExportJob.MetadataFormat.choices],
            ),
            OpenApiParameter("include_uploads_version_file", bool),
            OpenApiParameter("include_uploads_version_file_metadata", bool),
            OpenApiParameter(
                "object_id",
                uuid.UUID,
                description=_("Filter export jobs whose exported object list contains this UUID."),
            ),
            OpenApiParameter(
                "ordering",
                str,
                enum=[
                    "expires_at",
                    "-expires_at",
                    "finished_at",
                    "-finished_at",
                ],
            ),
        ],
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(
        request=ExportJobCreateSerializer,
        responses={
            202: ExportJobSerializer,
        },
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        export_job_id = uuid.uuid4()
        export_job = serializer.save(
            export_job_id=export_job_id,
            download_base_url=request.build_absolute_uri(
                reverse("export-download", kwargs={"pk": export_job_id}),
            ),
        )

        if (
            not export_job.source_export_job_id
            and not export_job.zip_file
            and export_job.status == ExportJob.Status.SCHEDULED
        ):
            transaction.on_commit(lambda: build_export_zip.apply_async((str(export_job.pk),)))

        return Response(
            ExportJobSerializer(export_job, context=self.get_serializer_context()).data,
            status=status.HTTP_202_ACCEPTED,
        )

    @action(
        detail=True,
        methods=["GET"],
        url_path="download",
        url_name="download",
        permission_classes=[AllowAny],
    )
    def download(self, request, pk=None):
        token = request.query_params.get("token", "")

        try:
            export_job = ExportJob.objects.get(pk=pk)
        except ExportJob.DoesNotExist:
            return HttpResponseNotFound(_("The requested export does not exist."))

        is_owner = (
            request.user
            and request.user.is_authenticated
            and export_job.created_by_id
            and export_job.created_by_id == request.user.pk
        )

        if (
            export_job.status != ExportJob.Status.FINISHED
            or export_job.is_download_expired()
            or not (is_owner or export_job.check_download_token(token))
            or not export_job.zip_file
        ):
            return HttpResponseNotFound(_("The requested export does not exist or has expired."))

        try:
            export_file = export_job.zip_file.open("rb")
            return FileResponse(
                export_file,
                as_attachment=True,
                filename=os.path.basename(export_job.zip_file.name),
                content_type="application/zip",
            )
        except Exception:
            return HttpResponseBadRequest()


class MetadataTemplateExportJobViewSet(
    BaseModelViewSet,
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
):
    queryset = MetadataTemplateExportJob.objects.none()
    serializer_class = MetadataTemplateExportJobSerializer
    ordering_fields = [
        "expires_at",
        "finished_at",
    ]

    serializer_action_classes = {
        "create": MetadataTemplateExportJobCreateSerializer,
    }

    permission_classes_by_action = {
        "create": [IsAuthenticated],
        "list": [IsAuthenticated],
        "retrieve": [IsAuthenticated],
        "download": [AllowAny],
    }

    def get_serializer_class(self):
        try:
            return self.serializer_action_classes[self.action]
        except (KeyError, AttributeError):
            return super().get_serializer_class()

    def get_permissions(self):
        return [permission() for permission in self.permission_classes_by_action.get(self.action, [])]

    def get_queryset(self):
        if self.request.user and self.request.user.is_authenticated:
            return MetadataTemplateExportJob.objects.filter(created_by=self.request.user).order_by("-creation_date")

        return MetadataTemplateExportJob.objects.none()

    @extend_schema(
        request=MetadataTemplateExportJobCreateSerializer,
        responses={
            202: MetadataTemplateExportJobSerializer,
        },
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        export_job_id = uuid.uuid4()
        export_job = serializer.save(
            export_job_id=export_job_id,
            download_base_url=request.build_absolute_uri(
                reverse("metadata-template-export-download", kwargs={"pk": export_job_id}),
            ),
        )

        transaction.on_commit(lambda: build_metadata_template_export_zip.apply_async((str(export_job.pk),)))

        return Response(
            MetadataTemplateExportJobSerializer(export_job, context=self.get_serializer_context()).data,
            status=status.HTTP_202_ACCEPTED,
        )

    @action(
        detail=True,
        methods=["GET"],
        url_path="download",
        url_name="download",
        permission_classes=[AllowAny],
    )
    def download(self, request, pk=None):
        token = request.query_params.get("token", "")

        try:
            export_job = MetadataTemplateExportJob.objects.get(pk=pk)
        except MetadataTemplateExportJob.DoesNotExist:
            return HttpResponseNotFound(_("The requested export does not exist."))

        is_owner = (
            request.user
            and request.user.is_authenticated
            and export_job.created_by_id
            and export_job.created_by_id == request.user.pk
        )

        if (
            export_job.status != MetadataTemplateExportJob.Status.FINISHED
            or export_job.is_download_expired()
            or not (is_owner or export_job.check_download_token(token))
            or not export_job.zip_file
        ):
            return HttpResponseNotFound(_("The requested export does not exist or has expired."))

        try:
            export_file = export_job.zip_file.open("rb")
            return FileResponse(
                export_file,
                as_attachment=True,
                filename=os.path.basename(export_job.zip_file.name),
                content_type="application/zip",
            )
        except Exception:
            return HttpResponseBadRequest()
