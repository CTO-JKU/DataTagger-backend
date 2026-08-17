import logging

from celery import shared_task

from fdm._celery import app
from fdm.metadata.models import MetadataTemplateContainer

logger = logging.getLogger(__name__)


@shared_task
def remove_expired_locks():
    metadata_template_containers = MetadataTemplateContainer.objects.filter(
        locked=True,
    )

    for metadata_template_container in metadata_template_containers:
        try:
            metadata_template_container.remove_expired_lock()
        except Exception as e:
            logger.error(
                f"Task 'remove_expired_lock' for metadata template container '{metadata_template_container}' failed: {e}",
            )
