from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework

from fdm.exports.models import ExportJob

__all__ = [
    "ExportJobFilter",
]


class ExportJobFilter(rest_framework.FilterSet):
    object_id = rest_framework.UUIDFilter(
        label=_("Filter by exported object UUID"),
        method="filter_by_object_id",
    )

    class Meta:
        model = ExportJob
        fields = [
            "status",
            "export_type",
            "metadata_format",
            "include_uploads_version_file",
            "include_uploads_version_file_metadata",
            "object_id",
        ]

    def filter_by_object_id(self, queryset, name, value):
        return queryset.filter(
            object_ids__contains=[
                str(value),
            ],
        )
