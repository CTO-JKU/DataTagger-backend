from django.db import models
from django.utils.translation import gettext_lazy as _

__all__ = [
    "MetadataFieldType",
]


class MetadataFieldType(models.TextChoices):
    INTEGER = "INTEGER", _("Integer")
    DECIMAL = "DECIMAL", _("Decimal")
    DATETIME = "DATETIME", _("Date & Time")
    DATE = "DATE", _("Date")
    TIME = "TIME", _("Time")
    TEXT = "TEXT", _("Text")
    WYSIWYG = "WYSIWYG", _("WYSIWYG editor")
    SELECTION = "SELECTION", _("Selection")
    MULTI_SELECTION = "MULTI_SELECTION", _("Multi selection")
    TAG = "TAG", _("Tag")
