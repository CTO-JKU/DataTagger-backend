import copy
import logging
import os
import uuid

from django.contrib.contenttypes.fields import GenericRelation
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.db.models import Q, QuerySet
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_userforeignkey.request import get_current_user

from fdm.core.helpers import check_empty_value
from fdm.core.models import BaseModel, ByUserMixin, LockMixin, TimestampMixin
from fdm.core.rest.permissions import can_edit_in_folder
from fdm.dbsettings.functions import get_dbsettings_value
from fdm.folders.models import Folder, Subfolder
from fdm.metadata.helpers import get_metadata_structure_for_type, set_metadata_for_relation, validate_metadata
from fdm.metadata.models import Metadata, MetadataTemplate, MetadataTemplateContainer, MetadataTemplateField
from fdm.storages.fields import DynamicRuntimeStorageFileField
from fdm.storages.models.mappings import STORAGE_PROVIDER_MAP
from fdm.uploads.models.managers import UploadsDatasetManager, UploadsVersionManager

logger = logging.getLogger(__name__)

__all__ = [
    "get_storage",
    "get_upload_to_path",
    "UploadsDataset",
    "UploadsVersion",
    "UploadsVersionFile",
]


def get_storage(instance):
    """Get storage instance with proper fallback"""
    if instance.is_published():
        storage = instance.get_storage()
        if storage:
            return storage.storage_backend
        # Fallback for published files uses "local" prefix
        create_class = STORAGE_PROVIDER_MAP["default_local"]["class"]
        create_kwargs = {"path_prefix": "local"}
    else:
        # Unpublished files always use "temp" prefix
        create_class = STORAGE_PROVIDER_MAP["default_local"]["class"]
        create_kwargs = {"path_prefix": "temp"}

    return create_class(**create_kwargs)


def get_upload_to_path(instance, filename):
    if instance.is_published():
        storage = instance.get_storage()
        return storage.get_upload_to_path(instance, filename)
    else:
        now = timezone.now()
        user = get_current_user()
        return now.strftime(f"temp/{user.id}/%Y/%m/%d/{filename}")


class UploadsDataset(BaseModel, TimestampMixin, ByUserMixin, LockMixin):
    class Status(models.TextChoices):
        SCHEDULED = "SCHEDULED", _("Scheduled")
        IN_PROGRESS = "IN_PROGRESS", _("In progress")
        ERROR = "ERROR", _("Error")
        FINISHED = "FINISHED", _("Finished")

    objects = UploadsDatasetManager()

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    name = models.CharField(
        max_length=255,
        blank=True,
        null=True,
    )

    display_name = models.CharField(
        max_length=255,
        blank=True,
        null=True,
    )

    folder = models.ForeignKey(
        Folder,
        blank=True,
        null=True,
        related_name="uploads_dataset",
        on_delete=models.CASCADE,
    )

    subfolder = models.ForeignKey(
        Subfolder,
        blank=True,
        null=True,
        related_name="uploads_dataset",
        on_delete=models.SET_NULL,
    )

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.SCHEDULED,
    )

    metadata_upgrade_status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.FINISHED,
    )

    metadata_upgrade_availability_status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.FINISHED,
    )

    metadata_template_processing_lease_token = models.UUIDField(
        blank=True,
        null=True,
    )

    metadata_template_processing_lease_expires_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    metadata_template_processing_heartbeat_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    metadata_template = models.ForeignKey(
        MetadataTemplate,
        blank=True,
        null=True,
        related_name="uploads_dataset",
        on_delete=models.SET_NULL,
    )

    has_upgradeable_metadata_templates = models.BooleanField(
        default=False,
    )

    publication_date = models.DateTimeField(
        verbose_name=_("Publication date"),
        blank=True,
        null=True,
    )

    expiry_date = models.DateTimeField(
        verbose_name=_("Expiry date"),
        blank=True,
        null=True,
    )

    deletable_until_date = models.DateTimeField(
        verbose_name=_("Deletable until date"),
        blank=True,
        null=True,
    )

    uploads_versions_count = models.IntegerField(
        blank=False,
        null=False,
        default=0,
    )

    @property
    def get_sorted_versions(self):
        return self.uploads_versions.order_by("-creation_date")

    @property
    def get_sorted_published_versions(self):
        return self.get_sorted_versions.exclude(
            publication_date__isnull=True,
        )

    @property
    def latest_version(self):
        return self.get_sorted_versions.first()

    @property
    def is_expired(self) -> bool:
        return bool(self.expiry_date and self.expiry_date < timezone.now())

    def latest_version_name(self) -> str | None:
        if self.latest_version and self.latest_version.version_file:
            try:
                original_filename = self.latest_version.version_file.metadata.get(custom_key="FILE_NAME")
                return original_filename.get_value()
            except Metadata.DoesNotExist:
                return self.latest_version.version_file.name

        return None

    def get_display_name(self):
        return f"{self.name or self.latest_version_name() or self.id}"

    def set_display_name(self):
        self.display_name = self.get_display_name()
        self.save()

    def is_published(self) -> bool:
        return self.publication_date is not None

    is_published.boolean = True
    is_published.short_description = "Is published"

    def _get_active_metadata_templates_for_upgrade(self) -> list[MetadataTemplate]:
        active_metadata_templates = []

        if self.is_published() and self.folder:
            if self.folder.project.metadata_template:
                active_metadata_templates.append(self.folder.project.metadata_template)

            if self.folder.metadata_template:
                active_metadata_templates.append(self.folder.metadata_template)

        if self.metadata_template:
            active_metadata_templates.append(self.metadata_template)

        return list({metadata_template for metadata_template in active_metadata_templates})

    def get_active_inherited_metadata_templates(self) -> list[MetadataTemplate]:
        active_metadata_templates = []

        if self.is_published() and self.folder:
            if self.folder.project.metadata_template:
                active_metadata_templates.append(self.folder.project.metadata_template)

            if self.folder.metadata_template:
                active_metadata_templates.append(self.folder.metadata_template)

        return list(self._get_metadata_templates_by_container(active_metadata_templates).values())

    def get_inherited_metadata_template_fields(self) -> list[MetadataTemplateField]:
        metadata_list = []

        for metadata_template in self.get_active_inherited_metadata_templates():
            metadata_list += metadata_template.metadata_template_fields.order_by(
                "order",
                "custom_key",
            )

        order = 0
        for metadata in metadata_list:
            metadata.order = order
            order += 1

        return metadata_list

    def is_latest_version_inherited_metadata_upgradeable(self) -> bool:
        if not self.latest_version:
            return False

        active_metadata_templates_by_container = self._get_metadata_templates_by_container(
            self.get_active_inherited_metadata_templates(),
        )
        if not active_metadata_templates_by_container:
            return False

        latest_metadata_templates_by_container = self._get_metadata_templates_by_container(
            [
                metadata_template
                for metadata_template in self.latest_version.metadata_templates.all()
                if str(metadata_template.metadata_template_container.pk) in active_metadata_templates_by_container
            ],
        )

        for container_pk, active_metadata_template in active_metadata_templates_by_container.items():
            if latest_metadata_templates_by_container.get(container_pk) != active_metadata_template:
                return True

        return False

    @staticmethod
    def _get_metadata_templates_by_container(metadata_templates: list[MetadataTemplate]) -> dict[str, MetadataTemplate]:
        return {
            str(metadata_template.metadata_template_container.pk): metadata_template
            for metadata_template in metadata_templates
        }

    def is_latest_version_upgradeable(self, max_upgrade=False) -> bool:
        if not self.latest_version:
            return False

        latest_metadata_templates = list(self.latest_version.metadata_templates.all())
        if max_upgrade:
            # Upgrade should be done to the latest version of a metadata template
            for metadata_template in latest_metadata_templates:
                if metadata_template.metadata_template_container.latest_metadata_template.pk != metadata_template.pk:
                    return True
        else:
            latest_metadata_templates_by_container = self._get_metadata_templates_by_container(
                latest_metadata_templates,
            )
            active_metadata_templates_by_container = self._get_metadata_templates_by_container(
                self._get_active_metadata_templates_for_upgrade(),
            )

            # Existing linked templates that are no longer active, or have a different active version.
            for container_pk, metadata_template in latest_metadata_templates_by_container.items():
                active_metadata_template = active_metadata_templates_by_container.get(container_pk)
                if not active_metadata_template or active_metadata_template.pk != metadata_template.pk:
                    return True

            # Newly active templates that are not currently linked.
            for container_pk, metadata_template in active_metadata_templates_by_container.items():
                latest_metadata_template = latest_metadata_templates_by_container.get(container_pk)
                if not latest_metadata_template or latest_metadata_template.pk != metadata_template.pk:
                    return True

            return False

        # Keep max_upgrade behavior as-is regarding currently active assignments.
        active_metadata_templates = self._get_active_metadata_templates_for_upgrade()
        for metadata_template in active_metadata_templates:
            if metadata_template not in latest_metadata_templates:
                return True

        return False

    is_latest_version_upgradeable.boolean = True
    is_latest_version_upgradeable.short_description = "Is upgradeable"

    def _collect_upgraded_latest_version_metadata(
        self,
        max_upgrade: bool = False,
        collect_conflicts: bool = False,
    ) -> tuple[list, list, list[dict], list[dict], str | None]:
        # If we don't refresh here, the data object will contain outdated references.
        self.refresh_from_db()

        latest_version = self.latest_version
        if not latest_version:
            return [], [], [], [], None

        base_version_pk = str(latest_version.pk)

        if not self.is_latest_version_upgradeable(max_upgrade=max_upgrade):
            return [], [], [], [], base_version_pk

        metadata_templates = list(latest_version.metadata_templates.all())
        metadata_list = latest_version.metadata.all()
        upgraded_metadata_list = []
        conflicts = []
        latest_version_for_container = {}
        old_metadata_templates_by_container = {}
        active_metadata_templates = self._get_active_metadata_templates_for_upgrade()
        active_metadata_templates_by_container = self._get_metadata_templates_by_container(active_metadata_templates)

        for metadata_template in metadata_templates:
            old_metadata_templates_by_container[str(metadata_template.metadata_template_container.pk)] = (
                metadata_template
            )

        if max_upgrade:
            # Upgrade should be done to the latest version of a metadata template
            latest_metadata_templates = list(
                {
                    metadata_template.metadata_template_container.latest_metadata_template
                    for metadata_template in metadata_templates
                },
            )
        else:
            # Upgrade should be done to the linked version of a metadata template. Metadata templates that are not
            # actively assigned anymore are not upgraded at all.
            latest_metadata_templates = []
            for metadata_template in metadata_templates:
                active_metadata_template = active_metadata_templates_by_container.get(
                    str(metadata_template.metadata_template_container.pk),
                )
                if active_metadata_template:
                    latest_metadata_templates.append(active_metadata_template)

        upgraded_metadata_templates_list = list({metadata_template for metadata_template in latest_metadata_templates})
        upgraded_metadata_template_container_list = [
            metadata_template.metadata_template_container for metadata_template in active_metadata_templates
        ]

        for metadata_template in upgraded_metadata_templates_list:
            metadata_template_container_pk = str(metadata_template.metadata_template_container.pk)

            if metadata_template_container_pk in latest_version_for_container:
                raise ValidationError("Invalid duplicate metadata template container found in hierarchy")

            latest_version_for_container[metadata_template_container_pk] = metadata_template

        # We must find all metadata with a reference to a metadata template.
        # If the metadata template already is the latest version, we just process the related metadata without any
        # changes. If the metadata template is not the latest version, we must upgrade the metadata to the latest
        # version. Metadata without a reference to a metadata template will also be processed without any changes.
        for metadata in metadata_list:
            latest_viable_metadata_template = None
            if metadata.metadata_template_field:
                latest_viable_metadata_template = latest_version_for_container.get(
                    str(metadata.metadata_template_field.metadata_template.metadata_template_container.pk),
                )

            if (
                metadata.metadata_template_field
                and latest_viable_metadata_template
                and latest_viable_metadata_template.pk != metadata.metadata_template_field.metadata_template.pk
            ):
                # A newer version of the metadata template is available. We must upgrade the metadata attributes.
                # The latest version of the metadata template field will be used as a reference.
                # If the field does not exist in the latest metadata template anymore, we must delete the field
                # reference.
                latest_version_field = metadata.metadata_template_field.get_latest_version_field(
                    metadata_template=latest_viable_metadata_template,
                )
                metadata_template_field_exists = latest_viable_metadata_template.metadata_template_fields.filter(
                    pk=latest_version_field.pk,
                ).exists()
                old_field = metadata.field
                old_custom_key = metadata.custom_key
                old_field_type = metadata.field_type
                old_config = copy.deepcopy(metadata.config)
                old_value = copy.deepcopy(metadata.get_value())
                old_metadata_template_field = metadata.metadata_template_field
                old_previous_version_field = metadata.previous_version_field
                conflict_id = f"{metadata.pk}:{latest_version_field.pk}"
                conflict_error = None

                try:
                    converted_value = metadata.get_converted_value(latest_version_field.field_type)
                except ValidationError as e:
                    if not collect_conflicts:
                        raise

                    conflict_error = ", ".join(e.messages)
                    converted_value = get_metadata_structure_for_type(
                        latest_version_field.field_type,
                        None,
                    )

                metadata.field = latest_version_field.field
                metadata.custom_key = latest_version_field.custom_key
                metadata.field_type = latest_version_field.field_type
                metadata.config = latest_version_field.config
                metadata.value = converted_value
                metadata.metadata_template_field = latest_version_field if metadata_template_field_exists else None

                if not conflict_error:
                    try:
                        validate_metadata([metadata])
                    except ValidationError as e:
                        if not collect_conflicts:
                            raise

                        conflict_error = ", ".join(e.messages)
                        metadata.value = get_metadata_structure_for_type(
                            latest_version_field.field_type,
                            None,
                        )

                if conflict_error:
                    conflicts.append(
                        {
                            "conflict_id": conflict_id,
                            "error": conflict_error,
                            "old": {
                                "field": old_field,
                                "custom_key": old_custom_key,
                                "field_type": old_field_type,
                                "value": old_value,
                                "config": old_config,
                                "metadata_template_field": old_metadata_template_field,
                                "previous_version_field": (
                                    str(old_previous_version_field.pk) if old_previous_version_field else None
                                ),
                                "order": metadata.order,
                            },
                            "new": {
                                "field": latest_version_field.field,
                                "custom_key": latest_version_field.custom_key,
                                "field_type": latest_version_field.field_type,
                                "value": None,
                                "config": copy.deepcopy(latest_version_field.config),
                                "metadata_template_field": (
                                    latest_version_field if metadata_template_field_exists else None
                                ),
                                "previous_version_field": str(metadata.pk),
                                "order": metadata.order,
                            },
                            "metadata": metadata,
                        },
                    )

            if isinstance(metadata, Metadata):
                metadata.previous_version_field = metadata

            upgraded_metadata_list.append(metadata)

        # We must add completely new metadata template fields from active metadata templates
        for metadata_template in latest_metadata_templates:
            for metadata_template_field in metadata_template.metadata_template_fields.all():
                if not any(item.metadata_template_field == metadata_template_field for item in upgraded_metadata_list):
                    upgraded_metadata_list.append(metadata_template_field)

        # We must also add metadata template fields from completely new metadata templates if they are available.
        for metadata_template in active_metadata_templates:
            if metadata_template not in upgraded_metadata_templates_list:
                upgraded_metadata_templates_list.append(metadata_template)
                upgraded_metadata_list += metadata_template.metadata_template_fields.order_by(
                    "order",
                    "custom_key",
                )

        # Finally, check if a metadata has a reference to a metadata template container that has been totally removed
        # or exchanged for another one
        for metadata in upgraded_metadata_list:
            if (
                isinstance(metadata, Metadata)
                and metadata.metadata_template_field
                and metadata.metadata_template_field.metadata_template.metadata_template_container
                not in upgraded_metadata_template_container_list
            ):
                metadata.metadata_template_field = None

        order = 0
        for metadata in upgraded_metadata_list:
            metadata.order = order
            order += 1

        upgraded_metadata_templates_by_container = {
            str(metadata_template.metadata_template_container.pk): metadata_template
            for metadata_template in upgraded_metadata_templates_list
        }
        template_versions = []
        for container_pk in sorted(
            set(old_metadata_templates_by_container.keys()) | set(upgraded_metadata_templates_by_container.keys()),
        ):
            old_metadata_template = old_metadata_templates_by_container.get(container_pk)
            new_metadata_template = upgraded_metadata_templates_by_container.get(container_pk)
            template_versions.append(
                {
                    "container_pk": container_pk,
                    "old_template_pk": old_metadata_template.pk if old_metadata_template else None,
                    "new_template_pk": new_metadata_template.pk if new_metadata_template else None,
                },
            )

        return (
            upgraded_metadata_list,
            upgraded_metadata_templates_list,
            conflicts,
            template_versions,
            base_version_pk,
        )

    def get_upgraded_latest_version_metadata(self, max_upgrade=False) -> tuple[list, list]:
        upgraded_metadata_list, upgraded_metadata_templates_list, _, _, _ = (
            self._collect_upgraded_latest_version_metadata(
                max_upgrade=max_upgrade,
                collect_conflicts=False,
            )
        )
        return upgraded_metadata_list, upgraded_metadata_templates_list

    def get_upgraded_latest_version_metadata_with_conflicts(
        self,
        max_upgrade: bool = False,
    ) -> tuple[list, list, list[dict], list[dict], str | None]:
        return self._collect_upgraded_latest_version_metadata(
            max_upgrade=max_upgrade,
            collect_conflicts=True,
        )

    def upgrade_latest_version_metadata(self, max_upgrade=False) -> tuple[bool, str | None]:
        from fdm.uploads.helpers import create_uploads_version_with_new_metadata_for_dataset

        # If we don't refresh here, the data object will contain outdated references.
        self.refresh_from_db()

        if not self.is_latest_version_upgradeable(max_upgrade=max_upgrade):
            return True, None

        try:
            upgraded_metadata_list, upgraded_metadata_templates_list = self.get_upgraded_latest_version_metadata(
                max_upgrade=max_upgrade,
            )

            validate_metadata(upgraded_metadata_list)

            create_uploads_version_with_new_metadata_for_dataset(
                dataset=self,
                name=str(_("Automatic metadata update")),
                metadata_list=upgraded_metadata_list,
                link_metadata_templates=upgraded_metadata_templates_list,
                retain_existing_metadata=False,
            )
        except ValidationError as e:
            return False, "\n".join(e.messages)

        return True, None

    def get_all_metadata_template_fields(self, include_uploads_dataset_fields: bool = False):
        metadata_list = []

        if self.is_published():
            if self.folder.project.metadata_template:
                metadata_list += self.folder.project.metadata_template.metadata_template_fields.order_by(
                    "order",
                    "custom_key",
                )

            if self.folder.metadata_template:
                metadata_list += self.folder.metadata_template.metadata_template_fields.order_by(
                    "order",
                    "custom_key",
                )

        if include_uploads_dataset_fields and self.metadata_template:
            metadata_list += self.metadata_template.metadata_template_fields.order_by(
                "order",
                "custom_key",
            )

        order = 0
        for metadata in metadata_list:
            metadata.order = order
            order += 1

        return metadata_list

    def get_active_metadata_templates_for_published_state(self) -> list[MetadataTemplate]:
        metadata_templates = []

        if self.folder:
            if self.folder.project.metadata_template:
                metadata_templates.append(self.folder.project.metadata_template)

            if self.folder.metadata_template:
                metadata_templates.append(self.folder.metadata_template)

        if self.metadata_template:
            metadata_templates.append(self.metadata_template)

        return list({metadata_template for metadata_template in metadata_templates})

    def publish(self, folder: Folder | str = None, subfolder: Subfolder | str = None):
        from fdm.uploads.helpers import create_uploads_version_with_new_metadata_for_dataset

        if not folder and not self.folder:
            raise ValidationError(_("You can't publish a dataset without a folder."))

        if self.locked and not self.is_locked_by_myself():
            raise PermissionDenied(_("You must not edit an element which has been locked by another user."))

        if self.is_published():
            raise PermissionDenied(_("This dataset has already been published."))

        if folder:
            self.folder_id = folder.pk if isinstance(folder, Folder) else folder

        if subfolder:
            self.subfolder_id = subfolder.pk if isinstance(subfolder, Subfolder) else subfolder

        if not can_edit_in_folder(
            user=self.created_by,
            folder_pk=self.folder_id,
        ):
            raise PermissionDenied(
                _("You must not publish any uploads datasets into a folder you haven't got the permission to."),
            )

        with transaction.atomic():
            try:
                self.deletable_until_date = timezone.now() + timezone.timedelta(
                    minutes=int(get_dbsettings_value("DATASET_DELETABLE_WITHIN_THRESHOLD", None)),
                )
            except Exception:
                self.deletable_until_date = None

            self.expiry_date = None
            self.publication_date = timezone.now()
            self.save()

            latest_version = self.latest_version
            if latest_version:
                # On publish, sync only missing template fields and missing active template links.
                include_uploads_dataset_fields = bool(
                    self.metadata_template
                    and not latest_version.metadata_templates.filter(pk=self.metadata_template.pk).exists(),
                )

                metadata_list = self.get_all_metadata_template_fields(
                    include_uploads_dataset_fields=include_uploads_dataset_fields,
                )

                existing_template_field_ids = set(
                    latest_version.metadata.exclude(
                        metadata_template_field__isnull=True,
                    ).values_list(
                        "metadata_template_field_id",
                        flat=True,
                    ),
                )

                missing_metadata_list = [
                    metadata_template_field
                    for metadata_template_field in metadata_list
                    if metadata_template_field.pk not in existing_template_field_ids
                ]

                latest_version_metadata_template_ids = set(
                    latest_version.metadata_templates.values_list(
                        "pk",
                        flat=True,
                    ),
                )

                active_metadata_template_ids = {
                    metadata_template.pk
                    for metadata_template in self.get_active_metadata_templates_for_published_state()
                }

                missing_active_metadata_templates = active_metadata_template_ids - latest_version_metadata_template_ids

                if missing_metadata_list or missing_active_metadata_templates:
                    create_uploads_version_with_new_metadata_for_dataset(
                        dataset=self,
                        name=str(_("Automatically applied metadata template fields due to the publishing process")),
                        metadata_list=missing_metadata_list,
                        retain_existing_metadata=True,
                        link_currently_active_metadata_templates=True,
                    )

            for uploads_version in self.uploads_versions.order_by("creation_date"):
                if not uploads_version.is_published():
                    uploads_version.publish()

    def restore_version(self, uploads_version: any = None):
        if not uploads_version:
            raise ValidationError(_("You can't restore a specific uploads version without providing a primary key."))

        if self.locked and not self.is_locked_by_myself():
            raise PermissionDenied(_("You must not edit an element which has been locked by another user."))

        if self.uploads_versions.count() < 2:
            raise PermissionDenied(
                _("A dataset must have at least two versions before you can restore a specific version."),
            )

        if isinstance(uploads_version, str):
            try:
                uploads_version = UploadsVersion.objects.get(
                    pk=uploads_version,
                )
            except UploadsVersion.DoesNotExist:
                raise ValidationError(_("The version you want to restore does not exist."))

        if self.latest_version == uploads_version:
            raise PermissionDenied(
                _("You can't restore the latest version of a dataset."),
            )

        if uploads_version.dataset != self:
            raise PermissionDenied(_("The version you want to restore is not part of the dataset."))

        # TODO: In the future it should be possible to have versions without files. We'll need to change this then.
        if not uploads_version.version_file:
            raise PermissionDenied(_("The version you want to restore has no file attached to it."))

        new_uploads_version = UploadsVersion.objects.create(
            version_file=uploads_version.version_file,
            dataset=self,
            restored_uploads_version=uploads_version,
        )

        if uploads_version.metadata.exists():
            set_metadata_for_relation(
                metadata_list=uploads_version.metadata.all(),
                relation=new_uploads_version,
            )

        if self.is_published():
            new_uploads_version.publish()

        # Link all metadata templates from the old version to the new version
        new_uploads_version.metadata_templates.set(
            [metadata_template for metadata_template in uploads_version.metadata_templates.all()],
        )

        return new_uploads_version

    def update_uploads_versions_count(self) -> None:
        new_count = self.uploads_versions.count()

        # We must use .update() to avoid running into a lock
        UploadsDataset.objects.filter(pk=self.pk).update(uploads_versions_count=new_count)
        self.uploads_versions_count = new_count

    def __str__(self):
        return f"{self.display_name or self.name or self.id}"

    def get_available_metadata_template_container(self) -> QuerySet:
        return MetadataTemplateContainer.objects.filter(
            # Metadata template container assigned to the dataset itself
            Q(
                assigned_to_content_type=self.get_content_type(),
                assigned_to_object_id=self.pk,
            )
            # Metadata template container assigned to the folder the dataset is part of
            | Q(
                assigned_to_content_type=self.folder.get_content_type(),
                assigned_to_object_id=self.folder.pk,
            )
            # Metadata template container assigned to the project the dataset is part of
            | Q(
                assigned_to_content_type=self.folder.project.get_content_type(),
                assigned_to_object_id=self.folder.project.pk,
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
            # Metadata templates assigned to the dataset itself
            Q(
                metadata_template_container__assigned_to_content_type=self.get_content_type(),
                metadata_template_container__assigned_to_object_id=self.pk,
            )
            # Metadata templates assigned to the folder the dataset is part of
            | Q(
                metadata_template_container__assigned_to_content_type=self.folder.get_content_type(),
                metadata_template_container__assigned_to_object_id=self.folder.pk,
            )
            # Metadata templates assigned to the project the dataset is part of
            | Q(
                metadata_template_container__assigned_to_content_type=self.folder.project.get_content_type(),
                metadata_template_container__assigned_to_object_id=self.folder.project.pk,
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

    def delete(self, *args, **kwargs):
        from django.contrib.auth import get_user_model
        from django.contrib.contenttypes.models import ContentType

        current_user = get_current_user()
        if not getattr(current_user, "can_hard_delete_datasets", False):
            if self.created_by != current_user:
                raise PermissionDenied(
                    _("You must not delete an uploads dataset which has been created by another user."),
                )

        file_ids = list(self.uploads_versions.values_list("version_file_id", flat=True))
        has_file_size_metadata = False
        user_ids = set(
            self.uploads_versions.exclude(version_file__created_by__isnull=True).values_list(
                "version_file__created_by_id",
                flat=True,
            ),
        )

        if file_ids:
            has_file_size_metadata = Metadata.objects.filter(
                assigned_to_content_type=ContentType.objects.get_for_model(UploadsVersionFile),
                assigned_to_object_id__in=file_ids,
                custom_key__iexact="FILE_SIZE",
            ).exists()

        # Signal to post_delete handlers that the dataset itself is being deleted,
        # so they skip the now-pointless uploads_versions_count update.
        self._is_being_deleted = True

        # Atomic cascade: if any pre_delete signal on a version/version_file raises,
        # the whole operation is rolled back instead of leaving a dataset row with
        # partially deleted versions (the symptom users observed as a 404 on detail).
        with transaction.atomic():
            for uploads_version in self.uploads_versions.all():
                uploads_version.dataset = self
                uploads_version.delete()

            super().delete(*args, **kwargs)

        if has_file_size_metadata:
            if self.folder:
                self.folder.update_storage_size()

            if user_ids:
                user_model = get_user_model()
                for user in user_model.objects.filter(pk__in=user_ids):
                    user.update_storage_size()

    def clean(self):
        if self.subfolder and self.subfolder.folder_id != self.folder_id:
            raise ValidationError(_("The subfolder must belong to the folder of the dataset."))

        # Make sure that the metadata template container of the active metadata template is not already an active
        # metadata template container elsewhere in the hierarchy of project > folder > dataset.
        if (
            self.metadata_template
            and self.folder
            and (
                (
                    self.folder.metadata_template
                    and self.folder.metadata_template.metadata_template_container
                    == self.metadata_template.metadata_template_container
                )
                or (
                    self.folder.project.metadata_template
                    and self.folder.project.metadata_template.metadata_template_container
                    == self.metadata_template.metadata_template_container
                )
            )
        ):
            raise ValidationError(
                _("A metadata template can only be actively assigned to one instance in the hierarchy at a time"),
            )


class UploadsVersionFile(BaseModel, TimestampMixin, ByUserMixin):
    class Status(models.TextChoices):
        SCHEDULED = "SCHEDULED", _("Scheduled")
        PROCESSED = "PROCESSED", _("Processed")
        IN_PROGRESS = "IN_PROGRESS", _("In progress")
        ERROR = "ERROR", _("Error")
        FINISHED = "FINISHED", _("Finished")

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    uploaded_file = DynamicRuntimeStorageFileField(
        max_length=4096,
        upload_to=get_upload_to_path,
        storage_instance_callable=get_storage,
        blank=True,
        null=True,
    )

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.SCHEDULED,
    )

    processing_lease_token = models.UUIDField(
        blank=True,
        null=True,
    )

    processing_lease_expires_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    processing_heartbeat_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    uploaded_using_tus = models.BooleanField(
        default=False,
    )

    is_referenced = models.BooleanField(
        default=False,
    )

    storage_relocating = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.FINISHED,
    )

    publication_date = models.DateTimeField(
        verbose_name=_("Publication date"),
        blank=True,
        null=True,
    )

    metadata = GenericRelation(
        Metadata,
        content_type_field="assigned_to_content_type",
        object_id_field="assigned_to_object_id",
    )

    @property
    def file_size(self) -> int:
        return self.uploaded_file.size

    @property
    def absolute_path(self) -> str:
        return os.path.dirname(self.uploaded_file.path)

    @property
    def relative_path(self) -> str:
        return os.path.dirname(self.uploaded_file.name)

    @property
    def name(self) -> str:
        return os.path.basename(self.uploaded_file.name)

    def is_published(self) -> bool:
        return self.publication_date is not None

    is_published.boolean = True
    is_published.short_description = "Is published"

    def publish(self):
        if self.is_published():
            raise PermissionDenied(_("This version file has already been published."))

        self.publication_date = timezone.now()
        self.storage_relocating = self.Status.SCHEDULED
        self.save()

    def move_file(self):
        from fdm.uploads.handlers import move_storage_file

        self.storage_relocating = self.Status.IN_PROGRESS
        self.save()

        try:
            move_storage_file(self.get_storage(), self)
            self.storage_relocating = self.Status.FINISHED
            if not self.status == self.Status.FINISHED:
                self.status = self.Status.SCHEDULED
        except Exception as e:
            self.storage_relocating = self.Status.ERROR
            logger.error(f"Failed to move file to new storage location: {e}")

        self.save()

    def get_folder(self):
        uploads_version = self.uploads_versions.first()

        if uploads_version and uploads_version.dataset:
            return uploads_version.dataset.folder

        return None

    def get_project(self):
        folder = self.get_folder()

        if folder:
            return folder.project

        return None

    def get_storage(self):
        folder = self.get_folder()

        if folder:
            return folder.storage

        return None

    def __str__(self):
        return f"{self.name or self.id}"

    def delete(self, *args, **kwargs):
        if self.metadata.exists():
            for metadata in self.metadata.all():
                metadata.delete()

        # Filesystem removal is delegated to the post_delete signal handler, which
        # wraps os.remove in transaction.on_commit so the file on disk is only
        # removed once the enclosing DB transaction actually commits. Doing the
        # removal here would escape the enclosing atomic() and leave orphaned
        # state if a cascade later rolls back.
        super().delete(*args, **kwargs)


class UploadsVersion(BaseModel, TimestampMixin, ByUserMixin):
    class Status(models.TextChoices):
        SCHEDULED = "SCHEDULED", _("Scheduled")
        IN_PROGRESS = "IN_PROGRESS", _("In progress")
        ERROR = "ERROR", _("Error")
        FINISHED = "FINISHED", _("Finished")

    objects = UploadsVersionManager()

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    name = models.CharField(
        max_length=255,
        blank=True,
        null=True,
    )

    dataset = models.ForeignKey(
        UploadsDataset,
        blank=True,
        null=True,
        related_name="uploads_versions",
        on_delete=models.CASCADE,
    )

    version_file = models.ForeignKey(
        UploadsVersionFile,
        blank=True,
        null=True,
        related_name="uploads_versions",
        on_delete=models.CASCADE,
    )

    publication_date = models.DateTimeField(
        verbose_name=_("Publication date"),
        blank=True,
        null=True,
    )

    metadata = GenericRelation(
        Metadata,
        content_type_field="assigned_to_content_type",
        object_id_field="assigned_to_object_id",
    )

    metadata_is_complete = models.BooleanField(
        default=True,
    )

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.SCHEDULED,
    )

    metadata_templates = models.ManyToManyField(
        MetadataTemplate,
        blank=True,
        related_name="uploads_versions",
    )

    restored_uploads_version = models.ForeignKey(
        "UploadsVersion",
        blank=True,
        null=True,
        related_name="uploads_version_restored_to",
        on_delete=models.SET_NULL,
    )

    @property
    def locked(self):
        return self.dataset.locked

    @property
    def locked_by(self):
        return self.dataset.locked_by

    @property
    def locked_at(self):
        return self.dataset.locked_at

    def is_published(self) -> bool:
        return self.publication_date is not None

    is_published.boolean = True
    is_published.short_description = "Is published"

    def is_latest_version(self) -> bool:
        return self.dataset and self.dataset.latest_version.pk == self.pk

    is_latest_version.boolean = True
    is_latest_version.short_description = "Is latest version in dataset"

    def link_currently_active_metadata_templates(self) -> None:
        for metadata_template in self.dataset.get_active_metadata_templates_for_published_state():
            self.metadata_templates.add(metadata_template)

    def check_metadata_completeness(self) -> bool:
        if not self.metadata_templates.exists():
            return True

        query = Q()

        for metadata_template in self.metadata_templates.all():
            query = query | Q(
                metadata_template=metadata_template,
                mandatory=True,
            )

        mandatory_metadata_template_fields = MetadataTemplateField.objects.filter(query)

        if not mandatory_metadata_template_fields.exists():
            return True

        for field in mandatory_metadata_template_fields.all():
            # We use filter here because somehow we could end up having multiple fields of the same relation
            # in a version
            metadata_instances = self.metadata.filter(metadata_template_field=field)

            if not metadata_instances.exists():
                return False

            for metadata_instance in metadata_instances:
                if not check_empty_value(metadata_instance.get_value()):
                    return False

                return metadata_instance.is_valid()

        return True

    def get_all_metadata(self):
        metadata = list(self.metadata.all())

        try:
            if self.version_file_id:
                metadata += list(self.version_file.metadata.all())

                # TODO: In the future it should be possible to have versions without files. We'll need to change this then.
                version_file_folder = self.version_file.get_folder()
                if version_file_folder:
                    metadata += list(version_file_folder.metadata.all())

                # TODO: In the future it should be possible to have versions without files. We'll need to change this then.
                version_file_project = self.version_file.get_project()
                if version_file_project:
                    metadata += list(version_file_project.metadata.all())
        except UploadsVersionFile.DoesNotExist:
            pass

        return metadata

    def publish(self):
        if self.dataset.locked and not self.dataset.is_locked_by_myself():
            raise PermissionDenied(_("You must not edit an element which has been locked by another user."))

        if self.is_published():
            raise PermissionDenied(_("This version has already been published."))

        if not self.version_file:
            raise ValidationError(_("The version has no file attached and cannot be published."))

        self.publication_date = timezone.now()
        self.save()

        if not self.version_file.is_published():
            self.version_file.publish()

    def __str__(self):
        return f"{self.dataset}: {self.name or self.id}"

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            folder = None
            user = None
            should_recompute = False

            if self.version_file_id:
                try:
                    should_recompute = self.version_file.metadata.filter(custom_key__iexact="FILE_SIZE").exists()
                    folder = self.version_file.get_folder()
                    user = self.version_file.created_by
                except UploadsVersionFile.DoesNotExist:
                    pass

            super().delete(*args, **kwargs)

            if self.version_file_id:
                try:
                    self.version_file.delete()
                except UploadsVersionFile.DoesNotExist:
                    pass

            if should_recompute:
                if folder:
                    folder.update_storage_size()

                if user:
                    user.update_storage_size()
