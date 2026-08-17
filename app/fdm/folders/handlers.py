from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _
from django_userforeignkey.request import get_current_user

from fdm.core.handlers import is_cascading_delete
from fdm.core.rest.permissions import is_project_admin, is_project_metadata_template_admin
from fdm.folders.helpers import set_folder_admin_permissions
from fdm.folders.models import Folder, FolderPermission
from fdm.metadata.models import MetadataTemplate
from fdm.projects.models import Project, ProjectMembership
from fdm.uploads.models import UploadsDataset, UploadsVersion, UploadsVersionFile
from fdm.uploads.tasks import schedule_metadata_template_upgrade_availability


def folder_is_empty(folder):
    return not UploadsDataset.objects.filter(
        folder=folder,
    ).exists()


def is_last_folder_admin(instance) -> bool:
    if not instance.is_folder_admin:
        return False

    return (
        FolderPermission.objects.filter(
            folder=instance.folder,
            is_folder_admin=True,
        ).count()
        == 1
    )


@receiver(pre_save, sender=FolderPermission)
def prevent_downgrade_of_project_admin(sender, instance, *args, **kwargs):
    # If the folder permission is created, then we don't need to check anything
    if instance.pk is None:
        return

    if is_project_admin(instance.project_membership.member, instance.folder.project):
        set_folder_admin_permissions(instance)


@receiver(pre_save, sender=FolderPermission)
def prevent_downgrade_of_project_metadata_template_admin(sender, instance, *args, **kwargs):
    # If the folder permission is created, then we don't need to check anything
    if instance.pk is None:
        return

    if is_project_metadata_template_admin(instance.project_membership.member, instance.folder.project):
        instance.is_metadata_template_admin = True


@receiver(pre_delete, sender=FolderPermission)
def prevent_removal_of_admins(sender, instance, *args, **kwargs):
    if is_cascading_delete():
        return

    if folder_is_empty(instance.folder):
        return

    if is_last_folder_admin(instance):
        raise PermissionDenied(_("You must not delete the last folder admin."))

    if is_project_admin(instance.project_membership.member, instance.folder.project):
        raise PermissionDenied(_("You must not delete project admins."))


@receiver(pre_save, sender=FolderPermission)
def prevent_downgrade_of_last_folder_admin(sender, instance, *args, **kwargs):
    # If the folder permission is created, then we don't need to check anything
    if instance.pk is None:
        return

    if folder_is_empty(instance.folder):
        return

    if is_last_folder_admin(instance):
        set_folder_admin_permissions(instance)


@receiver(pre_save, sender=Folder)
def pre_save_folder_description(sender, instance, *args, **kwargs):
    if not instance.description:
        instance.description = {}


@receiver(pre_save, sender=Folder)
def pre_save_folder_metadata_template(sender, instance, *args, **kwargs):
    # If the instance is created, then we don't need to check anything
    if instance.pk is None:
        return

    # Get the old value for this instance field if it already exists, else abort
    try:
        old_instance = Folder.objects.get(pk=instance.pk)
    except Folder.DoesNotExist:
        return

    # Check if the value has been changed for this instance field
    if old_instance.metadata_template == instance.metadata_template:
        return

    instance._schedule_metadata_template_upgrade_availability = True


@receiver(post_save, sender=Folder)
def post_save_folder_metadata_template(sender, instance, *args, **kwargs):
    if not getattr(instance, "_schedule_metadata_template_upgrade_availability", False):
        return

    del instance._schedule_metadata_template_upgrade_availability
    dataset_ids = UploadsDataset.objects.filter(
        folder=instance,
    ).values_list("pk", flat=True)
    schedule_metadata_template_upgrade_availability(dataset_ids)


@receiver(pre_delete, sender=Folder)
def pre_delete(sender, instance, **kwargs):
    # Folder must not be deleted if there are datasets linked to it
    datasets = UploadsDataset.objects.filter(
        folder=instance,
    )

    if datasets.exists():
        raise PermissionDenied(_("You must not delete folders with datasets in them."))


@receiver(post_save, sender=Project)
def add_default_folder_and_permissions_after_creating_a_project(sender, instance, created, *args, **kwargs):
    if not created:
        return

    ProjectMembership.objects.create(
        project=instance,
        member=instance.created_by,
        is_project_admin=True,
        is_metadata_template_admin=True,
        can_create_folders=True,
    )

    Folder.objects.create(
        name=_("General"),
        project=instance,
    )


@receiver(post_save, sender=Folder)
def add_default_folder_permissions_after_creating_a_folder(sender, instance, created, *args, **kwargs):
    if not created:
        return

    project_memberships = ProjectMembership.objects.filter(
        Q(project=instance.project) & (Q(member=instance.created_by) | Q(is_project_admin=True)),
    )

    for project_membership in project_memberships:
        try:
            FolderPermission.objects.create(
                folder=instance,
                project_membership=project_membership,
                is_folder_admin=True,
                is_metadata_template_admin=True,
                can_edit=True,
            )
        except ValidationError:
            pass


@receiver(pre_save, sender=FolderPermission)
def add_other_folder_permission_flags_when_admin_is_newly_set(sender, instance, *args, **kwargs):
    if not instance.is_folder_admin:
        return

    try:
        original_instance = FolderPermission.objects.get(
            pk=instance.pk,
        )

        if original_instance.is_folder_admin != instance.is_folder_admin:
            set_folder_admin_permissions(instance)
    except FolderPermission.DoesNotExist:
        pass


@receiver(pre_save, sender=FolderPermission)
def prevent_taking_of_other_folder_permission_flags_when_user_is_admin(sender, instance, *args, **kwargs):
    if instance.is_folder_admin:
        set_folder_admin_permissions(instance)


@receiver(post_save, sender=FolderPermission)
def post_save_folder_permission_update_members_count(sender, instance, *args, **kwargs):
    instance.folder.update_members_count()


@receiver(post_delete, sender=FolderPermission)
def post_delete_folder_permission_update_members_count(sender, instance, *args, **kwargs):
    instance.folder.update_members_count()


@receiver(post_save, sender=UploadsDataset)
def post_save_dataset_update_datasets_count(sender, instance, *args, **kwargs):
    if instance.folder:
        instance.folder.update_datasets_count()


@receiver(post_delete, sender=UploadsDataset)
def post_delete_dataset_update_datasets_count(sender, instance, *args, **kwargs):
    if instance.folder:
        instance.folder.update_datasets_count()


@receiver(pre_save, sender=UploadsDataset)
def pre_save_dataset_update_display_name(sender, instance, *args, **kwargs):
    instance.display_name = instance.get_display_name()


@receiver(post_save, sender=UploadsVersion)
def post_save_uploads_version_update_dataset_display_name(sender, instance, *args, **kwargs):
    instance.dataset.set_display_name()


@receiver(post_save, sender=UploadsVersionFile)
def post_save_uploads_version_file_update_dataset_display_name(sender, instance, *args, **kwargs):
    for uploads_version in instance.uploads_versions.all():
        uploads_version.dataset.set_display_name()


@receiver(post_save, sender=FolderPermission)
def send_email_notification_for_new_folder_permission(sender, instance, created, **kwargs):
    if created and instance.project_membership.member != get_current_user():
        instance.project_membership.member.send_email_notification_for_new_folder_permission(
            folder_permission=instance,
        )


@receiver(pre_save, sender=FolderPermission)
def send_email_notification_for_changed_folder_permission(sender, instance, **kwargs):
    # If the folder permission is created, then we don't need to check anything
    if instance.pk is None:
        return

    # Get the old value for this folder permission if it already exists, else abort
    try:
        folder_permission = FolderPermission.objects.get(pk=instance.pk)
    except FolderPermission.DoesNotExist:
        return

    if folder_permission.member == get_current_user():
        return

    if folder_permission.is_folder_admin != instance.is_folder_admin or folder_permission.can_edit != instance.can_edit:
        instance.project_membership.member.send_email_notification_for_changed_folder_permission(
            folder_permission=instance,
        )


@receiver(post_delete, sender=FolderPermission)
def send_email_notification_for_deleted_folder_permission(sender, instance, *args, **kwargs):
    if instance.project_membership.member != get_current_user():
        instance.project_membership.member.send_email_notification_for_deleted_folder_permission(
            folder_permission=instance,
        )


@receiver(post_save, sender=MetadataTemplate)
def post_save_metadata_template_update_folder_metadata_templates_count(sender, instance, *args, **kwargs):
    # We are currently intentionally ignoring global templates
    if instance.metadata_template_container and isinstance(
        instance.metadata_template_container.assigned_to_content_object,
        Folder,
    ):
        instance.metadata_template_container.assigned_to_content_object.refresh_from_db()
        instance.metadata_template_container.assigned_to_content_object.update_metadata_templates_count()


@receiver(post_delete, sender=MetadataTemplate)
def post_delete_metadata_template_update_folder_metadata_templates_count(sender, instance, *args, **kwargs):
    # We are currently intentionally ignoring global templates
    if instance.metadata_template_container and isinstance(
        instance.metadata_template_container.assigned_to_content_object,
        Folder,
    ):
        instance.metadata_template_container.assigned_to_content_object.refresh_from_db()
        instance.metadata_template_container.assigned_to_content_object.update_metadata_templates_count()
