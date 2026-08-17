import pytest
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.utils import timezone

from fdm.core.helpers import set_request_for_user
from fdm.folders.models import Folder, FolderPermission, get_default_folder_storage
from fdm.metadata.enums import MetadataFieldType
from fdm.metadata.helpers import set_metadata
from fdm.metadata.models import MetadataField, MetadataTemplate, MetadataTemplateContainer
from fdm.projects.models import Project, ProjectMembership
from fdm.uploads.models import UploadsDataset, UploadsVersion, UploadsVersionFile

sample_file = SimpleUploadedFile(
    "file.jpg",
    b"",
    content_type="image/jpg",
)


@pytest.mark.django_db
class TestFolderModel:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.project_1 = Project.objects.create(
            name="Project 1",
        )

    def test_default_folder_storage(self):
        """
        Ensure every folder has a default storage assigned to it.
        """
        assert self.project_1.folders.first().storage == get_default_folder_storage()

    def test_delete(self):
        """
        Ensure we can delete an empty folder.
        """
        folder_pk = self.project_1.folders.first().pk

        self.project_1.folders.first().delete()
        assert FolderPermission.objects.filter(folder=folder_pk).count() == 0

    def test_delete_protection(self):
        """
        Ensure we can't delete a folder if it isn't empty.
        """
        folder_pk = self.project_1.folders.first().pk

        UploadsDataset.objects.create(
            name="Dataset 1",
            folder=self.project_1.folders.first(),
        )

        with transaction.atomic():
            with pytest.raises(PermissionDenied):
                self.project_1.folders.first().delete()

        assert FolderPermission.objects.filter(folder=folder_pk).count() == 1

    def test_description_field(self):
        """
        Ensure the folder description field returns JSON data as a dict
        """
        folder = self.project_1.folders.first()

        folder.description = {
            "custom_key": "custom_value",
            "some_boolean": True,
        }
        folder.save()

        assert folder.description.get("custom_key", None) == "custom_value"
        assert folder.description.get("some_boolean", None) is True

        with pytest.raises(ValidationError):
            folder.description = ""
            folder.save()

        with pytest.raises(ValidationError):
            folder.description = None
            folder.save()

        with pytest.raises(ValidationError):
            folder.description = "string value"
            folder.save()

    def test_update_storage_size(self, initial_users):
        """
        Ensure the folder storage size is updated correctly
        """
        folder_1 = self.project_1.folders.first()

        uploads_dataset_1 = UploadsDataset.objects.create(
            name="Dataset 1",
            folder=folder_1,
            publication_date=timezone.now(),
            deletable_until_date=timezone.now() + timezone.timedelta(hours=1),
        )

        uploads_dataset_2 = UploadsDataset.objects.create(
            name="Dataset 2",
        )

        uploads_version_file_1 = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        uploads_version_file_2 = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        uploads_version_file_3 = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        UploadsVersion.objects.create(
            dataset=uploads_dataset_1,
            version_file=uploads_version_file_1,
        )

        UploadsVersion.objects.create(
            dataset=uploads_dataset_1,
            version_file=uploads_version_file_2,
        )

        UploadsVersion.objects.create(
            dataset=uploads_dataset_2,
            version_file=uploads_version_file_3,
        )

        file_size_1 = 123
        set_metadata(
            assigned_to_content_type=uploads_version_file_1.get_content_type(),
            assigned_to_object_id=uploads_version_file_1.pk,
            custom_key="FILE_SIZE",
            value=file_size_1,
        )

        file_size_2 = 456
        set_metadata(
            assigned_to_content_type=uploads_version_file_2.get_content_type(),
            assigned_to_object_id=uploads_version_file_2.pk,
            custom_key="FILE_SIZE",
            value=file_size_2,
        )

        file_size_3 = 789
        set_metadata(
            assigned_to_content_type=uploads_version_file_3.get_content_type(),
            assigned_to_object_id=uploads_version_file_3.pk,
            custom_key="FILE_SIZE",
            value=file_size_3,
        )

        assert folder_1.storage_size == 0

        folder_1.update_storage_size()
        folder_1.refresh_from_db()
        assert folder_1.storage_size == file_size_1 + file_size_2

        user_1 = initial_users["user_1"]
        assert user_1.storage_size == 0

        user_1.update_storage_size()
        user_1.refresh_from_db()
        assert user_1.storage_size == file_size_1 + file_size_2 + file_size_3

        uploads_dataset_1.delete()

        folder_1.refresh_from_db()
        assert folder_1.storage_size == 0

        user_1.refresh_from_db()
        assert user_1.storage_size == file_size_3

        uploads_dataset_2.delete()

        folder_1.refresh_from_db()
        assert folder_1.storage_size == 0

        user_1.refresh_from_db()
        assert user_1.storage_size == 0


@pytest.mark.django_db
class TestFolderMetadataModel:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.project_1 = Project.objects.create(
            name="Folder 1",
        )

        self.folder_1 = self.project_1.folders.first()

        self.folder_metadata_1 = set_metadata(
            assigned_to_content_type=self.folder_1.get_content_type(),
            assigned_to_object_id=self.folder_1.pk,
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
                assigned_to_content_type=self.folder_1.get_content_type(),
                assigned_to_object_id=self.folder_1.pk,
                field=self.metadata_field_1,
                custom_key="custom_key_1",
                value="custom_value_1",
            )

        with pytest.raises(ValidationError):
            set_metadata(
                assigned_to_content_type=self.folder_1.get_content_type(),
                assigned_to_object_id=self.folder_1.pk,
                value="custom_value_1",
            )

    def test_get_available_metadata_templates(self):
        """
        Ensure we get all metadata templates available to us.
        This contains metadata templates assigned to the folder itself, metadata templates assigned to the project
        the folder is part of and globally available metadata templates.
        """
        assert self.folder_1.get_available_metadata_templates().count() == 0

        metadata_template_container_1 = MetadataTemplateContainer.objects.create(
            name="Folder metadata template container 1",
            assigned_to_content_type=self.folder_1.get_content_type(),
            assigned_to_object_id=self.folder_1.pk,
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_1,
        )

        assert self.folder_1.get_available_metadata_template_container().count() == 1
        assert self.folder_1.get_available_metadata_templates().count() == 1

        folder_2 = Folder.objects.create(
            name="Folder 2",
            project=self.project_1,
        )

        metadata_template_container_2 = MetadataTemplateContainer.objects.create(
            name="Folder metadata template container 2",
            assigned_to_content_type=folder_2.get_content_type(),
            assigned_to_object_id=folder_2.pk,
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_2,
        )

        assert self.folder_1.get_available_metadata_template_container().count() == 1
        assert self.folder_1.get_available_metadata_templates().count() == 1

        metadata_template_container_3 = MetadataTemplateContainer.objects.create(
            name="Project metadata template container 1",
            assigned_to_content_type=self.project_1.get_content_type(),
            assigned_to_object_id=self.project_1.pk,
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_3,
        )

        assert self.folder_1.get_available_metadata_template_container().count() == 2
        assert self.folder_1.get_available_metadata_templates().count() == 2

        project_2 = Project.objects.create(
            name="Project 2",
        )

        metadata_template_container_4 = MetadataTemplateContainer.objects.create(
            name="Project metadata template container 2",
            assigned_to_content_type=project_2.get_content_type(),
            assigned_to_object_id=project_2.pk,
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_4,
        )

        assert self.folder_1.get_available_metadata_template_container().count() == 2
        assert self.folder_1.get_available_metadata_templates().count() == 2

        metadata_template_container_5 = MetadataTemplateContainer.objects.create(
            name="Global metadata template container 1",
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_5,
        )

        assert self.folder_1.get_available_metadata_template_container().count() == 3
        assert self.folder_1.get_available_metadata_templates().count() == 3

    def test_members_count(self, initial_users):
        """
        Ensure we get an accurate count of all members assigned to a folder.
        """
        assert self.folder_1.members_count == 1

        project_membership_2 = ProjectMembership.objects.create(
            project=self.project_1,
            member=initial_users["user_2"],
        )

        FolderPermission.objects.create(
            folder=self.folder_1,
            project_membership=project_membership_2,
        )

        assert self.folder_1.members_count == 2

        folder_permission = FolderPermission.objects.get(
            project_membership=project_membership_2,
            folder=self.folder_1,
        )
        folder_permission.delete()

        self.folder_1.refresh_from_db()
        assert self.folder_1.members_count == 1

    def test_datasets_count(self):
        """
        Ensure we get an accurate count of published datasets in a folder.
        """
        assert self.folder_1.datasets_count == 0

        UploadsDataset.objects.create(
            name="Dataset 1",
            folder=self.folder_1,
            publication_date=timezone.now(),
        )

        assert self.folder_1.datasets_count == 1

        UploadsDataset.objects.create(
            name="Dataset 2",
            folder=self.folder_1,
            publication_date=timezone.now(),
        )

        assert self.folder_1.datasets_count == 2

        folder_2 = Folder.objects.create(
            name="Folder 2",
            project=self.project_1,
        )

        UploadsDataset.objects.create(
            name="Dataset 3",
            folder=folder_2,
            publication_date=timezone.now(),
        )

        assert self.folder_1.datasets_count == 2

        UploadsDataset.objects.create(
            name="Dataset 4",
            folder=self.folder_1,
        )

        assert self.folder_1.datasets_count == 2

        UploadsDataset.objects.create(
            name="Dataset 5",
            folder=self.folder_1,
            publication_date=timezone.now(),
        )

        assert self.folder_1.datasets_count == 3


@pytest.mark.django_db
class TestFolderLockMixin:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.project_1 = Project.objects.create(
            name="Project 1",
        )

        self.folder_1 = self.project_1.folders.first()

    def test_lock(self, initial_users):
        """
        Ensure we have an expected lock behavior.
        """
        folder = Folder.objects.get(pk=self.folder_1.pk)
        folder.name = "Folder 1"
        folder.save()

        folder.refresh_from_db()
        assert folder.name == "Folder 1"
        assert folder.locked is False
        assert folder.locked_by is None
        assert folder.locked_at is None

        folder.lock()

        folder.refresh_from_db()
        assert folder.name == "Folder 1"
        assert folder.locked is True
        assert folder.locked_by == initial_users["user_1"]
        assert folder.locked_at is not None

        last_lock_time = folder.locked_at

        folder.lock()

        folder.refresh_from_db()
        assert folder.name == "Folder 1"
        assert folder.locked is True
        assert folder.locked_by == initial_users["user_1"]
        assert folder.locked_at is not None
        assert folder.locked_at > last_lock_time

        folder.name = "Folder 1 - Edit #1"
        folder.save()

        folder.refresh_from_db()
        assert folder.name == "Folder 1 - Edit #1"
        assert folder.locked is False
        assert folder.locked_by is None
        assert folder.locked_at is None

        folder.lock()

        folder.refresh_from_db()
        assert folder.name == "Folder 1 - Edit #1"
        assert folder.locked is True
        assert folder.locked_by == initial_users["user_1"]
        assert folder.locked_at is not None

        folder.unlock()

        folder.refresh_from_db()
        assert folder.name == "Folder 1 - Edit #1"
        assert folder.locked is False
        assert folder.locked_by is None
        assert folder.locked_at is None

        folder.lock()
        last_lock_time = folder.locked_at

        folder.name = "Folder 1 - Edit #2"
        folder.save(auto_unlock=False)

        folder.refresh_from_db()
        assert folder.name == "Folder 1 - Edit #2"
        assert folder.locked is True
        assert folder.locked_by == initial_users["user_1"]
        assert folder.locked_at is not None
        assert folder.locked_at == last_lock_time

        last_lock_time = folder.locked_at

        set_request_for_user(initial_users["user_2"])

        with pytest.raises(PermissionDenied):
            folder.name = "Folder 1 - Edit #3"
            folder.save()

        folder.refresh_from_db()
        assert folder.name == "Folder 1 - Edit #2"
        assert folder.locked_at == last_lock_time

        folder.name = "Folder 1 - Edit #3"
        folder.locked_at = timezone.now() - timezone.timedelta(minutes=settings.MAX_LOCK_TIME)
        folder.save()

        folder.refresh_from_db()
        assert folder.name == "Folder 1 - Edit #3"
        assert folder.locked is False
        assert folder.locked_by is None
        assert folder.locked_at is None
