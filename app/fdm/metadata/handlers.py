from django.core.exceptions import PermissionDenied
from django.db import IntegrityError
from django.db.models import Q
from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _

from fdm.metadata.enums import MetadataFieldType
from fdm.metadata.models import (
    Metadata,
    MetadataField,
    MetadataTag,
    MetadataTemplate,
    MetadataTemplateContainer,
    MetadataTemplateField,
)
from fdm.uploads.models import UploadsDataset
from fdm.uploads.tasks import schedule_metadata_template_upgrade_availability


@receiver(pre_save, sender=MetadataField)
def pre_save_metadata_field_update_field_type(sender, instance, *args, **kwargs):
    # If the metadata field is created, then we don't need to check anything
    if instance.pk is None:
        return

    # Get the old value for this metadata field if it already exists, else abort
    try:
        old_metadata_field_data = MetadataField.objects.get(pk=instance.pk)
    except MetadataField.DoesNotExist:
        return

    # Check if the field type has been changed for this metadata field
    if old_metadata_field_data.field_type == instance.field_type:
        return

    if instance.metadata.exists() or instance.metadata_template_fields.exists():
        raise PermissionDenied(
            _("You must not change field types of metadata template fields that are already in use."),
        )


@receiver(pre_delete, sender=MetadataField)
def pre_delete_metadata_field(sender, instance, **kwargs):
    if instance.metadata.exists() or instance.metadata_template_fields.exists():
        raise PermissionDenied(_("You must not delete metadata fields that are already in use."))


@receiver(pre_save, sender=Metadata)
def pre_save_metadata_update_field_type(sender, instance, *args, **kwargs):
    if instance.field:
        instance.field_type = instance.field.field_type


@receiver(post_save, sender=Metadata)
def post_save_metadata_update_dataset_display_name(sender, instance, *args, **kwargs):
    if hasattr(instance.assigned_to_content_object, "uploads_versions"):
        for uploads_version in instance.assigned_to_content_object.uploads_versions.all():
            uploads_version.dataset.set_display_name()


@receiver(post_delete, sender=Metadata)
def post_delete_metadata_update_dataset_display_name(sender, instance, *args, **kwargs):
    if hasattr(instance.assigned_to_content_object, "uploads_versions"):
        for uploads_version in instance.assigned_to_content_object.uploads_versions.all():
            uploads_version.dataset.set_display_name()


@receiver(pre_save, sender=Metadata)
def pre_save_metadata_config(sender, instance, *args, **kwargs):
    if not instance.config:
        instance.config = {}


@receiver(pre_save, sender=MetadataTemplateField)
def pre_save_metadata_template_field_config(sender, instance, *args, **kwargs):
    if not instance.config:
        instance.config = {}


@receiver(pre_delete, sender=MetadataTemplateContainer)
def pre_delete_metadata_template_container(sender, instance, **kwargs):
    # Only allow empty container elements to be deleted
    if instance.metadata_templates.exists():
        raise PermissionDenied(
            _("You must not delete metadata template container elements with metadata templates in them."),
        )


@receiver(pre_delete, sender=MetadataTemplate)
def pre_delete_metadata_template(sender, instance, **kwargs):
    raise PermissionDenied(_("You must not delete metadata templates."))


@receiver(pre_save, sender=MetadataTemplate)
def auto_create_metadata_template_container_for_metadata_template(sender, instance, **kwargs):
    # Every metadata template must have a metadata template container attached to it.
    # If it is missing, one will be created automatically.
    if not instance.metadata_template_container:
        name = instance.name

        # Apply metadata template name to container and let metadata template version name empty
        instance.metadata_template_container = MetadataTemplateContainer.objects.create(
            name=name,
        )
        instance.name = None


@receiver(post_save, sender=MetadataTemplate)
def post_save_metadata_template_set_upgradeable_flag(sender, instance, created, **kwargs):
    if not created:
        return

    # Schedule all datasets to be analyzed if a project, folder, or dataset itself is linked to a related template
    datasets = UploadsDataset.objects.filter(
        Q(folder__project__metadata_template__metadata_template_container=instance.metadata_template_container)
        | Q(folder__metadata_template__metadata_template_container=instance.metadata_template_container)
        | Q(metadata_template__metadata_template_container=instance.metadata_template_container),
    ).distinct()
    dataset_ids = list(datasets.values_list("pk", flat=True))
    schedule_metadata_template_upgrade_availability(dataset_ids)


def _resolve_folder_from_metadata(instance):
    from fdm.folders.models import Folder
    from fdm.uploads.models import UploadsVersion, UploadsVersionFile

    target = instance.assigned_to_content_object
    if target is None:
        return None

    if isinstance(target, Folder):
        return target
    elif isinstance(target, UploadsVersion):
        if target.dataset and target.dataset.folder:
            return target.dataset.folder
    elif isinstance(target, UploadsVersionFile):
        first_version = target.uploads_versions.first()
        if first_version and first_version.dataset and first_version.dataset.folder:
            return first_version.dataset.folder

    return None


@receiver(post_save, sender=Metadata)
def post_save_metadata_sync_tag_values(sender, instance, *args, **kwargs):
    if instance.field_type != MetadataFieldType.TAG:
        return

    folder = _resolve_folder_from_metadata(instance)
    if folder is None:
        return

    tag_values = instance.get_value()
    if not tag_values or not isinstance(tag_values, list):
        return

    for tag_value in tag_values:
        if not tag_value:
            continue
        try:
            MetadataTag.objects.get_or_create(
                folder=folder,
                value=str(tag_value),
            )
        except IntegrityError:
            pass
