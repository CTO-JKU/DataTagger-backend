from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from fdm.cms.models.models import Content
from fdm.cms.rest.serializers import ContentPageListSerializer, ContentSerializer

__all__ = [
    "ContentViewSet",
]


class ContentViewSet(RetrieveModelMixin, ListModelMixin, GenericViewSet):
    authentication_classes = ()

    pagination_class = None

    permission_classes = ()

    serializer_class = ContentSerializer

    def get_queryset(self):
        """
        Gets the queryset for the view set
        :return:
        """
        return Content.objects.none()

    lookup_field = "slug"

    @extend_schema(
        responses={
            200: ContentPageListSerializer,
        },
        methods=["GET"],
    )
    @action(
        detail=False,
        methods=["GET"],
        url_path="slugs",
        url_name="slugs",
        pagination_class=None,
    )
    def slugs(self, request, *args, **kwargs):
        queryset = Content.objects.filter(published=True)
        slugs = queryset.values_list("slug", flat=True).distinct()
        return Response(
            data=ContentPageListSerializer({"slugs": list(slugs)}).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        responses={
            200: ContentSerializer,
        },
    )
    def retrieve(self, request, *args, **kwargs):
        slug = kwargs.get("slug")
        obj = get_object_or_404(Content.objects.filter(published=True), slug=slug)
        serializer = self.get_serializer(obj)
        return Response(serializer.data)
