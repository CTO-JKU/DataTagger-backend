from django.db.models import Q
from rest_framework import mixins

from fdm.core.rest.base import BaseModelViewSet
from fdm.faq.models import *
from fdm.faq.rest.serializers import *

__all__ = [
    "FAQCategoryViewSet",
    "FAQViewSet",
]


class FAQCategoryViewSet(
    BaseModelViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
):
    queryset = FAQCategory.objects.none()

    serializer_class = FAQCategorySerializer

    throttle_classes = []

    filterset_class = []

    filterset_fields = []

    search_fields = [
        "category__name",
        "question",
        "answer",
    ]

    authentication_classes = []

    pagination_class = None

    permission_classes = []

    def get_queryset(self):
        """
        Gets the queryset for the view set
        :return:
        """
        return FAQCategory.objects.filter(
            published=True,
        ).order_by(
            "order",
        )


class FAQViewSet(
    BaseModelViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
):
    queryset = FAQ.objects.none()

    serializer_class = FAQSerializer

    throttle_classes = []

    filterset_class = []

    filterset_fields = []

    search_fields = [
        "category__name",
        "question",
        "answer",
    ]

    authentication_classes = []

    pagination_class = None

    permission_classes = []

    def get_queryset(self):
        """
        Gets the queryset for the view set
        :return:
        """
        return FAQ.objects.filter(
            Q(
                category__isnull=False,
                category__published=True,
            )
            | Q(
                category__isnull=True,
            ),
            published=True,
        ).order_by(
            "category__order",
            "order",
        )
