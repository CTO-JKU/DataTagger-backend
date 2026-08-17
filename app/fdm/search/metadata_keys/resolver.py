from uuid import UUID

from django.core.exceptions import PermissionDenied
from rest_framework.exceptions import NotFound

from fdm.core.rest.permissions import can_view_in_folder
from fdm.folders.models import Folder
from fdm.metadata.models import MetadataTemplate
from fdm.projects.models import Project, ProjectMembership
from fdm.uploads.models import UploadsDataset

PROJECT_SCOPE = "project"
FOLDER_SCOPE = "folder"
DATASET_SCOPE = "dataset"

ScopePair = tuple[MetadataTemplate, str, object]


class ActiveTemplateScopeResolver:
    def __init__(self, user):
        self.user = user

    def resolve(self, scope_kind: str, scope_id: UUID) -> list[ScopePair]:
        if scope_kind == PROJECT_SCOPE:
            return self._resolve_project(scope_id)
        if scope_kind == FOLDER_SCOPE:
            return self._resolve_folder(scope_id)
        if scope_kind == DATASET_SCOPE:
            return self._resolve_dataset(scope_id)
        raise ValueError(f"Unknown scope kind: {scope_kind}")

    def _resolve_project(self, project_id: UUID) -> list[ScopePair]:
        project = self._get_project(project_id)
        if not self._is_project_member(project):
            raise PermissionDenied("You do not have access to this project.")
        return self._pair(project.metadata_template, PROJECT_SCOPE, project.pk)

    def _resolve_folder(self, folder_id: UUID) -> list[ScopePair]:
        try:
            folder = Folder.objects.select_related("project").get(pk=folder_id)
        except Folder.DoesNotExist:
            raise NotFound("Folder not found.")

        if not can_view_in_folder(self.user, folder.pk):
            raise PermissionDenied("You do not have access to this folder.")

        pairs: list[ScopePair] = []
        project = folder.project
        if project is not None and self._is_project_member(project):
            pairs += self._pair(project.metadata_template, PROJECT_SCOPE, project.pk)
        pairs += self._pair(folder.metadata_template, FOLDER_SCOPE, folder.pk)
        return pairs

    def _resolve_dataset(self, dataset_id: UUID) -> list[ScopePair]:
        try:
            dataset = UploadsDataset.objects.select_related("folder__project").get(pk=dataset_id)
        except UploadsDataset.DoesNotExist:
            raise NotFound("Dataset not found.")

        if not self._can_view_dataset(dataset):
            raise PermissionDenied("You do not have access to this dataset.")

        pairs: list[ScopePair] = []
        folder = dataset.folder
        project = folder.project if folder else None
        if project is not None and self._is_project_member(project):
            pairs += self._pair(project.metadata_template, PROJECT_SCOPE, project.pk)
        if folder is not None and can_view_in_folder(self.user, folder.pk):
            pairs += self._pair(folder.metadata_template, FOLDER_SCOPE, folder.pk)
        pairs += self._pair(dataset.metadata_template, DATASET_SCOPE, dataset.pk)
        return pairs

    def _get_project(self, project_id: UUID) -> Project:
        try:
            return Project.objects.get(pk=project_id)
        except Project.DoesNotExist:
            raise NotFound("Project not found.")

    def _is_project_member(self, project: Project) -> bool:
        return ProjectMembership.objects.filter(member=self.user, project=project).exists()

    def _can_view_dataset(self, dataset: UploadsDataset) -> bool:
        if not dataset.is_published() and dataset.created_by == self.user:
            return True
        if dataset.folder is not None and can_view_in_folder(self.user, dataset.folder.pk):
            return True
        return False

    def _pair(self, template, source, source_id) -> list[ScopePair]:
        return [(template, source, source_id)] if template is not None else []
