import logging

from celery import shared_task
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from fdm.core.helpers import set_request_for_user
from fdm.exports.models import ExportJob, MetadataTemplateExportJob
from fdm.exports.services import (
    build_export_zip_file,
    build_metadata_template_export_zip_file,
    cleanup_expired_export_jobs,
    cleanup_expired_metadata_template_export_jobs,
    generate_download_token,
    get_download_url,
    get_export_lifetime_hours,
    get_metadata_template_export_download_url,
)

__all__ = [
    "build_export_zip",
    "build_metadata_template_export_zip",
    "cleanup_expired_exports",
]

logger = logging.getLogger(__name__)


@shared_task
def build_export_zip(export_job_id: str) -> None:
    try:
        export_job = ExportJob.objects.get(pk=export_job_id)
    except ExportJob.DoesNotExist:
        return

    if export_job.source_export_job_id:
        return

    set_request_for_user(export_job.created_by)

    try:
        export_job.status = ExportJob.Status.IN_PROGRESS
        export_job.error_message = None
        export_job.save(update_fields=["status", "error_message"])
        export_job.reused_export_jobs.filter(
            status__in=[
                ExportJob.Status.SCHEDULED,
                ExportJob.Status.IN_PROGRESS,
            ],
        ).update(status=ExportJob.Status.IN_PROGRESS, error_message=None)

        zip_file_name = build_export_zip_file(export_job)
        token = generate_download_token()
        expires_at = timezone.now() + timezone.timedelta(hours=get_export_lifetime_hours())

        export_job.zip_file.name = zip_file_name
        export_job.set_download_token(token)
        export_job.expires_at = expires_at
        export_job.finished_at = timezone.now()
        export_job.status = ExportJob.Status.FINISHED
        export_job.save(
            update_fields=[
                "zip_file",
                "download_token_hash",
                "expires_at",
                "finished_at",
                "status",
            ],
        )

        _send_export_ready_mail(export_job, token, expires_at)
        _finish_reused_export_jobs(export_job, zip_file_name)
    except (PermissionDenied, ValidationError) as e:
        _mark_export_job_failed(export_job, str(e))
        logger.error(f"Export job '{export_job.pk}' failed: {e}")
    except Exception as e:
        _mark_export_job_failed(export_job, str(e))
        logger.error(f"Export job '{export_job.pk}' failed: {e}")


@shared_task
def build_metadata_template_export_zip(export_job_id: str) -> None:
    try:
        export_job = MetadataTemplateExportJob.objects.get(pk=export_job_id)
    except MetadataTemplateExportJob.DoesNotExist:
        return

    set_request_for_user(export_job.created_by)

    try:
        export_job.status = MetadataTemplateExportJob.Status.IN_PROGRESS
        export_job.error_message = None
        export_job.save(update_fields=["status", "error_message"])

        zip_file_name = build_metadata_template_export_zip_file(export_job)
        token = generate_download_token()
        expires_at = timezone.now() + timezone.timedelta(hours=get_export_lifetime_hours())

        export_job.zip_file.name = zip_file_name
        export_job.set_download_token(token)
        export_job.expires_at = expires_at
        export_job.finished_at = timezone.now()
        export_job.status = MetadataTemplateExportJob.Status.FINISHED
        export_job.save(
            update_fields=[
                "zip_file",
                "download_token_hash",
                "expires_at",
                "finished_at",
                "status",
            ],
        )

        _send_metadata_template_export_ready_mail(export_job, token, expires_at)
    except (PermissionDenied, ValidationError) as e:
        _mark_metadata_template_export_job_failed(export_job, str(e))
        logger.error(f"Metadata template export job '{export_job.pk}' failed: {e}")
    except Exception as e:
        _mark_metadata_template_export_job_failed(export_job, str(e))
        logger.error(f"Metadata template export job '{export_job.pk}' failed: {e}")


@shared_task
def cleanup_expired_exports() -> None:
    deleted_count = cleanup_expired_export_jobs()
    metadata_template_deleted_count = cleanup_expired_metadata_template_export_jobs()
    logger.info(
        f"Cleaned up {deleted_count} expired export jobs and "
        f"{metadata_template_deleted_count} expired metadata template export jobs",
    )


def _finish_reused_export_jobs(source_export_job: ExportJob, zip_file_name: str) -> None:
    reused_export_jobs = source_export_job.reused_export_jobs.filter(
        status__in=[
            ExportJob.Status.SCHEDULED,
            ExportJob.Status.IN_PROGRESS,
        ],
    )

    for reused_export_job in reused_export_jobs:
        token = generate_download_token()
        expires_at = timezone.now() + timezone.timedelta(hours=get_export_lifetime_hours())
        reused_export_job.zip_file.name = zip_file_name
        reused_export_job.set_download_token(token)
        reused_export_job.expires_at = expires_at
        reused_export_job.finished_at = timezone.now()
        reused_export_job.status = ExportJob.Status.FINISHED
        reused_export_job.error_message = None
        reused_export_job.save(
            update_fields=[
                "zip_file",
                "download_token_hash",
                "expires_at",
                "finished_at",
                "status",
                "error_message",
            ],
        )
        _send_export_ready_mail(reused_export_job, token, expires_at)


def _mark_export_job_failed(export_job: ExportJob, error_message: str) -> None:
    export_job.status = ExportJob.Status.ERROR
    export_job.error_message = error_message
    export_job.save(update_fields=["status", "error_message"])
    export_job.reused_export_jobs.filter(
        status__in=[
            ExportJob.Status.SCHEDULED,
            ExportJob.Status.IN_PROGRESS,
        ],
    ).update(status=ExportJob.Status.ERROR, error_message=error_message)


def _mark_metadata_template_export_job_failed(export_job: MetadataTemplateExportJob, error_message: str) -> None:
    export_job.status = MetadataTemplateExportJob.Status.ERROR
    export_job.error_message = error_message
    export_job.save(update_fields=["status", "error_message"])


def _send_export_ready_mail(export_job: ExportJob, token: str, expires_at) -> None:
    if not export_job.created_by:
        return

    export_job.created_by.send_mail(
        subject=_("Your export is ready"),
        context={
            "download_url": get_download_url(export_job, token),
            "expires_at": expires_at,
            "export_type": export_job.export_type,
            "object_count": len(export_job.object_ids),
        },
        template_name="exports/emails/export_ready",
    )


def _send_metadata_template_export_ready_mail(
    export_job: MetadataTemplateExportJob,
    token: str,
    expires_at,
) -> None:
    if not export_job.created_by:
        return

    export_job.created_by.send_mail(
        subject=_("Your export is ready"),
        context={
            "download_url": get_metadata_template_export_download_url(export_job, token),
            "expires_at": expires_at,
            "export_type": _("Metadata template container"),
            "object_count": len(export_job.metadata_template_container_ids),
        },
        template_name="exports/emails/export_ready",
    )
