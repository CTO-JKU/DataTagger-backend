import os
from io import BytesIO

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.utils import timezone
from django_fernet.fernet import FernetTextFieldData

from fdm.core.helpers import set_request_for_user
from fdm.folders.models import Folder, get_default_folder_storage
from fdm.metadata.helpers import set_metadata
from fdm.metadata.models import Metadata, MetadataTemplate, MetadataTemplateContainer, MetadataTemplateField
from fdm.projects.models import Project
from fdm.storages.models import DynamicStorage
from fdm.storages.models.mappings import DEFAULT_STORAGE_TYPE
from fdm.uploads.helpers import create_uploads_version_with_new_metadata_for_dataset
from fdm.uploads.models import UploadsDataset, UploadsVersion, UploadsVersionFile
from fdm.uploads.models.models import get_storage

User = get_user_model()


def get_dummy_textfile() -> bytes:
    text_data = BytesIO(b"Random string")
    text_data.seek(0)

    return text_data.read()


@pytest.mark.django_db
class TestStoragesModel:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        self.initial_users = initial_users
        set_request_for_user(initial_users["user_1"])

        self.current_date = timezone.now()

        self.random_filename = f"{self.current_date.strftime('%Y-%m-%dT%H-%M-%S-%f')}.png"

        self.storage_1 = DynamicStorage.objects.get(
            storage_type=DEFAULT_STORAGE_TYPE,
            default=True,
        )

        self.storage_2 = DynamicStorage.objects.create(
            name="NAS storage",
            storage_type="private_dss",
            default=False,
            approved=True,
            mounted=True,
        )

        field_data = FernetTextFieldData()
        field_data.encrypt("dssfs/container/private-dss0001", settings.SECRET_KEY)
        self.storage_2.local_private_dss_path_encrypted = field_data
        self.storage_2.save()

    def test_string_representation(self):
        """
        Ensure we get the correct string representation of a storage.
        """
        assert str(self.storage_1) == f"{self.storage_1.name} ({self.storage_1.storage_type})"

    def test_default_storage(self):
        """
        Ensure we have exactly one default storage at any time.
        """
        storage = DynamicStorage.objects.filter(default=True)
        default_storage = get_default_folder_storage()

        assert storage.count() == 1
        assert storage.first() == default_storage

    def test_delete_protection_default_storage(self, client):
        default_storage = get_default_folder_storage()
        with transaction.atomic():
            with pytest.raises(PermissionDenied):
                default_storage.delete()
        assert DynamicStorage.objects.filter(pk=default_storage.pk).exists()

    def test_delete_protection_used_storage(self):
        project = Project.objects.create(name="Project 1")
        folder = Folder.objects.get(pk=project.folders.first().pk)

        with transaction.atomic():
            with pytest.raises(PermissionDenied):
                folder.storage.delete()

        folder.refresh_from_db()
        assert DynamicStorage.objects.filter(pk=folder.storage.pk).exists()

    def test_storage_reassignment(self):
        project = Project.objects.create(name="Project 1")
        folder = Folder.objects.get(pk=project.folders.first().pk)

        folder.storage = self.storage_2
        folder.save()
        folder.refresh_from_db()

        with transaction.atomic():
            with pytest.raises(PermissionDenied):
                self.storage_2.delete()

        folder.refresh_from_db()
        assert folder.storage == self.storage_2

    @pytest.mark.django_db
    def test_prevent_second_default_local(self):
        with pytest.raises(PermissionDenied):
            DynamicStorage.objects.create(
                name="Second Default Local",
                storage_type="default_local",
            )

    def test_folder_storage_assignment_after_storage_deletion(self):
        """
        Ensure a folder gets the default storage assigned if the previously assigned storage gets deleted.
        """
        assert DynamicStorage.objects.filter(default=True).count() == 1

        storage = DynamicStorage.objects.get(default=True)

        with transaction.atomic():
            with pytest.raises(PermissionDenied):
                storage.delete()

        assert DynamicStorage.objects.filter(default=True).count() == 1

    def test_complete_publishing_process(self, client):
        """
        Simulate the entire process of uploading and publishing a file.
        """
        dataset_1 = UploadsDataset.objects.create(
            name="Dataset 1",
        )
        assert UploadsDataset.objects.all().count() == 1

        project = Project.objects.create(
            name="Test project",
        )
        assert Project.objects.all().count() == 1
        assert Folder.objects.all().count() == 1
        assert project.folders_count == 1

        metadata_template_container_1 = MetadataTemplateContainer.objects.create(
            name="Project metadata template container 1",
            assigned_to_content_type=project.get_content_type(),
            assigned_to_object_id=project.pk,
        )

        metadata_template = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_1,
        )
        assert MetadataTemplateContainer.objects.all().count() == 1
        assert MetadataTemplate.objects.all().count() == 1

        MetadataTemplateField.objects.create(
            metadata_template=metadata_template,
            custom_key="metadata_template_field_1",
            mandatory=False,
        )
        metadata_template_field_2 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template,
            custom_key="metadata_template_field_2",
            mandatory=True,
        )
        assert MetadataTemplateField.objects.all().count() == 2

        folder = project.folders.first()
        folder.storage = self.storage_1
        folder.metadata_template = metadata_template
        folder.save()
        assert folder.storage == self.storage_1
        assert folder.metadata_template == metadata_template
        assert folder.members_count == 1
        assert folder.datasets_count == 0

        uploads_version = UploadsVersion.objects.create(
            dataset=dataset_1,
        )
        assert UploadsVersion.objects.all().count() == 1

        # At this moment in time it can't be determined if the metadata is complete as the version
        # has no connection to a dataset, folder or metadata template.
        assert uploads_version.metadata_is_complete is True

        sample_file = SimpleUploadedFile(
            self.random_filename,
            get_dummy_textfile(),
            content_type="text/plain",
        )

        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )
        assert UploadsVersionFile.objects.all().count() == 1

        uploads_version.version_file = uploads_version_file
        uploads_version.save()
        assert uploads_version.version_file is not None
        assert uploads_version.version_file.uploaded_file.name == self.current_date.strftime(
            f"temp/{self.initial_users['user_1'].id}/%Y/%m/%d/{sample_file.name}",
        )
        assert uploads_version.version_file.storage_relocating == UploadsVersionFile.Status.FINISHED

        uploads_version.dataset.publish(folder=folder.pk)
        uploads_version.refresh_from_db()
        assert uploads_version.publication_date is not None
        assert uploads_version.is_published() is True
        assert uploads_version.dataset.publication_date is not None
        assert uploads_version.dataset.is_published() is True
        assert uploads_version.dataset is not None
        assert uploads_version.dataset.folder.datasets_count == 1
        assert uploads_version.metadata_templates.count() == 0

        uploads_version = uploads_version.dataset.latest_version
        assert uploads_version.metadata_templates.count() == 1

        uploads_version_file = UploadsVersionFile.objects.get(pk=uploads_version.version_file.pk)
        assert uploads_version_file.storage_relocating == UploadsVersionFile.Status.SCHEDULED

        uploads_version_file.move_file()
        assert uploads_version_file.storage_relocating == UploadsVersionFile.Status.FINISHED

        storage = get_storage(uploads_version.version_file)
        upload_path = storage.get_upload_to_path(uploads_version.version_file, sample_file.name)
        assert os.path.dirname(uploads_version_file.uploaded_file.name) == os.path.dirname(
            upload_path.replace(storage.location, "", 1).lstrip("/"),
        )

        uploads_version_file = UploadsVersionFile.objects.get(pk=uploads_version.version_file.pk)
        file_metadata = Metadata.objects.filter(
            assigned_to_content_type=uploads_version_file.get_content_type(),
            assigned_to_object_id=uploads_version_file.pk,
        )

        metadata = file_metadata.filter(custom_key="FILE_NAME")
        assert metadata.count() == 1
        assert metadata.first().get_value() == uploads_version_file.name

        metadata = file_metadata.filter(custom_key="FILE_RELATIVE_PATH")
        assert metadata.count() == 1
        assert metadata.first().get_value() == uploads_version_file.relative_path

        # Now that the version has been published, is connected to a folder and also a
        # metadata template, it should not be in a complete state.
        assert uploads_version.check_metadata_completeness() is False

        uploads_version = create_uploads_version_with_new_metadata_for_dataset(
            dataset=uploads_version.dataset,
            metadata_list=[
                dict(
                    custom_key="metadata_template_field_2",
                    value="Test value",
                    metadata_template_field=metadata_template_field_2,
                ),
            ],
            retain_existing_metadata=False,
        )
        assert uploads_version.check_metadata_completeness() is True
