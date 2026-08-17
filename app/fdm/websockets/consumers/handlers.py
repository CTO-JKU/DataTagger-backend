import copy

from channels.layers import get_channel_layer
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django_userforeignkey.request import get_current_user

from fdm.core.helpers import get_content_type_for_model, send_websocket_message
from fdm.folders.models import Folder
from fdm.metadata.models import MetadataTemplateContainer
from fdm.projects.models import Project
from fdm.uploads.models import UploadsDataset, UploadsVersionFile

__all__ = [
    "send_model_changed_websocket_message",
]


def websockets_instance_has_changes(original_instance, new_instance, relevant_fields):
    for field_name in relevant_fields:
        if getattr(original_instance, field_name) != getattr(new_instance, field_name):
            return True
    return False


@receiver(pre_save, sender=MetadataTemplateContainer)
@receiver(pre_save, sender=Project)
@receiver(pre_save, sender=Folder)
@receiver(pre_save, sender=UploadsDataset)
def websockets_instance_pre_save(sender, instance, **kwargs):
    if instance.pk:
        try:
            original_instance = sender.objects.get(pk=instance.pk)
            instance._original_instance = copy.deepcopy(original_instance)
        except sender.DoesNotExist:
            instance._original_instance = None
    else:
        instance._original_instance = None


def send_model_changed_websocket_message(instance):
    channel_layer = get_channel_layer()

    if channel_layer:
        content_type = get_content_type_for_model(instance.__class__).lower()
        model_name = content_type.split(".")[1]
        group_name = f"{model_name}_{instance.pk}"
        current_user = get_current_user()

        send_websocket_message(
            channel_layer,
            group_name,
            {
                "type": "model_changed",
                "data": {
                    "content_type": model_name,
                    "pk": str(instance.pk),
                    "edited_by": current_user.email if current_user.is_authenticated else None,
                },
            },
        )


@receiver(post_save, sender=MetadataTemplateContainer)
def metadata_template_container_changed_post_save(sender, instance, created, **kwargs):
    original_instance = getattr(instance, "_original_instance", None)
    relevant_fields = (
        "name",
        "metadata_templates",
        "latest_metadata_template",
    )

    if original_instance and websockets_instance_has_changes(original_instance, instance, relevant_fields):
        send_model_changed_websocket_message(instance)


@receiver(post_save, sender=Project)
def project_changed_post_save(sender, instance, created, **kwargs):
    original_instance = getattr(instance, "_original_instance", None)
    relevant_fields = (
        "name",
        "description",
        "metadata_template",
        "members_count",
    )

    if original_instance and websockets_instance_has_changes(original_instance, instance, relevant_fields):
        send_model_changed_websocket_message(instance)


@receiver(post_save, sender=Folder)
def folder_changed_post_save(sender, instance, created, **kwargs):
    original_instance = getattr(instance, "_original_instance", None)
    relevant_fields = (
        "name",
        "project",
        "storage",
        "metadata_template",
        "datasets_count",
        "members_count",
    )

    if original_instance and websockets_instance_has_changes(original_instance, instance, relevant_fields):
        send_model_changed_websocket_message(instance)


@receiver(post_save, sender=UploadsDataset)
def uploads_dataset_changed_post_save(sender, instance, created, **kwargs):
    original_instance = getattr(instance, "_original_instance", None)
    relevant_fields = (
        "name",
        "display_name",
        "folder",
        "uploads_versions",
        "latest_version",
        "is_published",
        "publication_date",
        "is_expired",
        "expiry_date",
    )

    if original_instance and websockets_instance_has_changes(original_instance, instance, relevant_fields):
        send_model_changed_websocket_message(instance)


@receiver(pre_save, sender=UploadsVersionFile)
def uploads_version_file_parser_status_has_changed(instance, *args, **kwargs):
    existing_element = UploadsVersionFile.objects.filter(pk=instance.pk).first()

    # ignore elements where the status has not changed
    if existing_element and existing_element.status == instance.status:
        return

    channel_layer = get_channel_layer()
    if channel_layer:
        content_type = get_content_type_for_model(instance.__class__).lower()
        model_name = content_type.split(".")[1]
        group_name = f"{model_name}_{instance.pk}"
        send_websocket_message(
            channel_layer,
            group_name,
            {
                "type": "parser_status_changed",
                "data": {
                    "content_type": model_name,
                    "pk": str(instance.pk),
                    "status": str(instance.status),
                },
            },
        )
