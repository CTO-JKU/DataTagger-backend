import json
import logging
import os

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_userforeignkey.request import get_current_user

from fdm.file_parser.models import FileInformationParser
from fdm.rest_framework_tus.models import Upload
from fdm.rest_framework_tus.signals import finished
from fdm.storages.models import DynamicStorage
from fdm.uploads.helpers import create_uploads_version_with_new_file_for_dataset
from fdm.uploads.models import UploadsDataset, UploadsVersion, UploadsVersionFile
from fdm.uploads.tasks import schedule_metadata_template_upgrade_availability

logger = logging.getLogger(__name__)


@receiver(finished, sender=Upload)
def save_finished_tus_upload_as_uploads_version_file(sender, instance, **kwargs):
    logger.debug(
        f"Trying to save a tus-upload {instance.pk} as UploadsVersionFile for a UploadsDataset {instance.dataset}",
    )

    if not instance.dataset:
        return

    upload_metadata = instance.upload_metadata or {}

    try:
        if isinstance(upload_metadata, str):
            upload_metadata = json.loads(upload_metadata)
        elif not isinstance(upload_metadata, dict):
            upload_metadata = {}

        create_uploads_version_with_new_file_for_dataset(
            dataset=instance.dataset,
            name=_("File upload of {file}").format(
                file=upload_metadata.get("filename", instance.uploaded_file.name),
            ),
            uploaded_file=instance.uploaded_file,
            uploaded_using_tus=True,
            original_file_name=upload_metadata.get("filename", None),
            original_file_path=upload_metadata.get("filepath", None),
        )

        if instance.dataset.folder:
            instance.dataset.publish()
    except Exception:
        logger.exception(
            "Failed to complete file upload for upload '%s' and dataset '%s'",
            instance.pk,
            instance.dataset.pk,
        )

        try:
            instance.dataset.status = UploadsDataset.Status.ERROR
            instance.dataset.save()
        except Exception:
            logger.exception("Could not mark dataset '%s' as errored", instance.dataset.pk)
    finally:
        try:
            instance.dataset.unlock()
        except Exception:
            logger.exception("Could not unlock dataset '%s' after upload '%s'", instance.dataset.pk, instance.pk)

        # Always delete finished uploads to ensure deterministic tmp-file cleanup.
        try:
            instance.delete()
        except Exception:
            logger.exception("Could not delete finished upload '%s'", instance.pk)


@receiver(pre_save, sender=UploadsDataset)
def set_expiry_date_for_dataset(sender, instance, **kwargs):
    if instance.publication_date is not None:
        instance.expiry_date = None
    elif instance.creation_date is not None:
        instance.expiry_date = instance.creation_date + timezone.timedelta(days=settings.DRAFT_FILES_MAX_LIFETIME)
    else:
        instance.expiry_date = timezone.now() + timezone.timedelta(days=settings.DRAFT_FILES_MAX_LIFETIME)


@receiver(pre_save, sender=UploadsDataset)
def pre_save_uploads_dataset_metadata_template(sender, instance, *args, **kwargs):
    # If the instance is created, then we don't need to check anything
    if instance.pk is None:
        return

    # Get the old value for this instance field if it already exists, else abort
    try:
        old_instance = UploadsDataset.objects.get(pk=instance.pk)
    except UploadsDataset.DoesNotExist:
        return

    # Check if the value has been changed for this instance field
    if old_instance.metadata_template == instance.metadata_template:
        return

    instance.metadata_upgrade_availability_status = UploadsDataset.Status.SCHEDULED
    instance._schedule_metadata_template_upgrade_availability = True


@receiver(post_save, sender=UploadsDataset)
def post_save_uploads_dataset_metadata_template(sender, instance, *args, **kwargs):
    if not getattr(instance, "_schedule_metadata_template_upgrade_availability", False):
        return

    del instance._schedule_metadata_template_upgrade_availability
    schedule_metadata_template_upgrade_availability([instance.pk])


@receiver(pre_delete, sender=UploadsDataset)
def prevent_deleting_published_or_someone_elses_uploads_datasets(sender, instance, **kwargs):
    current_user = get_current_user()

    if getattr(current_user, "can_hard_delete_datasets", False):
        return

    if instance.locked and not instance.is_locked_by_myself():
        raise PermissionDenied(_("You must not delete an uploads dataset which has been locked by another user."))

    if instance.publication_date and (
        not instance.deletable_until_date
        or instance.deletable_until_date
        and timezone.now() > instance.deletable_until_date
    ):
        raise PermissionDenied("You must not delete an uploads dataset outside the deletable window.")

    if instance.created_by != current_user:
        raise PermissionDenied(_("You must not delete an uploads dataset which has been created by another user."))


@receiver(pre_delete, sender=UploadsVersion)
def prevent_deleting_published_or_someone_elses_uploads_versions(sender, instance, **kwargs):
    current_user = get_current_user()

    if getattr(current_user, "can_hard_delete_datasets", False):
        return

    dataset = instance.dataset

    if dataset.locked and not dataset.is_locked_by_myself():
        raise PermissionDenied(_("You must not delete an uploads version which has been locked by another user."))

    if dataset.publication_date and (
        not dataset.deletable_until_date
        or dataset.deletable_until_date
        and timezone.now() > dataset.deletable_until_date
    ):
        raise PermissionDenied("You must not delete an uploads version outside the deletable window.")

    if instance.created_by != current_user:
        raise PermissionDenied(_("You must not delete an uploads version which has been created by another user."))


def change_file_metadata(version_file: UploadsVersionFile):
    parser = FileInformationParser(version_file)
    return parser.parse(set_metadata=True)


def move_storage_file(storage: DynamicStorage, version_file: UploadsVersionFile):
    """
    Move a file to its new storage location based on storage type
    """
    storage_class = storage.storage_backend.__class__

    if not storage_class:
        logger.error(f"Could not get storage backend for storage type {storage.storage_type}")
        return False, version_file.uploaded_file.path

    storage_instance = storage.storage_backend
    if not storage_instance:
        logger.error(f"Could not instantiate storage for type {storage.storage_type}")
        return False, version_file.uploaded_file.path

    file_moved, new_file_path = storage_instance.move_file(version_file)
    if file_moved:
        version_file.uploaded_file.name = new_file_path
        version_file.storage_relocating = UploadsVersionFile.Status.FINISHED
        version_file.save()

        change_file_metadata(version_file)
        return True, new_file_path

    return False, version_file.uploaded_file.path


def get_uploads_version_dict_for_diff_comparison(uploads_version: UploadsVersion) -> dict[str, any]:
    return {
        "name": uploads_version.name,
        "dataset": uploads_version.dataset.pk if uploads_version.dataset else None,
        "version_file": uploads_version.version_file.pk if uploads_version.version_file else None,
        "publication_date": uploads_version.publication_date,
        "status": uploads_version.status,
    }


def is_allowed_to_apply_changes_to_already_published_uploads_version(
    uploads_version_1: UploadsVersion,
    uploads_version_2: UploadsVersion,
) -> bool:
    allowed_fields = [
        "name",
        "status",
    ]

    uploads_version_1_fields = get_uploads_version_dict_for_diff_comparison(uploads_version_1)
    uploads_version_2_fields = get_uploads_version_dict_for_diff_comparison(uploads_version_2)

    diff = dict(uploads_version_1_fields.items() ^ uploads_version_2_fields.items())

    for field in allowed_fields:
        diff.pop(field, None)

    if len(diff):
        return False

    return True


@receiver(pre_save, sender=UploadsVersion)
def auto_create_dataset_for_uploads_version(sender, instance, **kwargs):
    # Every uploads version must have a dataset attached to it. If it is missing one will be created automatically.
    if not instance.dataset:
        instance.dataset = UploadsDataset.objects.create()


@receiver(pre_save, sender=UploadsVersion)
def prevent_editing_already_published_versions(sender, instance, **kwargs):
    # If the uploads version is created, then we don't need to check anything
    if instance.pk is None:
        return

    # Get the old value for this uploads version if it already exists, else abort
    try:
        uploads_version = UploadsVersion.objects.get(pk=instance.pk)
    except UploadsVersion.DoesNotExist:
        return

    if not uploads_version.dataset:
        return

    if not uploads_version.is_published():
        return

    # It's ok to edit the latest published version
    if instance.pk == uploads_version.dataset.latest_version.pk:
        return

    # It's always okay to edit certain model fields
    if is_allowed_to_apply_changes_to_already_published_uploads_version(instance, uploads_version):
        return

    raise PermissionDenied(_("You must not edit an uploads version that has already been published."))


@receiver(post_save, sender=UploadsVersion)
def reset_deletable_until_date_on_foreign_version(sender, instance, **kwargs):
    dataset = instance.dataset
    current_user = get_current_user()

    if not dataset:
        return

    if not dataset.deletable_until_date:
        return

    if not current_user or dataset.created_by == current_user:
        return

    UploadsDataset.objects.filter(
        pk=dataset.pk,
    ).update(
        deletable_until_date=None,
    )


@receiver(post_save, sender=UploadsVersion)
def post_save_uploads_version_update_uploads_dataset_versions_count(sender, instance, *args, **kwargs):
    if not getattr(instance.dataset, "_is_being_deleted", False):
        instance.dataset.update_uploads_versions_count()


@receiver(post_delete, sender=UploadsVersion)
def post_delete_uploads_version_update_uploads_dataset_versions_count(sender, instance, *args, **kwargs):
    try:
        if instance.dataset and not getattr(instance.dataset, "_is_being_deleted", False):
            instance.dataset.update_uploads_versions_count()
    except UploadsDataset.DoesNotExist:
        pass


@receiver(pre_delete, sender=UploadsVersionFile)
def prevent_deleting_published_or_someone_elses_uploads_version_files(sender, instance, **kwargs):
    current_user = get_current_user()

    if getattr(current_user, "can_hard_delete_datasets", False):
        return

    if not instance.uploads_versions.exists():
        return

    dataset = instance.uploads_versions.first().dataset

    if dataset.locked and not dataset.is_locked_by_myself():
        raise PermissionDenied(_("You must not delete an uploads version file which has been locked by another user."))

    if dataset.publication_date and (
        not dataset.deletable_until_date
        or dataset.deletable_until_date
        and timezone.now() > dataset.deletable_until_date
    ):
        raise PermissionDenied("You must not delete an uploads version file outside the deletable window.")

    if instance.created_by != current_user:
        raise PermissionDenied(_("You must not delete an uploads version file which has been created by another user."))


@receiver(post_delete, sender=UploadsVersionFile)
def delete_files_after_uploads_version_file_deletion(sender, instance, *args, **kwargs):
    if not instance.uploaded_file:
        return

    # Capture the path now — by the time on_commit fires, `instance` may be gone
    # or its FieldFile may raise. Defer the filesystem removal until the enclosing
    # transaction commits so a rolled-back DB cascade cannot leave a deleted file
    # on disk with a surviving dataset/version row.
    path = instance.uploaded_file.path

    def _remove_file():
        if os.path.exists(path):
            os.remove(path)

    transaction.on_commit(_remove_file)
