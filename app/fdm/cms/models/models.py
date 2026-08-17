import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _

from fdm.core.models import BaseModel, ByUserMixin, TimestampMixin

__all__ = [
    "Content",
]


class Content(BaseModel, TimestampMixin, ByUserMixin):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    name = models.CharField(
        verbose_name=_("Name of the content"),
        max_length=128,
        db_index=True,
    )

    slug = models.SlugField(
        max_length=128,
        db_index=True,
        unique=True,
        verbose_name=_("URL slug for this content"),
    )

    published = models.BooleanField(
        default=False,
    )

    text_de = models.TextField(
        verbose_name=_("German text"),
        blank=True,
        null=True,
    )

    text_en = models.TextField(
        verbose_name=_("English Text"),
        blank=True,
        null=True,
    )

    class Meta:
        verbose_name = "Content Page"
        verbose_name_plural = "Content Pages"

    def __str__(self):
        return self.name
