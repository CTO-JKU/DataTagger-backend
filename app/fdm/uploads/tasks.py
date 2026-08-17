import logging
import uuid

from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from fdm._celery import app
from fdm.core.helpers import set_request_for_user
from fdm.folders.models import Folder
from fdm.uploads.models import UploadsDataset, UploadsVersion, UploadsVersionFile

__all__ = [
    "check_metadata_completeness",
    "dispatch_metadata_template_upgrade_availability",
    "dispatch_dataset_metadata_template_upgrade",
    "schedule_dataset_metadata_template_upgrades",
    "remove_expired_locks",
    "remove_expired_dataset_drafts",
    "refresh_metadata_template_upgrade_availability",
    "process_dataset_metadata_template_upgrade",
    "reconcile_dataset_metadata_template_processing",
    "schedule_metadata_template_upgrade_availability",
    "move_files",
    "recompute_folder_storage_size",
    "recompute_user_storage_size",
]

logger = logging.getLogger(__name__)

User = get_user_model()


def dispatch_metadata_template_upgrade_availability(dataset_id: str) -> None:
    try:
        refresh_metadata_template_upgrade_availability.apply_async((dataset_id,))
        logger.info("Metadata Template Upgrade Availability dispatched dataset_id=%s", dataset_id)
    except Exception:
        logger.exception(
            "Could not dispatch Metadata Template Upgrade Availability for dataset '%s'",
            dataset_id,
        )


def schedule_metadata_template_upgrade_availability(dataset_ids) -> None:
    dataset_ids = tuple(dataset_ids)
    UploadsDataset.objects.filter(pk__in=dataset_ids).update(
        metadata_upgrade_availability_status=UploadsDataset.Status.SCHEDULED,
    )

    def dispatch_after_commit() -> None:
        for dataset_id in dataset_ids:
            dispatch_metadata_template_upgrade_availability(str(dataset_id))

    transaction.on_commit(dispatch_after_commit)


def dispatch_dataset_metadata_template_upgrade(dataset_id: str) -> None:
    try:
        process_dataset_metadata_template_upgrade.apply_async((dataset_id,))
        logger.info("Dataset Metadata Template Upgrade dispatched dataset_id=%s", dataset_id)
    except Exception:
        logger.exception("Could not dispatch Dataset Metadata Template Upgrade for dataset '%s'", dataset_id)


def schedule_dataset_metadata_template_upgrades(dataset_ids) -> None:
    dataset_ids = tuple(dataset_ids)
    (
        UploadsDataset.objects.filter(pk__in=dataset_ids)
        .exclude(metadata_upgrade_status=UploadsDataset.Status.IN_PROGRESS)
        .update(metadata_upgrade_status=UploadsDataset.Status.SCHEDULED)
    )

    def dispatch_after_commit() -> None:
        for dataset_id in dataset_ids:
            dispatch_dataset_metadata_template_upgrade(str(dataset_id))

    transaction.on_commit(dispatch_after_commit)


def schedule_recompute_folder_storage_size(folder_id: str) -> None:
    debounce_seconds = settings.CELERY_RECOMPUTE_STORAGE_SIZE_DEBOUNCE_SECONDS

    if cache.add(f"recompute_folder_storage_size:{folder_id}", True, timeout=debounce_seconds):
        recompute_folder_storage_size.apply_async(
            (folder_id,),
            countdown=debounce_seconds,
        )


def schedule_recompute_user_storage_size(user_id: str) -> None:
    debounce_seconds = settings.CELERY_RECOMPUTE_STORAGE_SIZE_DEBOUNCE_SECONDS

    if cache.add(f"recompute_user_storage_size:{user_id}", True, timeout=debounce_seconds):
        recompute_user_storage_size.apply_async(
            (user_id,),
            countdown=debounce_seconds,
        )


@shared_task
def check_metadata_completeness():
    uploads_versions = UploadsVersion.objects.filter(
        status=UploadsVersion.Status.SCHEDULED,
    )

    for uploads_version in uploads_versions:
        # set request user to user that last updated the version so last_modified_by is automatically set correctly
        set_request_for_user(uploads_version.last_modified_by)

        if uploads_version.locked:
            logger.info(
                f"Task 'check_metadata_completeness' for uploads_version '{uploads_version}' skipped because of its lock status",
            )
            continue

        uploads_version.dataset.lock()

        try:
            uploads_version.status = UploadsVersion.Status.IN_PROGRESS
            uploads_version.save()

            uploads_version.metadata_is_complete = uploads_version.check_metadata_completeness()
            uploads_version.save()

            uploads_version.status = UploadsVersion.Status.FINISHED
            uploads_version.save()

            logger.info(f"Task 'check_metadata_completeness' for uploads version '{uploads_version}' succeeded")
        except PermissionDenied:
            logger.error(
                f"Task 'check_metadata_completeness' for uploads version '{uploads_version}' failed because of a "
                "permission error",
            )
        except Exception as e:
            uploads_version.status = UploadsVersion.Status.ERROR
            uploads_version.save()

            logger.error(f"Task 'check_metadata_completeness' for uploads version '{uploads_version}' failed: {e}")

        uploads_version.dataset.unlock()


@shared_task
def recompute_folder_storage_size(folder_id: str) -> None:
    try:
        folder = Folder.objects.get(pk=folder_id)
    except Folder.DoesNotExist:
        return

    # set request user to user that last updated the version so last_modified_by is automatically set correctly
    set_request_for_user(folder.last_modified_by)

    try:
        folder.update_storage_size()
    except Exception as e:
        logger.error(f"Task 'recompute_folder_storage_size' for folder '{folder}' failed: {e}")


@shared_task
def recompute_user_storage_size(user_id: str) -> None:
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return

    set_request_for_user(user)

    try:
        user.update_storage_size()
    except Exception as e:
        logger.error(f"Task 'recompute_user_storage_size' for user '{user}' failed: {e}")


@shared_task
def remove_expired_locks():
    datasets = UploadsDataset.objects.filter(
        locked=True,
    )

    for dataset in datasets:
        try:
            dataset.remove_expired_lock()
        except Exception as e:
            logger.error(f"Task 'remove_expired_lock' for dataset '{dataset}' failed: {e}")


@shared_task
def remove_expired_dataset_drafts():
    datasets = UploadsDataset.objects.filter(
        publication_date__isnull=True,
        expiry_date__lt=timezone.now(),
    )

    for dataset in datasets:
        try:
            # set request user to user that created the dataset, so we can actually delete it
            set_request_for_user(dataset.created_by)
            dataset.unlock()
            dataset.delete()
        except Exception as e:
            logger.error(f"Task 'remove_expired_dataset_drafts' for dataset '{dataset}' failed: {e}")


@shared_task(
    bind=True,
    max_retries=settings.METADATA_TEMPLATE_PROCESSING_AVAILABILITY_MAX_RETRIES,
)
def refresh_metadata_template_upgrade_availability(self, dataset_id: str) -> None:
    started_at = timezone.now()
    claim = _claim_metadata_template_processing(
        dataset_id,
        status_field="metadata_upgrade_availability_status",
    )
    if not claim:
        return

    dataset, lease_token = claim

    set_request_for_user(dataset.last_modified_by)
    try:
        has_upgradeable_metadata_templates = dataset.is_latest_version_upgradeable()
        updated = UploadsDataset.objects.filter(
            pk=dataset.pk,
            metadata_template_processing_lease_token=lease_token,
            metadata_upgrade_availability_status=UploadsDataset.Status.IN_PROGRESS,
        ).update(
            has_upgradeable_metadata_templates=has_upgradeable_metadata_templates,
            metadata_upgrade_availability_status=UploadsDataset.Status.FINISHED,
            metadata_template_processing_lease_token=None,
            metadata_template_processing_lease_expires_at=None,
            metadata_template_processing_heartbeat_at=None,
        )
        if not updated:
            _clear_metadata_template_processing_lease(dataset.pk, lease_token)
            return
        logger.info(
            "Metadata Template Upgrade Availability finished dataset_id=%s duration_seconds=%.3f",
            dataset.pk,
            (timezone.now() - started_at).total_seconds(),
        )
    except Exception as exc:
        retrying = self.request.retries < self.max_retries
        _release_metadata_template_processing(
            dataset.pk,
            lease_token,
            status_field="metadata_upgrade_availability_status",
            status=UploadsDataset.Status.SCHEDULED if retrying else UploadsDataset.Status.ERROR,
        )
        if retrying:
            countdown = settings.METADATA_TEMPLATE_PROCESSING_RETRY_BACKOFF_SECONDS * (2**self.request.retries)
            raise self.retry(exc=exc, countdown=countdown) from exc
        raise


@shared_task
def process_dataset_metadata_template_upgrade(dataset_id: str) -> None:
    started_at = timezone.now()
    claim = _claim_metadata_template_processing(
        dataset_id,
        status_field="metadata_upgrade_status",
    )
    if not claim:
        return

    dataset, lease_token = claim
    set_request_for_user(dataset.last_modified_by)
    try:
        if not dataset.is_latest_version_upgradeable():
            success, error = True, None
        else:
            success, error = dataset.upgrade_latest_version_metadata()

        if success:
            has_upgradeable_metadata_templates = dataset.is_latest_version_upgradeable()
            updated = UploadsDataset.objects.filter(
                pk=dataset.pk,
                metadata_template_processing_lease_token=lease_token,
                metadata_upgrade_status=UploadsDataset.Status.IN_PROGRESS,
            ).update(
                has_upgradeable_metadata_templates=has_upgradeable_metadata_templates,
                metadata_upgrade_status=UploadsDataset.Status.FINISHED,
                metadata_template_processing_lease_token=None,
                metadata_template_processing_lease_expires_at=None,
                metadata_template_processing_heartbeat_at=None,
            )
            if not updated:
                _clear_metadata_template_processing_lease(dataset.pk, lease_token)
                return
            logger.info(
                "Dataset Metadata Template Upgrade finished dataset_id=%s duration_seconds=%.3f",
                dataset.pk,
                (timezone.now() - started_at).total_seconds(),
            )
            return

        logger.error("Dataset Metadata Template Upgrade failed dataset_id=%s error=%s", dataset.pk, error)
    except Exception:
        logger.exception("Dataset Metadata Template Upgrade failed dataset_id=%s", dataset.pk)

    _release_metadata_template_processing(
        dataset.pk,
        lease_token,
        status_field="metadata_upgrade_status",
        status=UploadsDataset.Status.ERROR,
    )


@shared_task
def reconcile_dataset_metadata_template_processing() -> None:
    now = timezone.now()
    legacy_cutoff = now - timezone.timedelta(
        seconds=settings.METADATA_TEMPLATE_PROCESSING_LEGACY_GRACE_SECONDS,
    )
    batch_size = settings.METADATA_TEMPLATE_PROCESSING_RECONCILIATION_BATCH_SIZE
    recovered_ids = []
    reclaimable_lease = Q(metadata_template_processing_lease_expires_at__lte=now) | Q(
        metadata_template_processing_lease_token__isnull=True,
        last_modification_date__lte=legacy_cutoff,
    )
    expired_datasets = list(
        UploadsDataset.objects.filter(
            reclaimable_lease,
            Q(metadata_upgrade_availability_status=UploadsDataset.Status.IN_PROGRESS)
            | Q(metadata_upgrade_status=UploadsDataset.Status.IN_PROGRESS),
            locked=False,
        )[:batch_size],
    )
    for dataset in expired_datasets:
        update_fields = {
            "metadata_template_processing_lease_token": None,
            "metadata_template_processing_lease_expires_at": None,
            "metadata_template_processing_heartbeat_at": None,
        }
        if dataset.metadata_upgrade_availability_status == UploadsDataset.Status.IN_PROGRESS:
            update_fields["metadata_upgrade_availability_status"] = UploadsDataset.Status.SCHEDULED
        if dataset.metadata_upgrade_status == UploadsDataset.Status.IN_PROGRESS:
            update_fields["metadata_upgrade_status"] = UploadsDataset.Status.SCHEDULED
        updated = UploadsDataset.objects.filter(
            reclaimable_lease,
            pk=dataset.pk,
        ).update(**update_fields)
        if not updated:
            continue
        recovered_ids.append(dataset.pk)
        if "metadata_upgrade_availability_status" in update_fields:
            dispatch_metadata_template_upgrade_availability(str(dataset.pk))
        if "metadata_upgrade_status" in update_fields:
            dispatch_dataset_metadata_template_upgrade(str(dataset.pk))

    remaining = batch_size - len(recovered_ids)
    if remaining <= 0:
        return

    claimable_lease = Q(metadata_template_processing_lease_token__isnull=True) | Q(
        metadata_template_processing_lease_expires_at__lte=now,
    )

    availability_ids = list(
        UploadsDataset.objects.filter(
            claimable_lease,
            locked=False,
            metadata_upgrade_availability_status=UploadsDataset.Status.SCHEDULED,
        )
        .exclude(pk__in=recovered_ids)
        .values_list("pk", flat=True)[:remaining],
    )
    for dataset_id in availability_ids:
        dispatch_metadata_template_upgrade_availability(str(dataset_id))

    remaining -= len(availability_ids)
    if remaining <= 0:
        return

    upgrade_ids = list(
        UploadsDataset.objects.filter(
            claimable_lease,
            locked=False,
            metadata_upgrade_status=UploadsDataset.Status.SCHEDULED,
        )
        .exclude(pk__in=recovered_ids)
        .values_list("pk", flat=True)[:remaining],
    )
    for dataset_id in upgrade_ids:
        dispatch_dataset_metadata_template_upgrade(str(dataset_id))


def _claim_metadata_template_processing(
    dataset_id: str,
    *,
    status_field: str,
) -> tuple[UploadsDataset, uuid.UUID] | None:
    with transaction.atomic():
        try:
            dataset = UploadsDataset.objects.select_for_update().get(pk=dataset_id)
        except UploadsDataset.DoesNotExist:
            return None

        if getattr(dataset, status_field) != UploadsDataset.Status.SCHEDULED:
            return None

        if dataset.locked:
            return None

        now = timezone.now()
        if (
            dataset.metadata_template_processing_lease_token
            and dataset.metadata_template_processing_lease_expires_at
            and dataset.metadata_template_processing_lease_expires_at > now
        ):
            return None

        lease_token = uuid.uuid4()
        setattr(dataset, status_field, UploadsDataset.Status.IN_PROGRESS)
        dataset.metadata_template_processing_lease_token = lease_token
        dataset.metadata_template_processing_lease_expires_at = now + timezone.timedelta(
            seconds=settings.METADATA_TEMPLATE_PROCESSING_LEASE_SECONDS,
        )
        dataset.metadata_template_processing_heartbeat_at = now
        dataset.save(
            update_fields=[
                status_field,
                "metadata_template_processing_lease_token",
                "metadata_template_processing_lease_expires_at",
                "metadata_template_processing_heartbeat_at",
            ],
        )
        return dataset, lease_token


def _release_metadata_template_processing(
    dataset_id: str,
    lease_token: uuid.UUID,
    *,
    status_field: str,
    status: str,
) -> None:
    updated = UploadsDataset.objects.filter(
        pk=dataset_id,
        metadata_template_processing_lease_token=lease_token,
        **{status_field: UploadsDataset.Status.IN_PROGRESS},
    ).update(
        **{
            status_field: status,
            "metadata_template_processing_lease_token": None,
            "metadata_template_processing_lease_expires_at": None,
            "metadata_template_processing_heartbeat_at": None,
        },
    )
    if not updated:
        _clear_metadata_template_processing_lease(dataset_id, lease_token)


def _clear_metadata_template_processing_lease(dataset_id: str, lease_token: uuid.UUID) -> None:
    UploadsDataset.objects.filter(
        pk=dataset_id,
        metadata_template_processing_lease_token=lease_token,
    ).update(
        metadata_template_processing_lease_token=None,
        metadata_template_processing_lease_expires_at=None,
        metadata_template_processing_heartbeat_at=None,
    )


@shared_task
def move_files():
    # only move files after all the parsing is done
    version_files = UploadsVersionFile.objects.filter(
        storage_relocating=UploadsVersionFile.Status.SCHEDULED,
        status=UploadsVersionFile.Status.FINISHED,
    )

    for version_file in version_files:
        set_request_for_user(version_file.last_modified_by)
        version_file.move_file()
