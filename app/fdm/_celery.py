from celery import Celery
from celery.schedules import crontab
from django.conf import settings

app = Celery("fdm")

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Load task modules from all registered Django app configs.
app.autodiscover_tasks(settings.INSTALLED_APPS)

# Celery beat schedule
app.conf.beat_schedule = {
    "dataset_remove_expired_drafts": {
        "task": "fdm.uploads.tasks.remove_expired_dataset_drafts",
        "schedule": crontab(hour=0, minute=0),  # Daily at midnight
    },
    "dataset_remove_expired_locks": {
        "task": "fdm.uploads.tasks.remove_expired_locks",
        "schedule": 60 * 1,  # 1 minute
    },
    "dataset_reconcile_metadata_template_processing": {
        "task": "fdm.uploads.tasks.reconcile_dataset_metadata_template_processing",
        "schedule": 60 * 1,  # 1 minute
    },
    "exports_cleanup_expired_exports": {
        "task": "fdm.exports.tasks.cleanup_expired_exports",
        "schedule": 60 * 60,  # 1 hour
    },
    "file_parser_reconcile_uploaded_file_processing": {
        "task": "fdm.file_parser.tasks.reconcile_uploaded_file_processing",
        "schedule": 60 * 1,  # 1 minute
    },
    "folder_remove_expired_locks": {
        "task": "fdm.folders.tasks.remove_expired_locks",
        "schedule": 60 * 1,  # 1 minute
    },
    "project_remove_expired_locks": {
        "task": "fdm.projects.tasks.remove_expired_locks",
        "schedule": 60 * 1,  # 1 minute
    },
    "metadata_template_remove_expired_locks": {
        "task": "fdm.metadata.tasks.remove_expired_locks",
        "schedule": 60 * 1,  # 1 minute
    },
    "uploads_version_check_metadata_completeness": {
        "task": "fdm.uploads.tasks.check_metadata_completeness",
        "schedule": 60 * 1,  # 1 minute
    },
    "uploads_version_file_move_files": {
        "task": "fdm.uploads.tasks.move_files",
        "schedule": 60 * 1,  # 1 minute
    },
    "check_private_dss_storages_mounting_status": {
        "task": "fdm.storages.tasks.check_private_dss_storages_mounting_status",
        "schedule": 60 * 5,  # 5 minutes
    },
    "rest_framework_tus_remove_expired_uploads": {
        "task": "fdm.rest_framework_tus.tasks.remove_expired_uploads",
        "schedule": 60 * 60,  # 1 hour
    },
}
