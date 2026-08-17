import logging
import uuid

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from fdm.core.models import BaseModel, ByUserMixin, TimestampMixin

User = get_user_model()

logger = logging.getLogger(__name__)


__all__ = [
    "ApprovalQueue",
]


class ApprovalQueue(BaseModel, TimestampMixin, ByUserMixin):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
    )

    object_id = models.UUIDField()

    content_object = GenericForeignKey(
        "content_type",
        "object_id",
    )

    approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    approved_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = "Approval Queue Item"
        verbose_name_plural = "Approval Queue"
        constraints = [
            models.UniqueConstraint(
                fields=["content_type", "object_id"],
                name="unique_approval_queue_content",
            ),
        ]
