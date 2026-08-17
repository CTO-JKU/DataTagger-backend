import logging

from django.contrib import admin, messages
from django.contrib.contenttypes.models import ContentType
from django.db.models import IntegerField, OuterRef, Subquery, Value
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, Coalesce
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from fdm.core.helpers import format_storage_size
from fdm.metadata.filters import IsPublishedFilter
from fdm.metadata.models import Metadata
from fdm.uploads.filters import (
    UploadsDatasetHasUpgradeableMetadataTemplatesFilter,
    UploadsDatasetMetadataIsCompleteFilter,
    UploadsVersionMetadataIsCompleteFilter,
)
from fdm.uploads.models import UploadsDataset, UploadsVersion, UploadsVersionFile

logger = logging.getLogger(__name__)

__all__ = [
    "UploadsVersionInlineAdmin",
    "UploadsDatasetAdmin",
    "UploadsVersionAdmin",
    "UploadsVersionFileAdmin",
]


class BulkDeleteMixin:
    """Mixin that replaces Django's default delete_selected with a bulk delete action
    that calls each object's .delete() method individually to ensure proper cascading."""

    @admin.action(description=_("Delete selected items"))
    def bulk_delete_selected(self, request, queryset):
        backlink = request.get_full_path()

        if not request.user.can_hard_delete_datasets:
            self.message_user(
                request,
                _("You do not have permission to bulk delete."),
                level=messages.ERROR,
            )
            return HttpResponseRedirect(backlink)

        if "apply" not in request.POST:
            context = self.admin_site.each_context(request)
            context["opts"] = self.model._meta
            context["selected_datasets"] = queryset.all()
            context["backlink"] = backlink

            return render(
                request,
                "admin/uploads/delete_confirmation.html",
                context=context,
            )

        deleted_count = 0
        failed_count = 0

        for obj in queryset.all():
            try:
                obj.delete()
                deleted_count += 1
            except Exception:
                failed_count += 1
                logger.exception("Failed to delete %s (pk=%s)", obj.__class__.__name__, obj.pk)

        if deleted_count:
            self.message_user(
                request,
                _("Successfully deleted {count} item(s).").format(count=deleted_count),
                level=messages.SUCCESS,
            )

        if failed_count:
            self.message_user(
                request,
                _("Failed to delete {count} item(s).").format(count=failed_count),
                level=messages.ERROR,
            )

        return HttpResponseRedirect(backlink)

    actions = ["bulk_delete_selected"]

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        if not request.user.can_hard_delete_datasets:
            actions.pop("bulk_delete_selected", None)
        return actions


class UploadsVersionInlineAdmin(admin.TabularInline):
    model = UploadsVersion

    can_delete = False

    show_change_link = False

    fields = [
        "pk",
        "name",
        "version_file",
        "publication_date",
        "is_latest_version",
        "metadata_is_complete",
        "status",
    ]

    readonly_fields = [
        "pk",
        "name",
        "version_file",
        "publication_date",
        "is_latest_version",
        "metadata_is_complete",
        "status",
    ]

    ordering = [
        "-creation_date",
    ]

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(UploadsDataset)
class UploadsDatasetAdmin(BulkDeleteMixin, admin.ModelAdmin):
    delete_confirmation_template = "admin/uploads/delete_confirmation.html"
    list_display = [
        "display_name",
        "name",
        "folder",
        "is_published",
        "uploads_versions_count",
        "has_upgradeable_metadata_templates",
    ]

    list_filter = [
        "folder__project",
        IsPublishedFilter,
        UploadsDatasetMetadataIsCompleteFilter,
        UploadsDatasetHasUpgradeableMetadataTemplatesFilter,
    ]

    search_fields = [
        "pk",
        "name",
        "display_name",
        "folder__pk",
        "folder__name",
        "folder__project__pk",
        "folder__project__name",
    ]

    inlines = [
        UploadsVersionInlineAdmin,
    ]

    readonly_fields = [
        "display_name",
        "uploads_versions_count",
        "has_upgradeable_metadata_templates",
    ]

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.can_hard_delete_datasets or obj and obj.publication_date is None

    def get_deleted_objects(self, objs, request):
        deleted_objects, model_count, perms_needed, protected = super().get_deleted_objects(objs, request)

        if request.user.can_hard_delete_datasets:
            perms_needed = set()
            protected = []

        return deleted_objects, model_count, perms_needed, protected

    def response_delete(self, request, obj_display, obj_id):
        response = super().response_delete(request, obj_display, obj_id)
        if response.status_code in (301, 302, 303, 307, 308):
            return HttpResponseRedirect(reverse("admin:uploads_uploadsdataset_changelist"))
        return response


@admin.register(UploadsVersion)
class UploadsVersionAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "dataset",
        "version_file",
        "is_published",
        "is_latest_version",
    ]

    list_filter = [
        "dataset__folder__project",
        IsPublishedFilter,
        UploadsVersionMetadataIsCompleteFilter,
    ]

    search_fields = [
        "pk",
        "name",
        "dataset__pk",
        "dataset__name",
        "dataset__display_name",
        "dataset__folder__pk",
        "dataset__folder__name",
        "dataset__folder__project__pk",
        "dataset__folder__project__name",
    ]

    readonly_fields = [
        "metadata_is_complete",
        "status",
    ]

    def get_readonly_fields(self, request, obj=None):
        if obj:
            if not obj.dataset:
                return self.readonly_fields

            if not obj.is_published():
                return self.readonly_fields

            if obj.pk == obj.dataset.latest_version.pk:
                return self.readonly_fields

            return [
                "dataset",
                "version_file",
                "publication_date",
            ] + self.readonly_fields

        return self.readonly_fields

    actions = []

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(UploadsVersionFile)
class UploadsVersionFileAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "file_size_display",
        "status",
        "uploaded_using_tus",
        "is_published",
        "is_referenced",
    ]

    list_filter = [
        "status",
        "uploaded_using_tus",
        "is_referenced",
        IsPublishedFilter,
    ]

    search_fields = [
        "pk",
        "uploaded_file",
        "uploads_versions__pk",
        "uploads_versions__name",
        "uploads_versions__dataset__pk",
        "uploads_versions__dataset__name",
        "uploads_versions__dataset__display_name",
        "uploads_versions__dataset__folder__pk",
        "uploads_versions__dataset__folder__name",
        "uploads_versions__dataset__folder__project__pk",
        "uploads_versions__dataset__folder__project__name",
    ]

    fields = [
        "name",
        "file_size_display",
        "absolute_path",
        "status",
        "uploaded_using_tus",
        "is_published",
        "is_referenced",
    ]

    inlines = [
        UploadsVersionInlineAdmin,
    ]

    exclude = [
        "uploaded_file",
    ]

    actions = []

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        content_type = ContentType.objects.get_for_model(UploadsVersionFile)
        file_size_subquery = Metadata.objects.filter(
            assigned_to_content_type=content_type,
            assigned_to_object_id=OuterRef("pk"),
            custom_key__iexact="FILE_SIZE",
        ).values("value")[:1]

        return queryset.annotate(
            file_size_order=Coalesce(
                Cast(KeyTextTransform("value", Subquery(file_size_subquery)), IntegerField()),
                Value(0),
            ),
        )

    @admin.display(ordering="file_size_order", description="File size")
    def file_size_display(self, obj):
        return format_storage_size(obj.file_size)
