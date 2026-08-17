from django.contrib.admin import SimpleListFilter
from django.db.models import OuterRef, Subquery
from django.utils.translation import gettext_lazy as _

from fdm.uploads.models import UploadsVersion

__all__ = [
    "UploadsDatasetMetadataIsCompleteFilter",
    "UploadsDatasetHasUpgradeableMetadataTemplatesFilter",
    "UploadsVersionMetadataIsCompleteFilter",
]


class UploadsDatasetMetadataIsCompleteFilter(SimpleListFilter):
    title = "has complete metadata (latest version)"

    parameter_name = "metadata_is_complete"

    def lookups(self, request, model_admin):
        return (
            ("1", _("Yes")),
            ("0", _("No")),
        )

    def queryset(self, request, queryset):
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

        if self.value() == "1":
            return queryset.filter(
                latest_version_metadata_is_complete=True,
            )
        elif self.value() == "0":
            return queryset.filter(
                latest_version_metadata_is_complete=False,
            )

        return queryset


class UploadsDatasetHasUpgradeableMetadataTemplatesFilter(SimpleListFilter):
    title = "has upgradeable metadata templates (latest version)"

    parameter_name = "has_upgradeable_metadata_templates"

    def lookups(self, request, model_admin):
        return (
            ("1", _("Yes")),
            ("0", _("No")),
        )

    def queryset(self, request, queryset):
        if self.value() == "1":
            return queryset.filter(has_upgradeable_metadata_templates=True)
        elif self.value() == "0":
            return queryset.filter(has_upgradeable_metadata_templates=False)

        return queryset


class UploadsVersionMetadataIsCompleteFilter(SimpleListFilter):
    title = "has complete metadata"

    parameter_name = "metadata_is_complete"

    def lookups(self, request, model_admin):
        return (
            ("1", _("Yes")),
            ("0", _("No")),
        )

    def queryset(self, request, queryset):
        if self.value() == "1":
            return queryset.filter(metadata_is_complete=True)
        elif self.value() == "0":
            return queryset.filter(metadata_is_complete=False)

        return queryset
