from django.db.models import Q, Subquery
from django.utils import timezone
from rest_framework import permissions

from fdm.folders.models import FolderPermission
from fdm.metadata.models import MetadataTemplate, MetadataTemplateContainer
from fdm.projects.models import ProjectMembership

__all__ = [
    "can_view_in_folder",
    "can_edit_in_folder",
    "is_project_admin",
    "is_folder_admin",
    "is_project_metadata_template_admin",
    "is_folder_metadata_template_admin",
    "CanCreateProject",
    "IsProjectAdmin",
    "IsProjectAdminForMembership",
    "IsProjectMetadataTemplateAdmin",
    "IsProjectMember",
    "IsProjectMemberForMembership",
    "CanCreateProjectMembership",
    "CanCreateFolders",
    "CanCreateFolderPermission",
    "IsFolderAdmin",
    "IsFolderAdminForPermission",
    "IsFolderMetadataTemplateAdmin",
    "CanEditInFolder",
    "CanCreateDataset",
    "CanDeleteDataset",
    "CanCreateDatasetVersion",
    "CanViewFolder",
    "CanViewInFolder",
    "CanEditMetadataTemplate",
    "CanDeleteProjectMembership",
    "CanEditMetadataTemplateContainer",
]


def can_view_in_folder(user, folder_pk):
    return FolderPermission.objects.filter(
        project_membership__member=user,
        folder=folder_pk,
    ).exists()


def can_edit_in_folder(user, folder_pk):
    return FolderPermission.objects.filter(
        project_membership__member=user,
        folder=folder_pk,
        can_edit=True,
    ).exists()


def is_project_admin(user, project):
    return ProjectMembership.objects.filter(
        member=user,
        project=project,
        is_project_admin=True,
    ).exists()


def is_folder_admin(user, folder):
    return FolderPermission.objects.filter(
        folder=folder,
        project_membership__member=user,
        is_folder_admin=True,
    ).exists()


def is_project_metadata_template_admin(user, project):
    return ProjectMembership.objects.filter(
        member=user,
        project=project,
        is_metadata_template_admin=True,
    ).exists()


def is_folder_metadata_template_admin(user, folder):
    return FolderPermission.objects.filter(
        folder=folder,
        project_membership__member=user,
        is_metadata_template_admin=True,
    ).exists()


class CanCreateProject(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.can_create_projects


class IsProjectAdmin(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        return is_project_admin(request.user, obj)


class IsProjectAdminForMembership(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        return is_project_admin(request.user, obj.project)


class IsProjectMetadataTemplateAdmin(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        return is_project_metadata_template_admin(request.user, obj)


class CanDeleteProjectMembership(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        # A user can always delete his own membership
        if obj.member == request.user:
            return True

        return is_project_admin(request.user, obj.project)


class IsProjectMember(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        return ProjectMembership.objects.filter(
            member=request.user,
            project=obj,
        ).exists()


class IsProjectMemberForMembership(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        return ProjectMembership.objects.filter(
            member=request.user,
            project=obj.project,
        ).exists()


class CanCreateProjectMembership(permissions.BasePermission):
    """
    Custom permission to only allow project admins to create project memberships.
    """

    def has_permission(self, request, view):
        if request.method in ["GET", "HEAD", "OPTIONS"]:
            return True

        return ProjectMembership.objects.filter(
            member=request.user,
            project__pk=request.data.get("project", None),
            is_project_admin=True,
        ).exists()


class CanCreateFolders(permissions.BasePermission):
    """
    Custom permission to only allow users with can_create_folders permission to create folders.
    """

    def has_permission(self, request, view):
        if request.method in ["GET", "HEAD", "OPTIONS"]:
            return True
        return ProjectMembership.objects.filter(
            project=request.data.get("project", None),
            member=request.user,
            can_create_folders=True,
        ).exists()


class CanCreateFolderPermission(permissions.BasePermission):
    """
    Custom permission to only allow folder admins to create folder permissions.
    """

    def has_permission(self, request, view):
        if request.method in ["GET", "HEAD", "OPTIONS"]:
            return True

        return FolderPermission.objects.filter(
            project_membership__member=request.user,
            folder__pk=request.data.get("folder", None),
            is_folder_admin=True,
        ).exists()


class IsFolderAdmin(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        return is_folder_admin(request.user, obj)


class IsFolderAdminForPermission(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        return FolderPermission.objects.filter(
            folder=obj.folder,
            project_membership__member=request.user,
            is_folder_admin=True,
        ).exists()


class IsFolderMetadataTemplateAdmin(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        return is_folder_metadata_template_admin(request.user, obj)


class CanEditInFolder(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.method in ["GET", "HEAD", "OPTIONS"]:
            return True

        folder_pk = request.data.get("folder", None)

        if folder_pk:
            return can_edit_in_folder(
                user=request.user,
                folder_pk=folder_pk,
            )

        return False


class CanCreateDataset(permissions.BasePermission):
    def has_permission(self, request, view):
        folder_pk = request.data.get("folder", None)

        if folder_pk:
            return FolderPermission.objects.filter(
                folder=folder_pk,
                project_membership__member=request.user,
                can_edit=True,
            ).exists()

        return True


class CanDeleteDataset(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        from fdm.uploads.models import UploadsDataset

        if getattr(request.user, "can_hard_delete_datasets", False):
            return True

        query = Q(
            created_by=request.user,
        )

        if obj.publication_date:
            query &= Q(
                deletable_until_date__isnull=False,
                deletable_until_date__gte=timezone.now(),
            )
        else:
            query &= Q(
                publication_date__isnull=True,
            )

        return UploadsDataset.objects.filter(query).filter(pk=obj.pk).exists()


class CanCreateDatasetVersion(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        if obj.folder:
            return FolderPermission.objects.filter(
                folder=obj.folder,
                project_membership__member=request.user,
                can_edit=True,
            ).exists()

        return obj.created_by == request.user


class CanViewFolder(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        return FolderPermission.objects.filter(
            project_membership__member=request.user,
            folder=obj,
        ).exists()


class CanViewInFolder(permissions.BasePermission):
    def has_permission(self, request, view):
        folder_pk = request.data.get("dataset__folder", None)
        return can_view_in_folder(request.user, folder_pk)


class CanEditMetadataTemplate(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        from django.contrib.contenttypes.models import ContentType

        from fdm.folders.models import Folder, FolderPermission
        from fdm.projects.models import Project, ProjectMembership
        from fdm.uploads.models import UploadsDataset

        # Project metadata templates
        query = Q(
            metadata_template_container__assigned_to_content_type=ContentType.objects.get_for_model(Project),
            metadata_template_container__assigned_to_object_id__in=Subquery(
                ProjectMembership.objects.filter(
                    member=request.user,
                    is_metadata_template_admin=True,
                ).values_list(
                    "project__pk",
                    flat=True,
                ),
            ),
        )

        # Folder metadata templates
        query |= Q(
            metadata_template_container__assigned_to_content_type=ContentType.objects.get_for_model(Folder),
            metadata_template_container__assigned_to_object_id__in=Subquery(
                FolderPermission.objects.filter(
                    project_membership__member=request.user,
                    is_metadata_template_admin=True,
                ).values_list(
                    "folder__pk",
                    flat=True,
                ),
            ),
        )

        # Uploads dataset metadata templates
        query |= Q(
            metadata_template_container__assigned_to_content_type=ContentType.objects.get_for_model(UploadsDataset),
            metadata_template_container__assigned_to_object_id__in=Subquery(
                UploadsDataset.objects.filter(
                    folder__folderpermission__project_membership__member=request.user,
                    folder__folderpermission__can_edit=True,
                    folder__folderpermission__is_metadata_template_admin=True,
                ).values_list(
                    "pk",
                    flat=True,
                ),
            ),
        )

        # Global metadata templates
        if request.user.is_authenticated and request.user.is_global_metadata_template_admin:
            query |= Q(
                metadata_template_container__assigned_to_content_type__isnull=True,
                metadata_template_container__assigned_to_object_id__isnull=True,
            )

        return MetadataTemplate.objects.filter(query).filter(pk=obj.pk).exists()


class CanEditMetadataTemplateContainer(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        from django.contrib.contenttypes.models import ContentType

        from fdm.folders.models import Folder, FolderPermission
        from fdm.projects.models import Project, ProjectMembership
        from fdm.uploads.models import UploadsDataset

        # Project metadata template container
        query = Q(
            assigned_to_content_type=ContentType.objects.get_for_model(Project),
            assigned_to_object_id__in=Subquery(
                ProjectMembership.objects.filter(
                    member=request.user,
                    is_metadata_template_admin=True,
                ).values_list(
                    "project__pk",
                    flat=True,
                ),
            ),
        )

        # Folder metadata template container
        query |= Q(
            assigned_to_content_type=ContentType.objects.get_for_model(Folder),
            assigned_to_object_id__in=Subquery(
                FolderPermission.objects.filter(
                    project_membership__member=request.user,
                    is_metadata_template_admin=True,
                ).values_list(
                    "folder__pk",
                    flat=True,
                ),
            ),
        )

        # Uploads dataset metadata template container
        query |= Q(
            assigned_to_content_type=ContentType.objects.get_for_model(UploadsDataset),
            assigned_to_object_id__in=Subquery(
                UploadsDataset.objects.filter(
                    folder__folderpermission__project_membership__member=request.user,
                    folder__folderpermission__can_edit=True,
                    folder__folderpermission__is_metadata_template_admin=True,
                ).values_list(
                    "pk",
                    flat=True,
                ),
            ),
        )

        # Global metadata template container
        if request.user.is_authenticated and request.user.is_global_metadata_template_admin:
            query |= Q(
                assigned_to_content_type__isnull=True,
                assigned_to_object_id__isnull=True,
            )

        return MetadataTemplateContainer.objects.filter(query).filter(pk=obj.pk).exists()
