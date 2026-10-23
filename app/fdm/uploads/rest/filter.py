from django.db.models import OuterRef, Subquery
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework

from fdm.uploads.models import UploadsDataset, UploadsVersion

__all__ = [
    "UploadsDatasetMetadataIsCompleteRestFilter",
]


class UploadsDatasetMetadataIsCompleteRestFilter(rest_framework.FilterSet):
    metadata_is_complete = rest_framework.BooleanFilter(
        label=_("Filter by metadata is complete (latest version)"),
        method="filter_by_metadata_is_complete",
    )

    subfolder__isnull = rest_framework.BooleanFilter(
        label=_("Filter by datasets on the top level of the folder"),
        field_name="subfolder",
        lookup_expr="isnull",
    )

    class Meta:
        model = UploadsDataset
        fields = "__all__"

    def filter_by_metadata_is_complete(self, queryset, name, value):
        queryset = queryset.annotate(
            latest_version_metadata_is_complete=Subquery(
                UploadsVersion.objects.filter(
                    dataset=OuterRef("pk"),
                )
                .order_by(
                    "-creation_date",
                )
                .values(
                    "metadata_is_complete",
                )[:1],
            ),
        )

        if value is True:
            return queryset.filter(
                latest_version_metadata_is_complete=True,
            )
        elif value is False:
            return queryset.filter(
                latest_version_metadata_is_complete=False,
            )

        return queryset
