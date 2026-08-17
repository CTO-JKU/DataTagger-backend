import uuid

from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from fdm.core.models import BaseModel, ByUserMixin, TimestampMixin

__all__ = [
    "get_export_upload_to_path",
    "ExportJob",
    "MetadataTemplateExportJob",
]


def get_export_upload_to_path(instance, filename):
    return f"exports/{instance.pk}/{filename}"


class ExportJob(BaseModel, TimestampMixin, ByUserMixin):
    class Status(models.TextChoices):
        SCHEDULED = "SCHEDULED", _("Scheduled")
        IN_PROGRESS = "IN_PROGRESS", _("In progress")
        ERROR = "ERROR", _("Error")
        FINISHED = "FINISHED", _("Finished")

    class MetadataFormat(models.TextChoices):
        JSON = "json", _("JSON")
        CSV = "csv", _("CSV")
        XML = "xml", _("XML")

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    export_type = models.CharField(
        max_length=255,
    )

    object_ids = models.JSONField(
        blank=False,
        null=False,
        default=list,
    )

    exported_uploads_version_ids = models.JSONField(
        blank=True,
        null=False,
        default=list,
    )

    source_export_job = models.ForeignKey(
        "self",
        blank=True,
        null=True,
        related_name="reused_export_jobs",
        on_delete=models.SET_NULL,
    )

    metadata_format = models.CharField(
        max_length=16,
        choices=MetadataFormat.choices,
        default=MetadataFormat.JSON,
    )

    include_uploads_version_file = models.BooleanField(
        default=True,
    )

    include_uploads_version_file_metadata = models.BooleanField(
        default=True,
    )

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.SCHEDULED,
    )

    zip_file = models.FileField(
        upload_to=get_export_upload_to_path,
        max_length=300,
        blank=True,
        null=True,
    )

    download_token_hash = models.CharField(
        max_length=255,
        blank=True,
        null=True,
    )

    download_base_url = models.CharField(
        max_length=2048,
        blank=True,
        null=True,
    )

    expires_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    finished_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    error_message = models.TextField(
        blank=True,
        null=True,
    )

    def set_download_token(self, token: str) -> None:
        self.download_token_hash = make_password(token)

    def check_download_token(self, token: str) -> bool:
        if not token or not self.download_token_hash:
            return False

        return check_password(token, self.download_token_hash)

    def is_download_expired(self) -> bool:
        return bool(self.expires_at and self.expires_at <= timezone.now())

    def __str__(self):
        return f"{self.export_type}: {self.pk}"


class MetadataTemplateExportJob(BaseModel, TimestampMixin, ByUserMixin):
    class Status(models.TextChoices):
        SCHEDULED = "SCHEDULED", _("Scheduled")
        IN_PROGRESS = "IN_PROGRESS", _("In progress")
        ERROR = "ERROR", _("Error")
        FINISHED = "FINISHED", _("Finished")

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    metadata_template_container_ids = models.JSONField(
        blank=False,
        null=False,
        default=list,
    )

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.SCHEDULED,
    )

    zip_file = models.FileField(
        upload_to=get_export_upload_to_path,
        max_length=255,
        blank=True,
        null=True,
    )

    download_token_hash = models.CharField(
        max_length=255,
        blank=True,
        null=True,
    )

    download_base_url = models.CharField(
        max_length=2048,
        blank=True,
        null=True,
    )

    expires_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    finished_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    error_message = models.TextField(
        blank=True,
        null=True,
    )

    def set_download_token(self, token: str) -> None:
        self.download_token_hash = make_password(token)

    def check_download_token(self, token: str) -> bool:
        if not token or not self.download_token_hash:
            return False

        return check_password(token, self.download_token_hash)

    def is_download_expired(self) -> bool:
        return bool(self.expires_at and self.expires_at <= timezone.now())

    def __str__(self):
        return f"metadata template export: {self.pk}"
