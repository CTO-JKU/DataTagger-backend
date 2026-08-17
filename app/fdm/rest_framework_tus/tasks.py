import logging

from celery import shared_task
from django.utils import timezone

from fdm._celery import app
from fdm.rest_framework_tus.models import Upload

logger = logging.getLogger(__name__)


@shared_task
def remove_expired_uploads():
    uploads = Upload.objects.filter(
        expires__lte=timezone.now(),
    )

    for upload in uploads:
        try:
            upload.delete()
        except Exception as e:
            logger.error(f"Task 'remove_expired_uploads' for upload '{upload}' failed: {e}")
