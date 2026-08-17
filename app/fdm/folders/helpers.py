from django.db.models import F
from rest_framework import serializers

from fdm.core.helpers import get_or_create_user
from fdm.folders.models import FolderPermission
from fdm.projects.models import ProjectMembership

__all__ = [
    "set_folder_admin_permissions",
    "add_folder_permissions_for_users",
]


def set_folder_admin_permissions(permission: FolderPermission):
    permission.is_folder_admin = True
    permission.is_metadata_template_admin = True
    permission.can_edit = True


def add_folder_permissions_for_users(folder, folder_users):
    # All project admins must be automatically added with their respective FolderPermission.
    missing_project_admins = (
        ProjectMembership.objects.filter(
            project=folder.project,
            is_project_admin=True,
        )
        .exclude(
            member__email__in=[folder_user["email"].lower() for folder_user in folder_users],
        )
        .annotate(
            email=F("member__email"),
        )
        .values(
            "email",
        )
    )

    for admin in missing_project_admins:
        admin["is_folder_admin"] = True
        admin["is_metadata_template_admin"] = True
        admin["can_edit"] = True

    folder_users += list(missing_project_admins)

    for folder_user in folder_users:
        try:
            user = get_or_create_user(folder_user["email"])
        except Exception as e:
            raise serializers.ValidationError(str(e))

        try:
            project_membership = ProjectMembership.objects.get(
                project=folder.project,
                member=user,
            )
        except ProjectMembership.DoesNotExist:
            project_membership = ProjectMembership.objects.create(
                project=folder.project,
                member=user,
                is_project_admin=False,
                is_metadata_template_admin=False,
                can_create_folders=False,
            )

        try:
            folder_permission = FolderPermission.objects.get(
                project_membership=project_membership,
                folder=folder,
            )
            folder_permission.is_folder_admin = (
                True if project_membership.is_project_admin else folder_user["is_folder_admin"]
            )
            folder_permission.is_metadata_template_admin = (
                True if project_membership.is_project_admin else folder_user["is_metadata_template_admin"]
            )
            folder_permission.can_edit = True if project_membership.is_project_admin else folder_user["can_edit"]
            folder_permission.save()
        except FolderPermission.DoesNotExist:
            FolderPermission.objects.create(
                project_membership=project_membership,
                folder=folder,
                is_folder_admin=True if project_membership.is_project_admin else folder_user["is_folder_admin"],
                is_metadata_template_admin=(
                    True if project_membership.is_project_admin else folder_user["is_metadata_template_admin"]
                ),
                can_edit=True if project_membership.is_project_admin else folder_user["can_edit"],
            )
