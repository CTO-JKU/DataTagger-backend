from django_userforeignkey.request import get_current_user
from rest_framework import mixins

from fdm.core.rest.base import BaseModelViewSet
from fdm.dbsettings.models import *
from fdm.dbsettings.rest.serializers import *

__all__ = [
    "SettingsViewSet",
]


class SettingsViewSet(BaseModelViewSet, mixins.ListModelMixin, mixins.RetrieveModelMixin):
    queryset = Setting.objects.none()

    serializer_class = SettingsSerializer

    filterset_class = []

    filterset_fields = [
        "key",
    ]

    search_fields = [
        "key",
    ]

    pagination_class = None

    permission_classes_by_action = {
        "list": [],
        "retrieve": [],
    }

    def get_permissions(self):
        return [permission() for permission in self.permission_classes_by_action.get(self.action, [])]

    def get_queryset(self):
        """
        Gets the queryset for the view set
        :return:
        """
        user = get_current_user()

        if not user.is_authenticated:
            return Setting.objects.exclude(public=False)

        return Setting.objects.all()
