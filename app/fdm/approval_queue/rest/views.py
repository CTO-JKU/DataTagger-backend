from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_userforeignkey.request import get_current_user
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status
from rest_framework.decorators import action
from rest_framework.response import Response

from fdm.approval_queue.models import ApprovalQueue
from fdm.approval_queue.rest.serializers import ApprovalQueueSerializer
from fdm.core.rest.base import BaseModelViewSet
from fdm.storages.models import DynamicStorage

__all__ = [
    "ApprovalQueueViewSet",
]


class ApprovalQueueViewSet(
    BaseModelViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
):
    queryset = ApprovalQueue.objects.none()

    serializer_class = ApprovalQueueSerializer

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
        if current_user.is_authenticated and current_user.is_global_approval_queue_admin:
            return ApprovalQueue.objects.filter(approved_at__isnull=True)
        return ApprovalQueue.objects.none()

    @extend_schema(
        methods=["GET"],
    )
    @action(
        detail=True,
        methods=["GET"],
        url_path="approve",
        url_name="approve",
        filterset_class=None,
        pagination_class=None,
    )
    def approve(self, request, pk=None):
        try:
            obj = self.get_object()
            content_object = obj.content_object
            content_object.approved = True
            content_object.save()
            obj.approved_by = request.user
            obj.approved_at = timezone.now()
            obj.save()

            return Response(
                {
                    "detail": _("Item approved successfully!"),
                },
                status=status.HTTP_200_OK,
            )

        except DynamicStorage.DoesNotExist:
            return Response(
                {
                    "detail": _("Not found."),
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        except Exception as e:
            return Response(
                {
                    "detail": str(e),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
