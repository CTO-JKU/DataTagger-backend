from django.contrib.contenttypes.models import ContentType
from django.utils.translation import gettext_lazy as _
from rest_framework import filters

__all__ = [
    "ContentTypeFilter",
]

from fdm.core.helpers import get_content_type_instance


class ContentTypeFilter(filters.BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):
        query_params = request.query_params.copy()

        if "assigned_to_content_type" in query_params:
            assigned_to_content_type = query_params.get("assigned_to_content_type", None)

            try:
                assigned_to_content_type = int(assigned_to_content_type)
            except ValueError:
                pass

            if isinstance(assigned_to_content_type, int):
                content_type_object = ContentType.objects.get(pk=assigned_to_content_type)
            elif not assigned_to_content_type:
                content_type_object = None
            elif isinstance(assigned_to_content_type, str):
                content_type_object = get_content_type_instance(assigned_to_content_type)
            else:
                raise ValueError(_("Invalid template type"))

            queryset = queryset.filter(assigned_to_content_type=content_type_object)

        if "assigned_to_object_id" in query_params:
            assigned_to_object_id = query_params.get("assigned_to_object_id", None)
            queryset = queryset.filter(assigned_to_object_id=assigned_to_object_id)

        return queryset
