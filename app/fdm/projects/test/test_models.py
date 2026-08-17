import pytest
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from fdm.core.helpers import set_request_for_user
from fdm.folders.models import Folder, FolderPermission
from fdm.metadata.enums import MetadataFieldType
from fdm.metadata.helpers import set_metadata
from fdm.metadata.models import MetadataField
from fdm.projects.models import Project, ProjectMembership
from fdm.uploads.models import UploadsDataset


@pytest.mark.django_db
class TestProjectModel:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.project_1 = Project.objects.create(
            name="Project 1",
        )

    def test_folder_creation(self):
        """
        Ensure that a default folder is automatically created when creating a project.
        """
        project_2 = Project.objects.create(
            name="Project 2",
        )
        assert project_2.folders_count == 1
        assert project_2.folders.first().name == _("General")
        assert project_2.is_deletable is True

        project_2.name = "My project 2"
        project_2.save()
        assert project_2.folders_count == 1
        assert project_2.is_deletable is True

    def test_delete(self):
        """
        Ensure we can delete an empty project.
        """
        project_pk = self.project_1.pk

        self.project_1.delete()
        assert Project.objects.filter(pk=project_pk).count() == 0
        assert ProjectMembership.objects.filter(project=project_pk).count() == 0
        assert FolderPermission.objects.filter(project_membership__project=project_pk).count() == 0
        assert Folder.objects.filter(project=project_pk).count() == 0

    def test_delete_protection(self):
        """
        Ensure we can't delete a project if it isn't empty.
        """
        project_pk = self.project_1.pk

        assert self.project_1.is_deletable is True

        UploadsDataset.objects.create(
            name="Dataset 1",
            folder=self.project_1.folders.first(),
        )

        assert self.project_1.is_deletable is False

        with transaction.atomic():
            with pytest.raises(PermissionDenied):
                self.project_1.delete()

        assert Project.objects.filter(pk=project_pk).count() == 1
        assert ProjectMembership.objects.filter(project=project_pk).count() == 1
        assert FolderPermission.objects.filter(project_membership__project=project_pk).count() == 1
        assert Folder.objects.filter(project=project_pk).count() == 1

    def test_description_field(self):
        """
        Ensure the project description field returns JSON data as a dict
        """
        self.project_1.description = {
            "custom_key": "custom_value",
            "some_boolean": True,
        }
        self.project_1.save()

        assert self.project_1.description.get("custom_key", None) == "custom_value"
        assert self.project_1.description.get("some_boolean", None) is True

        with pytest.raises(ValidationError):
            self.project_1.description = ""
            self.project_1.save()

        with pytest.raises(ValidationError):
            self.project_1.description = None
            self.project_1.save()

        with pytest.raises(ValidationError):
            self.project_1.description = "string value"
            self.project_1.save()


@pytest.mark.django_db
class TestProjectMetadata:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.project_1 = Project.objects.create(
            name="Project 1",
        )

        self.project_metadata_1 = set_metadata(
            assigned_to_content_type=self.project_1.get_content_type(),
            assigned_to_object_id=self.project_1.pk,
            custom_key="custom_key_1",
            value="custom_value_1",
        )

        self.metadata_field_1 = MetadataField.objects.create(
            key="metadata_field_1",
            field_type=MetadataFieldType.TEXT,
            read_only=True,
        )

    def test_clean_method(self):
        """
        Ensure we get an error if we try to assign a custom key and a field at the same time, or none at all.
        """
        with pytest.raises(ValidationError):
            set_metadata(
                assigned_to_content_type=self.project_1.get_content_type(),
                assigned_to_object_id=self.project_1.pk,
                field=self.metadata_field_1,
                custom_key="custom_key_1",
                value="custom_value_1",
            )

        with pytest.raises(ValidationError):
            set_metadata(
                assigned_to_content_type=self.project_1.get_content_type(),
                assigned_to_object_id=self.project_1.pk,
                value="custom_value_1",
            )


@pytest.mark.django_db
class TestProjectLockMixin:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.project_1 = Project.objects.create(
            name="Project 1",
        )

    def test_lock(self, initial_users):
        """
        Ensure we have an expected lock behavior.
        """
        project = Project.objects.get(pk=self.project_1.pk)
        project.name = "Project 1"
        project.save()

        project.refresh_from_db()
        assert project.name == "Project 1"
        assert project.locked is False
        assert project.locked_by is None
        assert project.locked_at is None

        project.lock()

        project.refresh_from_db()
        assert project.name == "Project 1"
        assert project.locked is True
        assert project.locked_by == initial_users["user_1"]
        assert project.locked_at is not None

        last_lock_time = project.locked_at

        project.lock()

        project.refresh_from_db()
        assert project.name == "Project 1"
        assert project.locked is True
        assert project.locked_by == initial_users["user_1"]
        assert project.locked_at is not None
        assert project.locked_at > last_lock_time

        project.name = "Project 1 - Edit #1"
        project.save()

        project.refresh_from_db()
        assert project.name == "Project 1 - Edit #1"
        assert project.locked is False
        assert project.locked_by is None
        assert project.locked_at is None

        project.lock()

        project.refresh_from_db()
        assert project.name == "Project 1 - Edit #1"
        assert project.locked is True
        assert project.locked_by == initial_users["user_1"]
        assert project.locked_at is not None

        project.unlock()

        project.refresh_from_db()
        assert project.name == "Project 1 - Edit #1"
        assert project.locked is False
        assert project.locked_by is None
        assert project.locked_at is None

        project.lock()
        last_lock_time = project.locked_at

        project.name = "Project 1 - Edit #2"
        project.save(auto_unlock=False)

        project.refresh_from_db()
        assert project.name == "Project 1 - Edit #2"
        assert project.locked is True
        assert project.locked_by == initial_users["user_1"]
        assert project.locked_at is not None
        assert project.locked_at == last_lock_time

        last_lock_time = project.locked_at

        set_request_for_user(initial_users["user_2"])

        with pytest.raises(PermissionDenied):
            project.name = "Project 1 - Edit #3"
            project.save()

        project.refresh_from_db()
        assert project.name == "Project 1 - Edit #2"
        assert project.locked_at == last_lock_time

        project.name = "Project 1 - Edit #3"
        project.locked_at = timezone.now() - timezone.timedelta(minutes=settings.MAX_LOCK_TIME)
        project.save()

        project.refresh_from_db()
        assert project.name == "Project 1 - Edit #3"
        assert project.locked is False
        assert project.locked_by is None
        assert project.locked_at is None
