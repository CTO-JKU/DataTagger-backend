from django_userforeignkey.request import get_current_user
from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import mixins, status
from rest_framework.response import Response

from fdm.core.rest.base import BaseModelViewSet
from fdm.storages.models import DynamicStorage
from fdm.storages.rest.serializers import StorageCreatePayloadSerializer, StorageSerializer

__all__ = [
    "StorageViewSet",
]


class StorageViewSet(
    BaseModelViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
):
    queryset = DynamicStorage.objects.none()

    serializer_class = StorageSerializer

    throttle_classes = []

    filterset_class = []

    filterset_fields = []

    search_fields = [
        "name",
    ]

    def get_queryset(self):
        """
        Gets the queryset for the view set
        :return:
        """
        current_user = get_current_user()
        return DynamicStorage.objects.filter(created_by=current_user)

    @extend_schema(
        request=StorageCreatePayloadSerializer,
        responses={
            201: StorageSerializer,
        },
        examples=[
            OpenApiExample(
                "Create Storage Example 1",
                description="Example of creating a storage entry with the restricted storage_type",
                value={
                    "name": "Example Storage",
                    "storage_type": "private_dss",
                    "local_private_dss_path": "/path/to/private_dss",
                },
                request_only=True,
            ),
            OpenApiExample(
                "Create Storage Example 2",
                description="Example of creating a storage entry with the restricted storage_type",
                value={
                    "name": "Example Storage",
                    "description": {
                        "purpose": "Sample storage for data uploads",
                    },
                    "storage_type": "private_dss",
                    "local_private_dss_path": "/path/to/private_dss",
                },
                request_only=True,
            ),
        ],
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)

        return Response(
            StorageSerializer(serializer.instance).data,
            status=status.HTTP_201_CREATED,
            headers=headers,
        )
