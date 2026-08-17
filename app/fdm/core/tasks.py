import logging
from smtplib import SMTPException, SMTPRecipientsRefused

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail

from fdm._celery import app

logger = logging.getLogger(__name__)


def cleanup(self, exc, task_id, args, kwargs, einfo):
    logger.error(f"Error while sending email for task_id {task_id}: {exc}")


@shared_task(
    bind=True,
    autoretry_for=(SMTPException,),
    retry_kwargs={"max_retries": 3},
    retry_backoff=True,
    on_failure=cleanup,
)
def send_mail_via_celery(self, subject: str, message: str, html_message: str, recipient_list: list[str]) -> int:
    successful_sends = 0

    for recipient in recipient_list:
        try:
            send_mail(
                subject=subject,
                message=message,
                html_message=html_message,
                from_email=settings.EMAIL_SENDER,
                recipient_list=[recipient],
            )
            successful_sends += 1
        except SMTPRecipientsRefused as e:
            logger.error(f"Email to {recipient} was refused. Reason: {e}")
        except Exception as e:
            logger.error(f"Email to {recipient} could not be sent: {e}")

    logger.info(f"Successfully sent {successful_sends} emails to {recipient_list}")
    return successful_sends
