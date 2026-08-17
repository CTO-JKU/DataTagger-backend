import os
import tempfile
import time
import uuid

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_userforeignkey.models.fields import UserForeignKey
from django_userforeignkey.request import get_current_user

from fdm.rest_framework_tus import settings, signals, states
from fdm.rest_framework_tus.utils import write_bytes_to_file
from fdm.uploads.models import UploadsDataset

__all__ = [
    "get_upload_to_path",
    "Upload",
]


def get_upload_to_path(instance, filename):
    now = timezone.now()
    user = get_current_user()
    return now.strftime(f"temp/{user.id}/%Y/%m/%d/{filename}")


def custom_upload_path(instance, filename):
    # file will be uploaded to MEDIA_ROOT/<TUS_UPLOAD_DESTINATION>/<filename>
    return os.path.join(settings.TUS_UPLOAD_DESTINATION, filename)


class AbstractUpload(models.Model):
    """
    Abstract model for managing TUS uploads
    """

    guid = models.UUIDField(_("GUID"), default=uuid.uuid4, unique=True)

    state = models.CharField(max_length=50, default=states.INITIAL)

    upload_offset = models.BigIntegerField(default=0)
    upload_length = models.BigIntegerField(default=-1)

    upload_metadata = models.JSONField(default=dict)

    filename = models.CharField(max_length=255, blank=True)

    temporary_file_path = models.CharField(max_length=4096, null=True)

    expires = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True

    def clean_fields(self, exclude=None):
        super().clean_fields(exclude=exclude)
        if self.upload_offset < 0:
            raise ValidationError(_("upload_offset should be >= 0."))

    def write_data(self, bytes, chunk_size):
        num_bytes_written = write_bytes_to_file(self.temporary_file_path, self.upload_offset, bytes, makedirs=True)

        if num_bytes_written >= 0:
            self.upload_offset += num_bytes_written
            self.save()

    def delete(self, *args, **kwargs):
        if self.temporary_file_path and os.path.exists(self.temporary_file_path):
            os.remove(self.temporary_file_path)

        super().delete(*args, **kwargs)

    def generate_filename(self):
        return os.path.join(f"{uuid.uuid4()}.bin")

    def save(self, force_insert=False, force_update=False, using=None, update_fields=None):
        if not self.filename:
            self.filename = self.generate_filename()
        return super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )

    def is_complete(self):
        if self.upload_length == 0:
            return True
        return self.upload_offset == self.upload_length

    def temporary_file_exists(self):
        return self.temporary_file_path and os.path.isfile(self.temporary_file_path)

    def _temporary_file_exists(self):
        return self.temporary_file_exists()

    def get_or_create_temporary_file(self):
        # Make sure the tmp upload path exists
        os.makedirs(settings.TUS_UPLOAD_DIR, exist_ok=True)

        if not self.temporary_file_path:
            fd, path = tempfile.mkstemp(prefix="tus-upload-", dir=settings.TUS_UPLOAD_DIR)
            os.close(fd)
            self.temporary_file_path = path
            self.save()

        # There may be a slight delay when being connected to a lot of external storages due to bandwidth.
        # To avoid this, we check for the temp file multiple times before finally throwing an error.
        retry_count = 0

        while True:
            try:
                assert os.path.isfile(self.temporary_file_path)
                break
            except AssertionError:
                if retry_count < 10:
                    retry_count += 1
                    time.sleep(0.1)
                else:
                    raise FileNotFoundError(f"Temporary file {self.temporary_file_path} does not exist.")

        return self.temporary_file_path

    def get_flow(self):
        """Returns the UploadFlow FSM wrapper for this upload instance."""
        from fdm.rest_framework_tus.flow import UploadFlow

        return UploadFlow(self)


class Upload(AbstractUpload):
    """
    Default Upload model
    """

    dataset = models.ForeignKey(
        UploadsDataset,
        blank=True,
        null=True,
        related_name="uploads",
        on_delete=models.CASCADE,
    )

    user = UserForeignKey(
        verbose_name=_("User who uploaded this file"),
        auto_user_add=True,
        null=True,
        related_name="%(class)s_created",
    )

    uploaded_file = models.FileField(
        max_length=4096,
        upload_to=get_upload_to_path,
        blank=True,
        null=True,
    )

    def delete(self, *args, **kwargs):
        if self.temporary_file_path and os.path.exists(self.temporary_file_path):
            os.remove(self.temporary_file_path)

        if self.dataset:
            self.dataset.unlock()

        super().delete(*args, **kwargs)


def get_upload_model():
    """
    Returns the Upload model that is active in this project.
    """
    from django.apps import apps as django_apps

    from .settings import TUS_UPLOAD_MODEL

    try:
        return django_apps.get_model(TUS_UPLOAD_MODEL)
    except ValueError:
        raise ImproperlyConfigured("UPLOAD_MODEL must be of the form 'app_label.model_name'")
    except LookupError:
        raise ImproperlyConfigured("UPLOAD_MODEL refers to model '%s' that has not been installed" % TUS_UPLOAD_MODEL)
