from django.contrib import admin, messages
from django.db import connection
from django.http import HttpResponseRedirect
from django.utils.translation import gettext_lazy as _

from fdm.exports.models import ExportJob
from fdm.exports.services import delete_export_job

__all__ = [
    "ExportJobAdmin",
]


DELETABLE_EXPORT_JOB_STATUSES = [
    ExportJob.Status.ERROR,
    ExportJob.Status.FINISHED,
]


@admin.register(ExportJob)
class ExportJobAdmin(admin.ModelAdmin):
    list_display = [
        "pk",
        "export_type",
        "created_by",
        "status",
        "expires_at",
    ]

    list_filter = [
        "status",
        "export_type",
        "metadata_format",
        "include_uploads_version_file",
        "include_uploads_version_file_metadata",
        "created_by",
    ]

    fields = [
        "id",
        "export_type",
        "object_ids",
        "exported_uploads_version_ids",
        "source_export_job",
        "metadata_format",
        "include_uploads_version_file",
        "include_uploads_version_file_metadata",
        "status",
        "zip_file",
        "download_token_hash",
        "download_base_url",
        "expires_at",
        "finished_at",
        "error_message",
        "creation_date",
        "last_modification_date",
        "created_by",
        "last_modified_by",
    ]

    readonly_fields = fields

    search_fields = [
        "pk",
        "export_type",
        "created_by__email",
        "created_by__username",
    ]

    actions = [
        "cleanup_selected_export_jobs",
    ]

    @admin.action(description=_("Delete selected export jobs and ZIP files"))
    def cleanup_selected_export_jobs(self, request, queryset):
        backlink = request.get_full_path()
        eligible_jobs = queryset.filter(status__in=DELETABLE_EXPORT_JOB_STATUSES)
        skipped_count = queryset.exclude(status__in=DELETABLE_EXPORT_JOB_STATUSES).count()
        deleted_count = 0

        for export_job in eligible_jobs:
            delete_export_job(export_job)
            deleted_count += 1

        if deleted_count:
            self.message_user(
                request,
                _("Successfully deleted {count} export job(s) and ZIP file(s).").format(count=deleted_count),
                level=messages.SUCCESS,
            )

        if skipped_count:
            self.message_user(
                request,
                _("Skipped {count} scheduled or in-progress export job(s).").format(count=skipped_count),
                level=messages.WARNING,
            )

        return HttpResponseRedirect(backlink)

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def get_search_results(self, request, queryset, search_term):
        base_queryset = queryset
        queryset, may_have_duplicates = super().get_search_results(request, queryset, search_term)

        if search_term and connection.vendor == "postgresql":
            queryset |= base_queryset.filter(object_ids__contains=[search_term])

        return queryset, may_have_duplicates

    def has_module_permission(self, request):
        return self._is_active_staff(request)

    def has_view_permission(self, request, obj=None):
        return self._is_active_staff(request)

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        if not self._is_active_staff(request):
            return False

        if obj is None:
            return True

        return obj.status in DELETABLE_EXPORT_JOB_STATUSES

    def delete_model(self, request, obj):
        delete_export_job(obj)

    def delete_queryset(self, request, queryset):
        for export_job in queryset:
            delete_export_job(export_job)

    @staticmethod
    def _is_active_staff(request):
        return bool(request.user and request.user.is_active and request.user.is_staff)
