from django.core.exceptions import PermissionDenied
from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _
from django_userforeignkey.request import get_current_user

from fdm.folders.helpers import set_folder_admin_permissions
from fdm.folders.models import Folder, FolderPermission
from fdm.folders.signals import folder_datasets_count_updated
from fdm.metadata.models import MetadataTemplate
from fdm.projects.helpers import set_project_admin_permissions
from fdm.projects.models import Project, ProjectMembership
from fdm.uploads.models import UploadsDataset
from fdm.uploads.tasks import schedule_metadata_template_upgrade_availability


def is_last_project_admin(instance) -> bool:
    if not instance.is_project_admin:
        return False

    return (
        ProjectMembership.objects.filter(
            project=instance.project,
            is_project_admin=True,
        ).count()
        == 1
    )


def project_is_empty(instance):
    return not UploadsDataset.objects.filter(
        folder__project=instance.project,
    ).exists()


@receiver(pre_delete, sender=ProjectMembership)
def prevent_deletion_of_last_project_admin(sender, instance, *args, **kwargs):
    if project_is_empty(instance):
        return

    if is_last_project_admin(instance):
        raise PermissionDenied(_("You must not delete the last project admin."))


@receiver(pre_save, sender=ProjectMembership)
def prevent_update_removal_of_last_project_admin(sender, instance, *args, **kwargs):
    try:
        original_membership = ProjectMembership.objects.get(pk=instance.pk)
    except ProjectMembership.DoesNotExist:
        return

    if (
        original_membership.is_project_admin
        and not instance.is_project_admin
        and is_last_project_admin(original_membership)
    ):
        raise PermissionDenied(_("You must not delete the last project admin."))


@receiver(pre_delete, sender=Project)
def pre_delete(sender, instance, **kwargs):
    # Project must not be deleted if there are folders linked to it that are not empty.
    datasets = UploadsDataset.objects.filter(
        folder__project=instance,
    )

    if datasets.exists():
        raise PermissionDenied(_("You must not delete projects with datasets in them."))


@receiver(pre_save, sender=Project)
def pre_save_project_description(sender, instance, *args, **kwargs):
    if not instance.description:
        instance.description = {}


@receiver(pre_save, sender=Project)
def pre_save_project_metadata_template(sender, instance, *args, **kwargs):
    # If the instance is created, then we don't need to check anything
    if instance.pk is None:
        return

    # Get the old value for this instance field if it already exists, else abort
    try:
        old_instance = Project.objects.get(pk=instance.pk)
    except Project.DoesNotExist:
        return

    # Check if the value has been changed for this instance field
    if old_instance.metadata_template == instance.metadata_template:
        return

    instance._schedule_metadata_template_upgrade_availability = True


@receiver(post_save, sender=Project)
def post_save_project_metadata_template(sender, instance, *args, **kwargs):
    if not getattr(instance, "_schedule_metadata_template_upgrade_availability", False):
        return

    del instance._schedule_metadata_template_upgrade_availability
    dataset_ids = UploadsDataset.objects.filter(
        folder__project=instance,
    ).values_list("pk", flat=True)
    schedule_metadata_template_upgrade_availability(dataset_ids)


@receiver(pre_save, sender=ProjectMembership)
def add_other_project_membership_flags_when_admin_is_newly_set(sender, instance, *args, **kwargs):
    if not instance.is_project_admin:
        return

    original_instance = ProjectMembership.objects.filter(pk=instance.pk).first()

    if original_instance and original_instance.is_project_admin != instance.is_project_admin:
        set_project_admin_permissions(instance)


@receiver(pre_save, sender=ProjectMembership)
def prevent_taking_of_other_project_membership_flags_when_user_is_admin(sender, instance, *args, **kwargs):
    if instance.is_project_admin:
        set_project_admin_permissions(instance)


@receiver(post_save, sender=ProjectMembership)
def post_save_project_membership_update_folder_permissions(sender, instance, *args, **kwargs):
    if not instance.is_project_admin:
        return

    for folder in instance.project.folder.all():
        try:
            folder_permission = FolderPermission.objects.get(
                folder=folder,
                project_membership=instance,
            )
            set_folder_admin_permissions(instance)
            folder_permission.save()
        except FolderPermission.DoesNotExist:
            FolderPermission.objects.create(
                folder=folder,
                project_membership=instance,
                is_folder_admin=True,
                is_metadata_template_admin=True,
                can_edit=True,
            )


@receiver(post_save, sender=ProjectMembership)
def post_save_project_membership_update_folder_metadata_template_admin_permissions(sender, instance, *args, **kwargs):
    for folder in instance.project.folder.filter(folderpermission__project_membership__member=instance.member):
        try:
            folder_permission = FolderPermission.objects.get(
                folder=folder,
                project_membership=instance,
            )
            folder_permission.is_metadata_template_admin = True
            folder_permission.save()
        except FolderPermission.DoesNotExist:
            pass


@receiver(post_delete, sender=ProjectMembership)
def remove_folder_permissions_after_deleting_a_project_membership(sender, instance, *args, **kwargs):
    folder_permissions = FolderPermission.objects.filter(
        project_membership=instance,
    )

    for folder_permission in folder_permissions:
        folder_permission.delete()


@receiver(post_save, sender=ProjectMembership)
def post_save_project_membership_update_members_count(sender, instance, *args, **kwargs):
    instance.project.update_members_count()


@receiver(post_delete, sender=ProjectMembership)
def post_delete_project_membership_update_members_count(sender, instance, *args, **kwargs):
    instance.project.update_members_count()


@receiver(post_save, sender=Project)
def send_email_notification_for_new_project(sender, instance, created, **kwargs):
    if created:
        for project_membership in instance.project_members.exclude(member=get_current_user()):
            project_membership.member.send_email_notification_for_new_project(
                project=project_membership.project,
            )


@receiver(post_save, sender=ProjectMembership)
def send_email_notification_for_new_project_membership(sender, instance, created, **kwargs):
    if created and instance.member != get_current_user():
        instance.member.send_email_notification_for_new_project_membership(
            project=instance.project,
        )


@receiver(pre_save, sender=ProjectMembership)
def send_email_notification_for_changed_project_membership(sender, instance, **kwargs):
    # If the project membership is created, then we don't need to check anything
    if instance.pk is None:
        return

    # Get the old value for this project membership if it already exists, else abort
    try:
        project_membership = ProjectMembership.objects.get(pk=instance.pk)
    except ProjectMembership.DoesNotExist:
        return

    if project_membership.member == get_current_user():
        return

    if (
        project_membership.is_project_admin != instance.is_project_admin
        or project_membership.can_create_folders != instance.can_create_folders
    ):
        instance.member.send_email_notification_for_changed_project_membership(
            project=instance.project,
        )


@receiver(post_delete, sender=ProjectMembership)
def send_email_notification_for_deleted_project_membership(sender, instance, *args, **kwargs):
    if instance.member != get_current_user():
        instance.member.send_email_notification_for_deleted_project_membership(
            project=instance.project,
        )


@receiver(folder_datasets_count_updated, sender=Folder)
def set_project_deletable_status(sender, instance, *args, **kwargs):
    instance.project.is_deletable = not UploadsDataset.objects.filter(
        folder__project=instance.project,
    ).exists()
    instance.project.save()


@receiver(post_save, sender=MetadataTemplate)
def post_save_metadata_template_update_project_metadata_templates_count(sender, instance, *args, **kwargs):
    # We are currently intentionally ignoring global templates
    if instance.metadata_template_container and isinstance(
        instance.metadata_template_container.assigned_to_content_object,
        Project,
    ):
        instance.metadata_template_container.assigned_to_content_object.refresh_from_db()
        instance.metadata_template_container.assigned_to_content_object.update_metadata_templates_count()

        for folder in instance.metadata_template_container.assigned_to_content_object.folders.all():
            folder.update_metadata_templates_count()


@receiver(post_delete, sender=MetadataTemplate)
def post_delete_metadata_template_update_project_metadata_templates_count(sender, instance, *args, **kwargs):
    # We are currently intentionally ignoring global templates
    if instance.metadata_template_container and isinstance(
        instance.metadata_template_container.assigned_to_content_object,
        Project,
    ):
        instance.metadata_template_container.assigned_to_content_object.refresh_from_db()
        instance.metadata_template_container.assigned_to_content_object.update_metadata_templates_count()

        for folder in instance.metadata_template_container.assigned_to_content_object.folders.all():
            folder.update_metadata_templates_count()
