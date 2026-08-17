import pytest

from fdm.core.helpers import set_request_for_user
from fdm.folders.models import Folder, FolderPermission
from fdm.metadata.models import MetadataTemplateContainer
from fdm.metadata.services import add_scoped_container_to_pool
from fdm.projects.models import Project, ProjectMembership
from fdm.uploads.models import UploadsDataset


@pytest.mark.django_db
class TestAddScopedContainerToPool:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])
        self.admin = initial_users["user_1"]
        self.outsider = initial_users["user_2"]

        self.project = Project.objects.create(name="Project 1")
        self.folder = self.project.folders.first()

    def _container_for(self, scope):
        return MetadataTemplateContainer.objects.create(
            name="Container",
            assigned_to_content_type=scope.get_content_type() if scope else None,
            assigned_to_object_id=scope.pk if scope else None,
        )

    def test_project_scope_with_admin_user_adds_membership_and_returns_true(self):
        container = self._container_for(self.project)

        result = add_scoped_container_to_pool(container, self.admin)

        assert result is True
        assert self.project.metadata_template_container.filter(pk=container.pk).exists()

    def test_folder_scope_with_admin_user_adds_membership_and_returns_true(self):
        container = self._container_for(self.folder)

        result = add_scoped_container_to_pool(container, self.admin)

        assert result is True
        assert self.folder.metadata_template_container.filter(pk=container.pk).exists()

    def test_global_container_returns_false_and_touches_nothing(self):
        container = self._container_for(None)

        result = add_scoped_container_to_pool(container, self.admin)

        assert result is False
        assert not self.project.metadata_template_container.filter(pk=container.pk).exists()
        assert not self.folder.metadata_template_container.filter(pk=container.pk).exists()

    def test_dataset_scope_returns_false_and_touches_nothing(self):
        dataset = UploadsDataset.objects.create(name="Dataset 1")
        container = self._container_for(dataset)

        result = add_scoped_container_to_pool(container, self.admin)

        assert result is False
        assert not self.project.metadata_template_container.filter(pk=container.pk).exists()
        assert not self.folder.metadata_template_container.filter(pk=container.pk).exists()

    def test_project_scope_with_non_admin_user_returns_false_without_raising(self):
        ProjectMembership.objects.create(
            project=self.project,
            member=self.outsider,
            is_project_admin=False,
            is_metadata_template_admin=True,
        )
        container = self._container_for(self.project)

        result = add_scoped_container_to_pool(container, self.outsider)

        assert result is False
        assert not self.project.metadata_template_container.filter(pk=container.pk).exists()
