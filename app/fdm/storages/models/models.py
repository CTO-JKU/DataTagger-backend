import logging
import os
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import FileSystemStorage, default_storage
from django.db import models
from django.db.models.fields.files import FieldFile
from django.utils.translation import gettext_lazy as _
from django_fernet.fields import *
from waffle import switch_is_active

from fdm.core.models import ApprovalQueueMixin, BaseModel, ByUserMixin, TimestampMixin
from fdm.storages.models.mappings import DEFAULT_STORAGE_TYPE, STORAGE_PROVIDER_MAP

User = get_user_model()

logger = logging.getLogger(__name__)

__all__ = [
    "DynamicStorage",
    "Storage",
    "StorageLocationLocal",
    "StorageLocationNasLrz",
    "StorageLocation",
    "DynamicStorageFileField",
]


class DynamicStorage(BaseModel, TimestampMixin, ByUserMixin, ApprovalQueueMixin):
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

    description = models.JSONField(
        blank=True,
        null=False,
        default=dict,
    )

    storage_type = models.CharField(
        max_length=32,
        choices=[(k, v["name"]) for k, v in STORAGE_PROVIDER_MAP.items()],
        default=DEFAULT_STORAGE_TYPE,
        help_text=_("Type of storage provider"),
    )

    local_private_dss_path_encrypted = FernetTextField(
        verbose_name=_("The path of a locally mounted private DSS container."),
        null=True,
        blank=True,
    )

    default = models.BooleanField(
        default=False,
    )

    mounted = models.BooleanField(
        default=False,
    )

    @property
    def local_private_dss_path(self):
        if self.local_private_dss_path_encrypted:
            return self.local_private_dss_path_encrypted.decrypt(settings.SECRET_KEY)
        return None

    def __str__(self):
        return f"{self.name} ({self.storage_type})"

    def save(self, *args, **kwargs):
        # Check waffle switch for `private_dss`
        if self.storage_type == "private_dss" and not switch_is_active("storage_private_dss_enabled_switch"):
            raise PermissionDenied("The 'private_dss' storage type is currently not enabled.")

        super().save(*args, **kwargs)

        default_storage = Storage.objects.filter(default=True)
        if self.default:
            default_storage.update(default=False)
        elif not default_storage.exclude(pk=self.pk).exists():
            self.default = True

    @property
    def storage_backend(self):
        """Retrieve or initialize the storage backend."""
        if not self._is_valid_storage_type():
            return default_storage

        if not hasattr(self, "_storage_backend"):
            self._initialize_storage_backend()

        return self._storage_backend

    def _is_valid_storage_type(self):
        """Check if storage type is valid and has a storage class defined."""
        return self.storage_type in STORAGE_PROVIDER_MAP and "class" in STORAGE_PROVIDER_MAP[self.storage_type]

    def _initialize_storage_backend(self):
        """Initialize the storage backend based on the storage_type."""
        storage_config = STORAGE_PROVIDER_MAP[self.storage_type]
        storage_class = storage_config["class"]

        # Prepare initialization arguments
        create_kwargs = storage_config.get("kwargs", {}).copy()

        if self.storage_type == "private_dss":
            create_kwargs.update(self._get_private_dss_kwargs())

        self._storage_backend = storage_class(**create_kwargs)

    def _get_private_dss_kwargs(self):
        """Get kwargs specific to private DSS."""
        if not self.local_private_dss_path_encrypted:
            raise ValidationError(_("A private DSS container path is required."))

        location = os.path.join(
            settings.PRIVATE_DSS_MOUNT_PATH,
            self.local_private_dss_path_encrypted.decrypt(settings.SECRET_KEY).lstrip("/"),
        )
        return {"location": location}

    def path(self, name):
        """Delegate path method to storage backend"""
        return os.path.join(self.storage_backend.location, name)

    @property
    def is_local(self):
        return STORAGE_PROVIDER_MAP[self.storage_type]["local"] is True

    @property
    def is_cloud(self):
        return STORAGE_PROVIDER_MAP[self.storage_type]["local"] is False

    def get_upload_to_path(self, instance, filename):
        return self.storage_backend.get_upload_to_path(instance, filename)

    def move_file(self, version_file):
        return self.storage_backend.move_file(version_file)

    def check_credentials(self):
        # Implement credential checking logic here
        pass

    def clean(self):
        if self.storage_type == "private_dss" and not switch_is_active("storage_private_dss_enabled_switch"):
            raise PermissionDenied("The 'private_dss' storage type is currently not enabled.")

        super().clean()

        default_storages = DynamicStorage.objects.filter(default=True)

        if not self.pk and not self.default and not default_storages.exists():
            self.default = True

        if self.default:
            default_exists = default_storages.exclude(pk=self.pk).exists()
            if default_exists:
                raise ValidationError(
                    {
                        "default": _("Another storage is already set as default. Please unset it first."),
                    },
                )

    class Meta:
        verbose_name = _("Storage")
        verbose_name_plural = _("Storages")

        indexes = [
            models.Index(fields=["storage_type"]),
            models.Index(fields=["default"]),
        ]


class Storage(BaseModel, TimestampMixin, ByUserMixin):
    class Type(models.TextChoices):
        LOCAL = "LOCAL", _("Local")
        NAS_LRZ = "NAS_LRZ", _("NAS LRZ")

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

    storage_type = models.CharField(
        max_length=32,
        choices=Type.choices,
        default=Type.LOCAL,
    )

    default = models.BooleanField(
        default=False,
    )

    def __str__(self):
        return f"{self.name} ({self.storage_type})"

    def save(self, *args, **kwargs):
        default_storage = Storage.objects.filter(default=True)

        if self.default:
            default_storage.update(default=False)
        elif not default_storage.exclude(pk=self.pk).exists():
            self.default = True

        super().save(*args, **kwargs)


class StorageLocationBase:
    path = ""

    def get_upload_to_path(self, instance, filename):
        storage = self.get_storage()
        folder = instance.get_folder()

        return storage.get_available_name(
            name=os.path.join(
                storage.location,
                self.path,
                str(folder.project.pk),
                str(folder.pk),
                filename,
            ),
            max_length=255,
        )

    def get_storage(self):
        return FileSystemStorage(
            location=os.path.join(settings.MEDIA_ROOT),
            base_url=os.path.join(settings.MEDIA_URL),
        )

    def move_file(self, version_file) -> (bool, str):
        from fdm.uploads.models import get_upload_to_path

        storage = self.get_storage()

        old_file_path = version_file.uploaded_file.name
        new_file_path = (
            get_upload_to_path(
                version_file,
                version_file.name,
            )
            .replace(storage.location, "", 1)
            .lstrip("/")
        )

        # Check if the file needs to be moved at all
        if os.path.dirname(old_file_path) == os.path.dirname(new_file_path):
            return False, old_file_path

        new_absolute_file_path = os.path.join(
            storage.location,
            new_file_path,
        )
        new_dir_name = os.path.dirname(new_absolute_file_path)

        if not os.path.exists(new_dir_name):
            os.makedirs(new_dir_name)

        # Move file according to the storage's appropriate method
        if os.path.exists(version_file.uploaded_file.path) and os.path.exists(new_dir_name):
            os.rename(
                version_file.uploaded_file.path,
                new_absolute_file_path,
            )

            # TODO: Cleanup. Remove old path if becomes empty.

            logger.debug(f"Moved version file from '{version_file.uploaded_file.path}' to '{new_absolute_file_path}'")

            return True, new_file_path
        else:
            logger.error(f"Version file '{version_file.uploaded_file.path}' does not exist")

            return False, version_file.uploaded_file.path


class StorageLocationLocal(StorageLocationBase):
    path = "local"


class StorageLocationNasLrz(StorageLocationBase):
    path = "nas_lrz"


class StorageLocation:
    def __init__(self, storage_type: str):
        if not storage_type:
            raise ValueError(_("A storage type must be provided."))

        self.storage_type = storage_type

    def get_class(self):
        if self.storage_type == Storage.Type.LOCAL:
            return StorageLocationLocal
        elif self.storage_type == Storage.Type.NAS_LRZ:
            return StorageLocationNasLrz

        raise NotImplementedError


class DynamicStorageFieldFile(FieldFile):
    """
    attr_class for DynamicStorageFileField
    This class checks if the instance is a published file at a different storage or not and sets the storage to be used
    accordingly.
    """

    def __init__(self, instance, field, name):
        super().__init__(instance, field, name)

        storage = instance.get_storage()

        if instance.is_published() and storage:
            Storage = StorageLocation(storage.storage_type).get_class()
            self.storage = Storage().get_storage()
        else:
            self.storage = FileSystemStorage()


class DynamicStorageFileField(models.FileField):
    """
    Custom FileField to be used for the path of File and UploadedFileEntry.
    This class checks if the instance is a published file at a different storage or not and sets the storage to be used
    accordingly.
    """

    attr_class = DynamicStorageFieldFile

    def pre_save(self, model_instance, add):
        storage = model_instance.get_storage()

        if model_instance.is_published() and storage:
            Storage = StorageLocation(storage.storage_type).get_class()
            self.storage = Storage().get_storage()
        else:
            self.storage = FileSystemStorage()
        file = super().pre_save(model_instance, add)
        return file
