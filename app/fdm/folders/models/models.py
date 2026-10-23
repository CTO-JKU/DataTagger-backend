import uuid

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.fields import GenericRelation
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models
from django.db.models import Q, QuerySet
from django.utils.translation import gettext_lazy as _
from waffle import switch_is_active

from fdm.core.models import BaseModel, ByUserMixin, LockMixin, TimestampMixin
from fdm.folders.signals import folder_datasets_count_updated
from fdm.metadata.models import Metadata, MetadataTemplate, MetadataTemplateContainer
from fdm.projects.models import Project, ProjectMembership
from fdm.storages.models import DynamicStorage

User = get_user_model()

__all__ = [
    "get_default_folder_storage",
    "Folder",
    "FolderPermission",
    "Subfolder",
]


def get_default_folder_storage() -> DynamicStorage | None:
    try:
        return DynamicStorage.objects.get(default=True)
    except DynamicStorage.DoesNotExist:
        return None


class Folder(BaseModel, TimestampMixin, ByUserMixin, LockMixin):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    project = models.ForeignKey(
        Project,
        blank=False,
        null=False,
        related_name="folder",
        on_delete=models.CASCADE,
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

    storage = models.ForeignKey(
        DynamicStorage,
        blank=True,
        null=True,
        default=get_default_folder_storage,
        related_name="folder",
        on_delete=models.SET_DEFAULT,
    )

    metadata_template = models.ForeignKey(
        MetadataTemplate,
        blank=True,
        null=True,
        related_name="folder",
        on_delete=models.SET_NULL,
    )

    members_count = models.IntegerField(
        blank=False,
        null=False,
        default=0,
    )

    datasets_count = models.IntegerField(
        blank=False,
        null=False,
        default=0,
    )

    metadata_templates_count = models.IntegerField(
        blank=False,
        null=False,
        default=0,
    )

    storage_size = models.BigIntegerField(
        blank=False,
        null=False,
        default=0,
    )

    metadata = GenericRelation(
        Metadata,
        content_type_field="assigned_to_content_type",
        object_id_field="assigned_to_object_id",
    )

    def update_members_count(self) -> None:
        from fdm.websockets.consumers.handlers import send_model_changed_websocket_message

        new_count = FolderPermission.objects.filter(
            folder=self,
        ).count()

        # We must use .update() to avoid running into a lock
        Folder.objects.filter(pk=self.pk).update(members_count=new_count)
        self.members_count = new_count

        send_model_changed_websocket_message(self)

    def update_datasets_count(self) -> None:
        from fdm.uploads.models import UploadsDataset
        from fdm.websockets.consumers.handlers import send_model_changed_websocket_message

        new_count = UploadsDataset.objects.filter(
            folder=self,
            publication_date__isnull=False,
        ).count()

        # We must use .update() to avoid running into a lock
        Folder.objects.filter(pk=self.pk).update(datasets_count=new_count)
        self.datasets_count = new_count

        send_model_changed_websocket_message(self)

        folder_datasets_count_updated.send(
            sender=self.__class__,
            instance=self,
        )

    def update_storage_size(self) -> None:
        from django.contrib.contenttypes.models import ContentType

        from fdm.core.helpers import get_metadata_value_file_size
        from fdm.metadata.models import Metadata
        from fdm.uploads.models import UploadsVersion, UploadsVersionFile

        total_size = 0
        file_ids = list(
            UploadsVersion.objects.filter(
                dataset__folder=self.pk,
            ).values_list("version_file_id", flat=True),
        )

        if file_ids:
            metadata_values = Metadata.objects.filter(
                assigned_to_content_type=ContentType.objects.get_for_model(UploadsVersionFile),
                assigned_to_object_id__in=file_ids,
                custom_key="FILE_SIZE",
            ).values_list("value", flat=True)

            for metadata_value in metadata_values:
                total_size += get_metadata_value_file_size(metadata_value)

        # We must use .update() to avoid running into a lock
        Folder.objects.filter(pk=self.pk).update(storage_size=total_size)

    def update_metadata_templates_count(self) -> None:
        # We are currently intentionally ignoring global templates and counting all metadata template versions
        new_count = MetadataTemplate.objects.filter(
            # Metadata templates assigned to the folder itself
            Q(
                metadata_template_container__assigned_to_content_type=self.get_content_type(),
                metadata_template_container__assigned_to_object_id=self.pk,
            )
            # Metadata templates assigned to the project the folder is part of
            | Q(
                metadata_template_container__assigned_to_content_type=self.project.get_content_type(),
                metadata_template_container__assigned_to_object_id=self.project.pk,
            ),
        ).count()

        # We must use .update() to avoid running into a lock
        Folder.objects.filter(pk=self.pk).update(metadata_templates_count=new_count)
        self.metadata_templates_count = new_count

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.cache = {
            "storage": self.storage,
        }

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Ensure the validation only runs for new instances or when storage is being updated
        if self.storage and self.storage.storage_type == "private_dss":
            if not switch_is_active("storage_private_dss_enabled_switch"):
                raise PermissionDenied(
                    _("The 'private_dss' storage type is currently disabled by the system."),
                )
            if not (self.storage.approved and self.storage.mounted):  # Check both flags
                raise PermissionDenied(
                    _("The selected private DSS storage is not yet approved or mounted."),
                )

        super().save(*args, **kwargs)

        # Check if the storage changed and move all files in this folder to the new storage location
        if self.cache["storage"] != self.storage:
            from fdm.uploads.models import UploadsVersionFile

            UploadsVersionFile.objects.filter(
                uploads_versions__dataset__folder=self,
            ).update(
                storage_relocating=UploadsVersionFile.Status.SCHEDULED,
            )

    def clean(self):
        from fdm.uploads.models import UploadsDataset

        if not isinstance(self.description, dict):
            raise ValidationError(_("Description contains invalid JSON data"))

        # Make sure that the metadata template container of the active metadata template is not already an active
        # metadata template container elsewhere in the hierarchy of project > folder > dataset.
        if self.metadata_template and (
            (
                self.project.metadata_template
                and self.project.metadata_template.metadata_template_container
                == self.metadata_template.metadata_template_container
            )
            or UploadsDataset.objects.filter(
                folder=self,
                metadata_template__metadata_template_container=self.metadata_template.metadata_template_container,
            ).exists()
        ):
            raise ValidationError(
                _("A metadata template can only be actively assigned to one instance in the hierarchy at a time"),
            )

    def get_available_metadata_template_container(self) -> QuerySet:
        return MetadataTemplateContainer.objects.filter(
            # Metadata template container assigned to the folder itself
            Q(
                assigned_to_content_type=self.get_content_type(),
                assigned_to_object_id=self.pk,
            )
            # Metadata template container assigned to the project the folder is part of
            | Q(
                assigned_to_content_type=self.project.get_content_type(),
                assigned_to_object_id=self.project.pk,
            )
            # Globally available metadata template container
            | Q(
                assigned_to_content_type__isnull=True,
                assigned_to_object_id__isnull=True,
            ),
        ).order_by(
            "name",
            "creation_date",
        )

    def get_available_metadata_templates(self) -> QuerySet:
        return MetadataTemplate.objects.filter(
            # Metadata templates assigned to the folder itself
            Q(
                metadata_template_container__assigned_to_content_type=self.get_content_type(),
                metadata_template_container__assigned_to_object_id=self.pk,
            )
            # Metadata templates assigned to the project the folder is part of
            | Q(
                metadata_template_container__assigned_to_content_type=self.project.get_content_type(),
                metadata_template_container__assigned_to_object_id=self.project.pk,
            )
            # Globally available metadata templates
            | Q(
                metadata_template_container__assigned_to_content_type__isnull=True,
                metadata_template_container__assigned_to_object_id__isnull=True,
            ),
        ).order_by(
            "metadata_template_container__name",
            "creation_date",
        )


class FolderPermission(BaseModel):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    project_membership = models.ForeignKey(
        ProjectMembership,
        on_delete=models.CASCADE,
    )

    folder = models.ForeignKey(
        Folder,
        on_delete=models.CASCADE,
    )

    is_folder_admin = models.BooleanField(
        default=False,
    )

    is_metadata_template_admin = models.BooleanField(
        default=False,
    )

    can_edit = models.BooleanField(
        default=False,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["folder", "project_membership"],
                name="unique_folder_project_membership",
            ),
        ]

    def __str__(self):
        return (
            f"{self.project_membership.member.email} "
            f"(Folder: {self.folder.name} in Project: {self.project_membership.project})"
        )

    @property
    def member(self):
        return self.project_membership.member


class Subfolder(BaseModel, TimestampMixin, ByUserMixin):
    """
    An organisational subfolder within a folder. Subfolders can be nested and inherit the permissions, storage and
    metadata templates of the folder they belong to.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    folder = models.ForeignKey(
        Folder,
        blank=False,
        null=False,
        related_name="subfolders",
        on_delete=models.CASCADE,
    )

    parent = models.ForeignKey(
        "self",
        blank=True,
        null=True,
        related_name="children",
        on_delete=models.CASCADE,
    )

    name = models.CharField(
        max_length=255,
        blank=False,
        null=False,
    )

    metadata = GenericRelation(
        Metadata,
        content_type_field="assigned_to_content_type",
        object_id_field="assigned_to_object_id",
    )

    class Meta:
        ordering = [
            "name",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["folder", "parent", "name"],
                condition=Q(parent__isnull=False),
                name="unique_subfolder_name_in_parent",
                violation_error_message=_("A subfolder with this name already exists here."),
            ),
            models.UniqueConstraint(
                fields=["folder", "name"],
                condition=Q(parent__isnull=True),
                name="unique_subfolder_name_in_folder_root",
                violation_error_message=_("A subfolder with this name already exists here."),
            ),
        ]

    def __str__(self):
        return f"{self.folder.name} / {' / '.join(subfolder.name for subfolder in self.get_path())}"

    def clean(self):
        self.name = (self.name or "").strip()

        if not self.name or self.name in [".", ".."] or any(char in self.name for char in "/\\"):
            raise ValidationError({"name": _("Please provide a valid subfolder name without slashes.")})

        if self.parent:
            if self.parent.folder_id != self.folder_id:
                raise ValidationError({"parent": _("The parent subfolder must belong to the same folder.")})

            # Prevent moving a subfolder into itself or one of its own descendants
            ancestor = self.parent
            while ancestor:
                if ancestor.pk == self.pk:
                    raise ValidationError({"parent": _("A subfolder can't be moved into itself or its subfolders.")})
                ancestor = ancestor.parent

    def get_path(self) -> list["Subfolder"]:
        """
        Returns the list of subfolders from the top-most ancestor down to this subfolder.
        """
        path = []
        subfolder = self
        while subfolder:
            path.insert(0, subfolder)
            subfolder = subfolder.parent
        return path

    def get_descendant_pks(self) -> list:
        pks = [self.pk]
        children = list(Subfolder.objects.filter(parent__in=pks).values_list("pk", flat=True))
        while children:
            pks += children
            children = list(Subfolder.objects.filter(parent__in=children).values_list("pk", flat=True))
        return pks

    def has_datasets(self) -> bool:
        from fdm.uploads.models import UploadsDataset

        return UploadsDataset.objects.filter(subfolder__in=self.get_descendant_pks()).exists()

    @classmethod
    def get_or_create_path(cls, folder: Folder, path: str, parent: "Subfolder | None" = None) -> "Subfolder | None":
        """
        Resolves a relative path like "10 / pdata / 1" or "10/pdata/1" below the given parent (or the folder root),
        creating missing subfolders on the way. Returns the deepest subfolder, or the parent for an empty path.
        """
        segments = [segment.strip() for segment in path.replace("\\", "/").split("/")]

        subfolder = parent
        for segment in segments:
            if not segment or segment in [".", ".."]:
                continue

            existing = cls.objects.filter(folder=folder, parent=subfolder, name=segment).first()
            subfolder = existing or cls.objects.create(folder=folder, parent=subfolder, name=segment)

        return subfolder
