import uuid

from django.contrib.auth import get_user_model
from django.db import models

from fdm.core.models import BaseModel, ByUserMixin, TimestampMixin

User = get_user_model()

__all__ = [
    "FAQCategory",
    "FAQ",
]


class FAQCategory(BaseModel, ByUserMixin, TimestampMixin):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    name = models.CharField(
        max_length=255,
        blank=False,
        null=False,
    )

    slug = models.SlugField(
        max_length=255,
        blank=True,
        null=False,
        unique=True,
    )

    order = models.PositiveIntegerField(
        default=0,
        blank=False,
        null=False,
        db_index=True,
    )

    published = models.BooleanField(
        default=False,
    )

    class Meta:
        ordering = [
            "order",
        ]

    def __str__(self):
        return self.name


class FAQ(BaseModel, ByUserMixin, TimestampMixin):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    question = models.CharField(
        max_length=255,
        blank=False,
        null=False,
    )

    slug = models.SlugField(
        max_length=255,
        blank=True,
        null=False,
        unique=True,
    )

    answer = models.TextField(
        blank=True,
        null=True,
    )

    category = models.ForeignKey(
        FAQCategory,
        blank=True,
        null=True,
        related_name="faq",
        on_delete=models.CASCADE,
    )

    order = models.PositiveIntegerField(
        default=0,
        blank=False,
        null=False,
        db_index=True,
    )

    published = models.BooleanField(
        default=False,
    )

    class Meta:
        ordering = [
            "order",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=["question", "category"],
                name="unique_faq_question_category",
            ),
        ]

    def __str__(self):
        return self.question
