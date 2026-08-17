import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from fdm.core.models import TimestampMixin

__all__ = [
    "ShibbolethAuthCode",
]


class ShibbolethAuthCode(TimestampMixin, models.Model):
    auth_code = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    def auth_code_is_expired(self) -> bool:
        time_difference = timezone.now() - self.creation_date
        return time_difference.total_seconds() > settings.SHIBBOLETH_AUTH_CODE_MAX_LIFESPAN

    @staticmethod
    def cleanup() -> None:
        ShibbolethAuthCode.objects.filter(
            creation_date__lt=timezone.now()
            - timezone.timedelta(
                seconds=settings.SHIBBOLETH_AUTH_CODE_MAX_LIFESPAN,
            ),
        ).delete()

    def __str__(self):
        return f"AuthCode: {self.auth_code})"
