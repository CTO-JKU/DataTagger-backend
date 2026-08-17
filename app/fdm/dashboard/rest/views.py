from drf_spectacular.utils import extend_schema
from rest_framework.response import Response

from fdm.core.rest.base import BaseGenericViewSet
from fdm.dashboard.rest.serializers import DashboardSnapshotSerializer
from fdm.dashboard.selectors import build_dashboard_snapshot

__all__ = [
    "DashboardViewSet",
]


class DashboardViewSet(BaseGenericViewSet):
    throttle_classes = []

    @extend_schema(
        responses={
            200: DashboardSnapshotSerializer(many=False),
        },
    )
    def list(self, request):
        snapshot = build_dashboard_snapshot(request.user)

        return Response(DashboardSnapshotSerializer(snapshot).data)
