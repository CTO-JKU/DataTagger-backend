import logging
import os
from typing import Tuple

from celery import shared_task
from django.conf import settings
from django.core.exceptions import PermissionDenied

from fdm._celery import app
from fdm.core.helpers import set_request_for_user
from fdm.storages.models import DynamicStorage

__all__ = [
    "check_private_dss_storages_mounting_status",
]

logger = logging.getLogger(__name__)


def construct_dss_path(mount_point: str) -> str:
    """
    Construct the full DSS path from a mount point.

    :param mount_point: The mount point (e.g., "pn49podss0001" or "/pn49podss0001")
    :return: Full path using the configured DSS base path (e.g., "/dssmount/pn49podss0001")
    """
    # Use the Django setting for DSS mount path
    dss_base_path = getattr(settings, "PRIVATE_DSS_MOUNT_PATH", "/dssmount")

    if mount_point.startswith(f"{dss_base_path}/"):
        return mount_point
    else:
        # Remove leading slash and join with DSS base path
        clean_path = mount_point.lstrip("/")
        return f"{dss_base_path}/{clean_path}"


def is_storage_accessible(mount_point: str) -> Tuple[bool, str]:
    """
    Simple check if a DSS storage path exists and is a directory.

    :param mount_point: The mount point to check
    :return: Tuple of (is_accessible, status_message)
    """
    try:
        full_path = construct_dss_path(mount_point)
        logger.debug(f"Checking accessibility of: {full_path}")

        # Check if path exists
        if not os.path.exists(full_path):
            return False, f"Path does not exist: {full_path}"

        # Check if it's a directory
        if not os.path.isdir(full_path):
            return False, f"Path is not a directory: {full_path}"

        return True, f"Path exists and is a directory: {full_path}"

    except OSError as e:
        # This catches symlink loops and other OS-level issues
        if "Too many levels of symbolic links" in str(e):
            return False, f"Symlink loop detected: {full_path}"
        return False, f"OS error: {e}"
    except Exception as e:
        logger.error(f"Unexpected error checking {mount_point}: {e}")
        return False, f"Unexpected error: {e}"


def trigger_and_check_storage(mount_point: str, max_retries: int = 3) -> Tuple[bool, str]:
    """
    Check storage accessibility with retries to handle autofs delays.

    :param mount_point: The mount point to check
    :param max_retries: Maximum number of attempts
    :return: Tuple of (is_accessible, final_status_message)
    """
    import time

    for attempt in range(max_retries):
        is_accessible, message = is_storage_accessible(mount_point)

        if is_accessible:
            if attempt > 0:
                logger.info(f"Storage {mount_point} became accessible after {attempt + 1} attempts")
            return True, message

        # If this was an OS error (possibly autofs not triggered), wait and retry
        if "Cannot access" in message and attempt < max_retries - 1:
            logger.debug(f"Attempt {attempt + 1} failed for {mount_point}, retrying in 2 seconds: {message}")
            time.sleep(2)
        else:
            logger.debug(f"Attempt {attempt + 1} failed for {mount_point}: {message}")

    return False, message


@shared_task
def check_private_dss_storages_mounting_status():
    """
    Celery task to check the accessibility of private DSS storages.
    """
    logger.info("Starting private DSS storage accessibility check")

    try:
        storages = DynamicStorage.objects.filter(
            storage_type="private_dss",
            local_private_dss_path_encrypted__isnull=False,
            approved=True,
            mounted=False,
        )

        logger.info(f"Found {storages.count()} storages to check")

        accessible_count = 0
        not_accessible_count = 0
        error_count = 0

        for storage in storages:
            try:
                set_request_for_user(storage.last_modified_by)

                mount_point = storage.local_private_dss_path
                logger.info(f"Checking storage '{storage}' with mount point: {mount_point}")

                # Check for development/testing environment
                is_dev_mode = (
                    hasattr(settings, "PRIVATE_DSS_MOUNT_PATH")
                    and hasattr(settings, "MEDIA_ROOT")
                    and settings.PRIVATE_DSS_MOUNT_PATH == settings.MEDIA_ROOT
                )

                if is_dev_mode:
                    logger.info(f"Development mode detected for storage '{storage}'")
                    storage.mounted = True
                    storage.save()
                    accessible_count += 1
                    continue

                # Check storage accessibility with retries for autofs
                is_accessible, status_message = trigger_and_check_storage(mount_point)

                if is_accessible:
                    storage.mounted = True
                    storage.save()
                    logger.info(f"Storage '{storage}' is accessible: {status_message}")
                    accessible_count += 1
                else:
                    storage.mounted = False
                    storage.save()
                    logger.info(f"Storage '{storage}' is not accessible: {status_message}")
                    not_accessible_count += 1

            except PermissionDenied as e:
                logger.error(f"Permission denied for storage '{storage}': {e}")
                error_count += 1
                # Don't update the storage status on permission errors

            except Exception as e:
                logger.error(f"Unexpected error checking storage '{storage}': {e}")
                error_count += 1
                # Don't update the storage status on unexpected errors

        logger.info(
            f"Private DSS storage check completed. "
            f"Accessible: {accessible_count}, Not accessible: {not_accessible_count}, Errors: {error_count}",
        )

    except Exception as e:
        logger.error(f"Fatal error in check_private_dss_storages_mounting_status: {e}")
        raise
