import os
import time
from datetime import timedelta

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import TestCase
from django.utils import timezone
from zope.interface import named

from fdm.core.helpers import set_request_for_user
from fdm.dbsettings.functions import get_dbsettings_value
from fdm.folders.models import Folder, FolderPermission
from fdm.metadata.enums import MetadataFieldType
from fdm.metadata.helpers import set_metadata
from fdm.metadata.models import (
    Metadata,
    MetadataField,
    MetadataTemplate,
    MetadataTemplateContainer,
    MetadataTemplateField,
)
from fdm.projects.models import Project, ProjectMembership
from fdm.storages.models import DynamicStorage
from fdm.storages.models.mappings import DEFAULT_STORAGE_TYPE
from fdm.uploads.models import UploadsDataset, UploadsVersion, UploadsVersionFile
from fdm.uploads.tasks import remove_expired_dataset_drafts

User = get_user_model()

sample_file = SimpleUploadedFile(
    "file.jpg",
    b"",
    content_type="image/jpg",
)


@pytest.mark.django_db
class TestUploadsDatasetModel:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.uploads_dataset_1 = UploadsDataset.objects.create(
            name="Dataset 1",
        )

    def test_string_representation(self):
        """
        Ensure we get the correct string representation of a dataset.
        """
        assert str(self.uploads_dataset_1) == self.uploads_dataset_1.name

        uploads_dataset_2 = UploadsDataset.objects.create()
        assert str(uploads_dataset_2) == str(uploads_dataset_2.pk)

    def test_latest_version(self):
        """
        Ensure we get the latest version of a dataset.
        """
        assert self.uploads_dataset_1.uploads_versions_count == 0

        uploads_version_1 = UploadsVersion.objects.create(
            dataset=self.uploads_dataset_1,
            publication_date=timezone.now(),
        )

        assert self.uploads_dataset_1.latest_version.pk == uploads_version_1.pk
        assert self.uploads_dataset_1.uploads_versions_count == 1

        uploads_version_2 = UploadsVersion.objects.create(
            dataset=self.uploads_dataset_1,
            publication_date=timezone.now(),
        )

        assert self.uploads_dataset_1.latest_version.pk == uploads_version_2.pk
        assert self.uploads_dataset_1.uploads_versions_count == 2

    def test_is_latest_version_upgradeable_project_template(self):
        """
        Ensure we get the correct information if a metadata template is being upgradeable.
        """
        project_1 = Project.objects.create(
            name="Project 1",
        )

        metadata_template_container_project_1 = MetadataTemplateContainer.objects.create(
            name="Project metadata template container 1",
            assigned_to_content_type=project_1.get_content_type(),
            assigned_to_object_id=project_1.pk,
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_project_1,
        )

        project_1.metadata_template = metadata_template_container_project_1.latest_metadata_template
        project_1.save()

        folder_1 = project_1.folders.first()

        metadata_template_container_folder_1 = MetadataTemplateContainer.objects.create(
            name="Folder metadata template container 1",
            assigned_to_content_type=folder_1.get_content_type(),
            assigned_to_object_id=folder_1.pk,
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_folder_1,
        )

        folder_1.metadata_template = metadata_template_container_folder_1.latest_metadata_template
        folder_1.save()

        uploads_dataset_1 = UploadsDataset.objects.create(
            name="Dataset 1",
            folder=folder_1,
            publication_date=timezone.now(),
        )

        metadata_template_container_uploads_dataset_1 = MetadataTemplateContainer.objects.create(
            name="Uploads dataset metadata template container 1",
            assigned_to_content_type=uploads_dataset_1.get_content_type(),
            assigned_to_object_id=uploads_dataset_1.pk,
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_uploads_dataset_1,
        )

        uploads_dataset_1.metadata_template = metadata_template_container_uploads_dataset_1.latest_metadata_template
        uploads_dataset_1.save()

        uploads_version_1 = UploadsVersion.objects.create(
            dataset=uploads_dataset_1,
            publication_date=timezone.now(),
        )

        uploads_dataset_1.status = UploadsDataset.Status.FINISHED
        uploads_dataset_1.metadata_upgrade_availability_status = UploadsDataset.Status.FINISHED
        uploads_dataset_1.save()

        assert uploads_version_1.metadata_templates.count() == 0
        assert uploads_dataset_1.is_latest_version_upgradeable() is True
        assert uploads_dataset_1.status == UploadsDataset.Status.FINISHED

        uploads_version_1.link_currently_active_metadata_templates()

        assert uploads_version_1.metadata_templates.count() == 3
        assert uploads_dataset_1.is_latest_version_upgradeable() is False
        assert uploads_dataset_1.status == UploadsDataset.Status.FINISHED

        metadata_template = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_project_1,
        )

        uploads_dataset_1.refresh_from_db()
        assert uploads_dataset_1.is_latest_version_upgradeable() is False
        assert uploads_dataset_1.status == UploadsDataset.Status.FINISHED
        assert uploads_dataset_1.metadata_upgrade_availability_status == UploadsDataset.Status.SCHEDULED

        assert uploads_dataset_1.is_latest_version_upgradeable(max_upgrade=True) is True
        assert uploads_dataset_1.status == UploadsDataset.Status.FINISHED

        uploads_dataset_1.metadata_upgrade_availability_status = UploadsDataset.Status.FINISHED
        uploads_dataset_1.save()

        project_1.metadata_template = metadata_template
        project_1.save()

        uploads_dataset_1.refresh_from_db()
        assert uploads_dataset_1.is_latest_version_upgradeable() is True
        assert uploads_dataset_1.status == UploadsDataset.Status.FINISHED
        assert uploads_dataset_1.metadata_upgrade_availability_status == UploadsDataset.Status.SCHEDULED

        assert uploads_dataset_1.is_latest_version_upgradeable(max_upgrade=True) is True
        assert uploads_dataset_1.status == UploadsDataset.Status.FINISHED

        project_1.metadata_template = None
        project_1.save()

        uploads_dataset_1.refresh_from_db()
        assert uploads_dataset_1.is_latest_version_upgradeable() is True
        assert uploads_dataset_1.status == UploadsDataset.Status.FINISHED
        assert uploads_dataset_1.metadata_upgrade_availability_status == UploadsDataset.Status.SCHEDULED

    def test_is_latest_version_upgradeable_folder_template(self):
        """
        Ensure we get the correct information if a metadata template is being upgradeable.
        """
        project_1 = Project.objects.create(
            name="Project 1",
        )

        metadata_template_container_project_1 = MetadataTemplateContainer.objects.create(
            name="Project metadata template container 1",
            assigned_to_content_type=project_1.get_content_type(),
            assigned_to_object_id=project_1.pk,
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_project_1,
        )

        project_1.metadata_template = metadata_template_container_project_1.latest_metadata_template
        project_1.save()

        folder_1 = project_1.folders.first()

        metadata_template_container_folder_1 = MetadataTemplateContainer.objects.create(
            name="Folder metadata template container 1",
            assigned_to_content_type=folder_1.get_content_type(),
            assigned_to_object_id=folder_1.pk,
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_folder_1,
        )

        folder_1.metadata_template = metadata_template_container_folder_1.latest_metadata_template
        folder_1.save()

        uploads_dataset_1 = UploadsDataset.objects.create(
            name="Dataset 1",
            folder=folder_1,
            publication_date=timezone.now(),
        )

        metadata_template_container_uploads_dataset_1 = MetadataTemplateContainer.objects.create(
            name="Uploads dataset metadata template container 1",
            assigned_to_content_type=uploads_dataset_1.get_content_type(),
            assigned_to_object_id=uploads_dataset_1.pk,
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_uploads_dataset_1,
        )

        uploads_dataset_1.metadata_template = metadata_template_container_uploads_dataset_1.latest_metadata_template
        uploads_dataset_1.save()

        uploads_version_1 = UploadsVersion.objects.create(
            dataset=uploads_dataset_1,
            publication_date=timezone.now(),
        )

        uploads_dataset_1.status = UploadsDataset.Status.FINISHED
        uploads_dataset_1.metadata_upgrade_availability_status = UploadsDataset.Status.FINISHED
        uploads_dataset_1.save()

        assert uploads_version_1.metadata_templates.count() == 0
        assert uploads_dataset_1.is_latest_version_upgradeable() is True
        assert uploads_dataset_1.status == UploadsDataset.Status.FINISHED

        uploads_version_1.link_currently_active_metadata_templates()

        assert uploads_version_1.metadata_templates.count() == 3
        assert uploads_dataset_1.is_latest_version_upgradeable() is False
        assert uploads_dataset_1.status == UploadsDataset.Status.FINISHED

        metadata_template = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_folder_1,
        )

        folder_1.refresh_from_db()
        assert folder_1.metadata_template is not None

        uploads_dataset_1.refresh_from_db()
        assert uploads_dataset_1.is_latest_version_upgradeable() is False
        assert uploads_dataset_1.status == UploadsDataset.Status.FINISHED
        assert uploads_dataset_1.metadata_upgrade_availability_status == UploadsDataset.Status.SCHEDULED

        assert uploads_dataset_1.is_latest_version_upgradeable(max_upgrade=True) is True
        assert uploads_dataset_1.status == UploadsDataset.Status.FINISHED

        uploads_dataset_1.metadata_upgrade_availability_status = UploadsDataset.Status.FINISHED
        uploads_dataset_1.save()

        folder_1.metadata_template = metadata_template
        folder_1.save()

        uploads_dataset_1.refresh_from_db()
        assert uploads_dataset_1.is_latest_version_upgradeable() is True
        assert uploads_dataset_1.status == UploadsDataset.Status.FINISHED
        assert uploads_dataset_1.metadata_upgrade_availability_status == UploadsDataset.Status.SCHEDULED

        assert uploads_dataset_1.is_latest_version_upgradeable(max_upgrade=True) is True
        assert uploads_dataset_1.status == UploadsDataset.Status.FINISHED

        folder_1.metadata_template = None
        folder_1.save()

        uploads_dataset_1.refresh_from_db()
        assert uploads_dataset_1.is_latest_version_upgradeable() is True
        assert uploads_dataset_1.status == UploadsDataset.Status.FINISHED
        assert uploads_dataset_1.metadata_upgrade_availability_status == UploadsDataset.Status.SCHEDULED

    def test_is_latest_version_upgradeable_removed_global_template_reference(self):
        """
        Ensure stale global template links become upgradeable when the active assignment is removed.
        """
        project_1 = Project.objects.create(
            name="Project 1",
        )
        folder_1 = project_1.folders.first()

        uploads_dataset_1 = UploadsDataset.objects.create(
            name="Dataset 1",
            folder=folder_1,
            publication_date=timezone.now(),
        )

        metadata_template_container_1 = MetadataTemplateContainer.objects.create(
            name="Global metadata template container 1",
        )
        metadata_template_1 = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_1,
        )

        uploads_dataset_1.metadata_template = metadata_template_1
        uploads_dataset_1.save()

        uploads_version_1 = UploadsVersion.objects.create(
            dataset=uploads_dataset_1,
            publication_date=timezone.now(),
        )
        uploads_version_1.link_currently_active_metadata_templates()

        uploads_dataset_1.refresh_from_db()
        assert uploads_dataset_1.is_latest_version_upgradeable() is False

        uploads_dataset_1.metadata_template = None
        uploads_dataset_1.save()

        uploads_dataset_1.refresh_from_db()
        assert uploads_dataset_1.is_latest_version_upgradeable() is True

    def test_is_latest_version_upgradeable_uploads_dataset_template(self):
        """
        Ensure we get the correct information if a metadata template is being upgradeable.
        """
        project_1 = Project.objects.create(
            name="Project 1",
        )

        metadata_template_container_project_1 = MetadataTemplateContainer.objects.create(
            name="Project metadata template container 1",
            assigned_to_content_type=project_1.get_content_type(),
            assigned_to_object_id=project_1.pk,
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_project_1,
        )

        project_1.metadata_template = metadata_template_container_project_1.latest_metadata_template
        project_1.save()

        folder_1 = project_1.folders.first()

        metadata_template_container_folder_1 = MetadataTemplateContainer.objects.create(
            name="Folder metadata template container 1",
            assigned_to_content_type=folder_1.get_content_type(),
            assigned_to_object_id=folder_1.pk,
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_folder_1,
        )

        folder_1.metadata_template = metadata_template_container_folder_1.latest_metadata_template
        folder_1.save()

        uploads_dataset_1 = UploadsDataset.objects.create(
            name="Dataset 1",
            folder=folder_1,
            publication_date=timezone.now(),
        )

        metadata_template_container_uploads_dataset_1 = MetadataTemplateContainer.objects.create(
            name="Uploads dataset metadata template container 1",
            assigned_to_content_type=uploads_dataset_1.get_content_type(),
            assigned_to_object_id=uploads_dataset_1.pk,
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_uploads_dataset_1,
        )

        uploads_dataset_1.metadata_template = metadata_template_container_uploads_dataset_1.latest_metadata_template
        uploads_dataset_1.save()

        uploads_version_1 = UploadsVersion.objects.create(
            dataset=uploads_dataset_1,
            publication_date=timezone.now(),
        )

        uploads_dataset_1.status = UploadsDataset.Status.FINISHED
        uploads_dataset_1.metadata_upgrade_availability_status = UploadsDataset.Status.FINISHED
        uploads_dataset_1.save()

        assert uploads_version_1.metadata_templates.count() == 0
        assert uploads_dataset_1.is_latest_version_upgradeable() is True
        assert uploads_dataset_1.status == UploadsDataset.Status.FINISHED

        uploads_version_1.link_currently_active_metadata_templates()

        assert uploads_version_1.metadata_templates.count() == 3
        assert uploads_dataset_1.is_latest_version_upgradeable() is False
        assert uploads_dataset_1.status == UploadsDataset.Status.FINISHED

        metadata_template = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_uploads_dataset_1,
        )

        uploads_dataset_1.refresh_from_db()
        assert uploads_dataset_1.is_latest_version_upgradeable() is False
        assert uploads_dataset_1.status == UploadsDataset.Status.FINISHED
        assert uploads_dataset_1.metadata_upgrade_availability_status == UploadsDataset.Status.SCHEDULED

        assert uploads_dataset_1.is_latest_version_upgradeable(max_upgrade=True) is True
        assert uploads_dataset_1.status == UploadsDataset.Status.FINISHED

        uploads_dataset_1.metadata_upgrade_availability_status = UploadsDataset.Status.FINISHED
        uploads_dataset_1.save()

        uploads_dataset_1.metadata_template = metadata_template
        uploads_dataset_1.save()

        uploads_dataset_1.refresh_from_db()
        assert uploads_dataset_1.is_latest_version_upgradeable() is True
        assert uploads_dataset_1.status == UploadsDataset.Status.FINISHED
        assert uploads_dataset_1.metadata_upgrade_availability_status == UploadsDataset.Status.SCHEDULED

        assert uploads_dataset_1.is_latest_version_upgradeable(max_upgrade=True) is True
        assert uploads_dataset_1.status == UploadsDataset.Status.FINISHED

    def test_delete(self, initial_users):
        """
        Ensure we can delete a dataset as long as it has not been published.
        """
        set_request_for_user(initial_users["user_1"])

        dataset_pk = self.uploads_dataset_1.pk

        self.uploads_dataset_1.delete()
        assert not UploadsDataset.objects.filter(pk=dataset_pk).exists()

        uploads_dataset_2 = UploadsDataset.objects.create(
            name="Dataset 2",
        )
        uploads_dataset_2_pk = uploads_dataset_2.pk

        version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )
        version_file_pk = version_file.pk
        version_file_path = version_file.uploaded_file.path

        uploads_version = UploadsVersion.objects.create(
            dataset=uploads_dataset_2,
            version_file=version_file,
        )
        uploads_version_pk = uploads_version.pk

        with TestCase.captureOnCommitCallbacks(execute=True):
            uploads_dataset_2.delete()

        assert not UploadsDataset.objects.filter(pk=uploads_dataset_2_pk).exists()
        assert not UploadsVersion.objects.filter(pk=uploads_version_pk).exists()
        assert not UploadsVersionFile.objects.filter(pk=version_file_pk).exists()
        assert not os.path.exists(version_file_path)

        uploads_dataset_3 = UploadsDataset.objects.create(
            name="Dataset 3",
        )
        uploads_dataset_3_pk = uploads_dataset_3.pk

        version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )
        version_file_pk = version_file.pk
        version_file_path = version_file.uploaded_file.path

        uploads_version = UploadsVersion.objects.create(
            dataset=uploads_dataset_3,
            version_file=version_file,
        )
        uploads_version_pk = uploads_version.pk

        uploads_dataset_3.lock()
        with TestCase.captureOnCommitCallbacks(execute=True):
            uploads_dataset_3.delete()

        assert not UploadsDataset.objects.filter(pk=uploads_dataset_3_pk).exists()
        assert not UploadsVersion.objects.filter(pk=uploads_version_pk).exists()
        assert not UploadsVersionFile.objects.filter(pk=version_file_pk).exists()
        assert not os.path.exists(version_file_path)

    def test_hard_delete(self, initial_users):
        """
        Ensure we can hard delete a dataset with the appropriate permission.
        """
        user_1 = User.objects.get(pk=initial_users["user_1"].pk)
        assert user_1.can_hard_delete_datasets is False

        version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        UploadsVersion.objects.create(
            dataset=self.uploads_dataset_1,
            version_file=version_file,
        )

        self.uploads_dataset_1.publication_date = timezone.now()
        self.uploads_dataset_1.save()

        assert UploadsDataset.objects.count() == 1
        assert self.uploads_dataset_1.publication_date is not None
        assert UploadsVersion.objects.count() == 1
        assert UploadsVersionFile.objects.count() == 1

        with transaction.atomic():
            with pytest.raises(PermissionDenied):
                self.uploads_dataset_1.delete()

        assert UploadsDataset.objects.count() == 1
        assert self.uploads_dataset_1.publication_date is not None
        assert UploadsVersion.objects.count() == 1
        assert UploadsVersionFile.objects.count() == 1

        initial_users["user_1"].can_hard_delete_datasets = True
        initial_users["user_1"].save()

        user_1.refresh_from_db()
        assert user_1.can_hard_delete_datasets is True

        self.uploads_dataset_1.delete()

        assert UploadsDataset.objects.count() == 0
        assert UploadsVersion.objects.count() == 0
        assert UploadsVersionFile.objects.count() == 0

    def test_delete_protection(self, initial_users):
        """
        Ensure we can't delete a dataset if it has been published.
        """
        set_request_for_user(initial_users["user_1"])

        self.uploads_dataset_1.publication_date = timezone.now()
        self.uploads_dataset_1.save()

        uploads_dataset_1_pk = self.uploads_dataset_1.pk

        self.uploads_dataset_1.lock()

        set_request_for_user(initial_users["user_2"])

        with transaction.atomic():
            with pytest.raises(PermissionDenied):
                self.uploads_dataset_1.delete()

        set_request_for_user(initial_users["user_1"])

        self.uploads_dataset_1.unlock()
        self.uploads_dataset_1.deletable_until_date = timezone.now() - timezone.timedelta(hours=1)
        self.uploads_dataset_1.save()

        with transaction.atomic():
            with pytest.raises(PermissionDenied):
                self.uploads_dataset_1.delete()

        assert UploadsDataset.objects.filter(pk=uploads_dataset_1_pk).exists()

        self.uploads_dataset_1.deletable_until_date = None
        self.uploads_dataset_1.save()

        with transaction.atomic():
            with pytest.raises(PermissionDenied):
                self.uploads_dataset_1.delete()

        assert UploadsDataset.objects.filter(pk=uploads_dataset_1_pk).exists()

        self.uploads_dataset_1.deletable_until_date = timezone.now() + timezone.timedelta(
            minutes=int(get_dbsettings_value("DATASET_DELETABLE_WITHIN_THRESHOLD")),
        )
        self.uploads_dataset_1.save()
        self.uploads_dataset_1.delete()

        assert not UploadsDataset.objects.filter(pk=uploads_dataset_1_pk).exists()

        uploads_dataset_2 = UploadsDataset.objects.create(
            name="Dataset 2",
            publication_date=timezone.now(),
            deletable_until_date=timezone.now() - timezone.timedelta(hours=1),
        )

        version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        uploads_version = UploadsVersion.objects.create(
            dataset=uploads_dataset_2,
            version_file=version_file,
        )

        uploads_dataset_2_pk = uploads_dataset_2.pk
        version_file_pk = version_file.pk
        uploads_version_pk = uploads_version.pk

        with transaction.atomic():
            with pytest.raises(PermissionDenied):
                uploads_dataset_2.delete()

        assert UploadsDataset.objects.filter(pk=uploads_dataset_2_pk).exists()
        assert UploadsVersion.objects.filter(pk=uploads_version.pk).exists()
        assert UploadsVersionFile.objects.filter(pk=version_file.pk).exists()

        with transaction.atomic():
            with pytest.raises(PermissionDenied):
                uploads_version.delete()

        assert UploadsDataset.objects.filter(pk=uploads_dataset_2_pk).exists()
        assert UploadsVersion.objects.filter(pk=uploads_version.pk).exists()
        assert UploadsVersionFile.objects.filter(pk=version_file.pk).exists()

        with transaction.atomic():
            with pytest.raises(PermissionDenied):
                version_file.delete()

        assert UploadsDataset.objects.filter(pk=uploads_dataset_2_pk).exists()
        assert UploadsVersion.objects.filter(pk=uploads_version.pk).exists()
        assert UploadsVersionFile.objects.filter(pk=version_file.pk).exists()

        uploads_dataset_2.deletable_until_date = timezone.now() + timezone.timedelta(
            minutes=int(get_dbsettings_value("DATASET_DELETABLE_WITHIN_THRESHOLD")),
        )
        uploads_dataset_2.save()
        uploads_dataset_2.delete()

        assert not UploadsDataset.objects.filter(pk=uploads_dataset_2_pk).exists()
        assert not UploadsVersion.objects.filter(pk=uploads_version_pk).exists()
        assert not UploadsVersionFile.objects.filter(pk=version_file_pk).exists()

    def test_delete_expired_locked_dataset(self):
        """
        Ensure expired, locked datasets can be deleted.
        """
        self.uploads_dataset_1.lock()
        assert self.uploads_dataset_1.locked is True

        self.uploads_dataset_1.creation_date = timezone.now() - timedelta(
            days=settings.DRAFT_FILES_MAX_LIFETIME,
        )
        self.uploads_dataset_1.publication_date = None
        self.uploads_dataset_1.save()

        assert self.uploads_dataset_1.is_expired is True

        remove_expired_dataset_drafts()

        assert UploadsDataset.objects.count() == 0

    def test_check_expired_draft(self, initial_users):
        """
        Ensure we can determine if a dataset in the drafts section has expired.
        """
        assert self.uploads_dataset_1.is_expired is False
        assert self.uploads_dataset_1.expiry_date is not None
        assert self.uploads_dataset_1.expiry_date > timezone.now()

        self.uploads_dataset_1.creation_date = timezone.now() - timedelta(days=settings.DRAFT_FILES_MAX_LIFETIME)
        self.uploads_dataset_1.save()

        assert self.uploads_dataset_1.is_expired is True
        assert self.uploads_dataset_1.expiry_date is not None
        assert self.uploads_dataset_1.expiry_date < timezone.now()

        self.uploads_dataset_1.publication_date = timezone.now()
        self.uploads_dataset_1.save()

        assert self.uploads_dataset_1.is_expired is False
        assert self.uploads_dataset_1.expiry_date is None

        self.uploads_dataset_1.creation_date = timezone.now() - timedelta(days=settings.DRAFT_FILES_MAX_LIFETIME)
        self.uploads_dataset_1.save()

        assert self.uploads_dataset_1.is_expired is False
        assert self.uploads_dataset_1.expiry_date is None
        assert UploadsDataset.objects.count() == 1

        remove_expired_dataset_drafts()
        assert UploadsDataset.objects.count() == 1

        self.uploads_dataset_1.creation_date = timezone.now() - timedelta(days=settings.DRAFT_FILES_MAX_LIFETIME)
        self.uploads_dataset_1.save()

        remove_expired_dataset_drafts()
        assert UploadsDataset.objects.count() == 1

        self.uploads_dataset_1.creation_date = timezone.now() - timedelta(days=settings.DRAFT_FILES_MAX_LIFETIME)
        self.uploads_dataset_1.publication_date = None
        self.uploads_dataset_1.save()

        remove_expired_dataset_drafts()
        assert UploadsDataset.objects.count() == 0

    def test_display_name(self):
        """
        Ensure the display name gets updated correctly.
        """
        assert self.uploads_dataset_1.display_name == self.uploads_dataset_1.name

        self.uploads_dataset_1.name = None
        self.uploads_dataset_1.save()
        assert self.uploads_dataset_1.display_name == str(self.uploads_dataset_1.pk)

        uploads_version_1 = UploadsVersion.objects.create(
            dataset=self.uploads_dataset_1,
        )
        assert self.uploads_dataset_1.latest_version == uploads_version_1
        assert self.uploads_dataset_1.display_name == str(self.uploads_dataset_1.pk)

        uploads_version_1.name = "v1"
        uploads_version_1.save()
        assert self.uploads_dataset_1.display_name == str(self.uploads_dataset_1.pk)

        uploads_version_file_1 = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )
        uploads_version_2 = UploadsVersion.objects.create(
            dataset=self.uploads_dataset_1,
            version_file=uploads_version_file_1,
        )
        assert self.uploads_dataset_1.latest_version == uploads_version_2
        assert self.uploads_dataset_1.display_name == uploads_version_file_1.name

        metadata = set_metadata(
            assigned_to_content_type=uploads_version_file_1.get_content_type(),
            assigned_to_object_id=uploads_version_file_1.pk,
            custom_key="FILE_NAME",
            value=uploads_version_file_1.name,
            read_only=True,
        )
        assert self.uploads_dataset_1.display_name == metadata.get_value()

        metadata.set_value("random_file_name.jpg")
        self.uploads_dataset_1.refresh_from_db()
        assert self.uploads_dataset_1.display_name == "random_file_name.jpg"

        uploads_version_file_2 = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )
        uploads_version_1.version_file = uploads_version_file_2
        uploads_version_1.save()
        self.uploads_dataset_1.refresh_from_db()
        assert self.uploads_dataset_1.display_name == "random_file_name.jpg"

        uploads_version_2.version_file = uploads_version_file_2
        uploads_version_2.save()
        self.uploads_dataset_1.refresh_from_db()
        assert self.uploads_dataset_1.display_name == uploads_version_file_2.name

        metadata.assigned_to_object_id = uploads_version_file_2.pk
        metadata.save()
        self.uploads_dataset_1.refresh_from_db()
        assert self.uploads_dataset_1.display_name == "random_file_name.jpg"

        metadata.delete()
        self.uploads_dataset_1.refresh_from_db()
        assert not Metadata.objects.filter(
            assigned_to_content_type=uploads_version_file_1.get_content_type(),
            assigned_to_object_id=uploads_version_file_1.pk,
            custom_key="FILE_NAME",
        ).exists()
        assert self.uploads_dataset_1.display_name == uploads_version_file_2.name

        uploads_version_file_3 = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )
        uploads_version_3 = UploadsVersion.objects.create(
            dataset=self.uploads_dataset_1,
            version_file=uploads_version_file_3,
        )
        assert self.uploads_dataset_1.latest_version == uploads_version_3
        assert self.uploads_dataset_1.display_name == uploads_version_file_3.name

        self.uploads_dataset_1.name = "Dataset 1"
        self.uploads_dataset_1.save()
        self.uploads_dataset_1.refresh_from_db()
        assert self.uploads_dataset_1.display_name == "Dataset 1"

    def test_restore(self):
        """
        Ensure we can restore an uploads version.
        """
        metadata_template_container_1 = MetadataTemplateContainer.objects.create(
            name="Metadata template container 1",
        )

        metadata_template_1 = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_1,
        )

        assert self.uploads_dataset_1.uploads_versions.count() == 0

        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        uploads_version_1 = UploadsVersion.objects.create(
            dataset=self.uploads_dataset_1,
            version_file=uploads_version_file,
        )

        uploads_version_1.metadata_templates.add(metadata_template_1)

        assert uploads_version_1.metadata.count() == 0
        assert uploads_version_1.metadata_templates.count() == 1
        assert self.uploads_dataset_1.uploads_versions.count() == 1
        assert self.uploads_dataset_1.latest_version == uploads_version_1

        with pytest.raises(PermissionDenied):
            self.uploads_dataset_1.restore_version(
                uploads_version=uploads_version_1,
            )

        uploads_version_2 = UploadsVersion.objects.create(
            dataset=self.uploads_dataset_1,
            version_file=uploads_version_file,
        )

        set_metadata(
            assigned_to_content_type=uploads_version_2.get_content_type(),
            assigned_to_object_id=uploads_version_2.pk,
            custom_key="custom_key_1",
            value="custom_value_1",
        )

        assert uploads_version_2.metadata.count() == 1
        assert uploads_version_2.metadata_templates.count() == 0
        assert self.uploads_dataset_1.uploads_versions.count() == 2
        assert self.uploads_dataset_1.latest_version == uploads_version_2

        uploads_version_3 = self.uploads_dataset_1.restore_version(
            uploads_version=uploads_version_1,
        )

        assert uploads_version_3.metadata.count() == 0
        assert uploads_version_3.metadata_templates.count() == 1
        assert self.uploads_dataset_1.uploads_versions.count() == 3
        assert self.uploads_dataset_1.latest_version == uploads_version_3
        assert uploads_version_1 != uploads_version_3
        assert uploads_version_3.restored_uploads_version == uploads_version_1

        uploads_version_4 = self.uploads_dataset_1.restore_version(
            uploads_version=uploads_version_2,
        )

        assert uploads_version_4.metadata.count() == 1
        assert uploads_version_4.metadata_templates.count() == 0
        assert self.uploads_dataset_1.uploads_versions.count() == 4
        assert self.uploads_dataset_1.latest_version == uploads_version_4
        assert uploads_version_2 != uploads_version_4
        assert uploads_version_4.restored_uploads_version == uploads_version_2

        with pytest.raises(PermissionDenied):
            self.uploads_dataset_1.restore_version(
                uploads_version=uploads_version_4,
            )

        uploads_dataset_2 = UploadsDataset.objects.create(
            name="Dataset 2",
        )

        uploads_version_5 = UploadsVersion.objects.create(
            dataset=uploads_dataset_2,
        )

        with pytest.raises(PermissionDenied):
            self.uploads_dataset_1.restore_version(
                uploads_version=uploads_version_5,
            )

        assert self.uploads_dataset_1.uploads_versions.count() == 4
        assert self.uploads_dataset_1.latest_version == uploads_version_4

    def test_metadata_template_fields_ordering(self):
        """
        Ensure we get the correct ordering of metadata template fields.
        """
        metadata_template_container_1 = MetadataTemplateContainer.objects.create(
            name="metadata_template_container_1",
        )

        metadata_template_1 = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_1,
        )

        metadata_template_1_metadata_template_field_1 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_1,
            custom_key="metadata_template_1_metadata_template_field_1",
            order=0,
        )

        metadata_template_1_metadata_template_field_2 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_1,
            custom_key="metadata_template_1_metadata_template_field_2",
            order=1,
        )

        assert MetadataTemplateContainer.objects.count() == 1
        assert MetadataTemplate.objects.count() == 1
        assert MetadataTemplateField.objects.count() == 2

        metadata_template_container_2 = MetadataTemplateContainer.objects.create(
            name="metadata_template_container_2",
        )

        metadata_template_2 = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_2,
        )

        metadata_template_2_metadata_template_field_1 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_2,
            custom_key="metadata_template_2_metadata_template_field_1",
            order=0,
        )

        metadata_template_2_metadata_template_field_2 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_2,
            custom_key="metadata_template_2_metadata_template_field_2",
            order=1,
        )

        assert MetadataTemplateContainer.objects.count() == 2
        assert MetadataTemplate.objects.count() == 2
        assert MetadataTemplateField.objects.count() == 4

        metadata_template_container_3 = MetadataTemplateContainer.objects.create(
            name="metadata_template_container_3",
        )

        metadata_template_3 = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_3,
        )

        metadata_template_3_metadata_template_field_1 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_3,
            custom_key="metadata_template_3_metadata_template_field_1",
            order=0,
        )

        metadata_template_3_metadata_template_field_2 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_3,
            custom_key="metadata_template_2_metadata_template_field_2",
            order=1,
        )

        assert MetadataTemplateContainer.objects.count() == 3
        assert MetadataTemplate.objects.count() == 3
        assert MetadataTemplateField.objects.count() == 6

        project_1 = Project.objects.create(
            name="Project 1",
        )
        project_1.metadata_template = metadata_template_1
        project_1.save()

        folder_1 = project_1.folders.first()
        folder_1.metadata_template = metadata_template_2
        folder_1.save()

        self.uploads_dataset_1.folder = folder_1
        self.uploads_dataset_1.metadata_template = metadata_template_3
        self.uploads_dataset_1.publication_date = timezone.now()
        self.uploads_dataset_1.save()

        metadata_template_fields = self.uploads_dataset_1.get_all_metadata_template_fields(
            include_uploads_dataset_fields=False,
        )
        assert len(metadata_template_fields) == 4

        assert metadata_template_fields[0].pk == metadata_template_1_metadata_template_field_1.pk
        assert metadata_template_fields[0].order == 0
        assert metadata_template_fields[1].pk == metadata_template_1_metadata_template_field_2.pk
        assert metadata_template_fields[1].order == 1
        assert metadata_template_fields[2].pk == metadata_template_2_metadata_template_field_1.pk
        assert metadata_template_fields[2].order == 2
        assert metadata_template_fields[3].pk == metadata_template_2_metadata_template_field_2.pk
        assert metadata_template_fields[3].order == 3

        metadata_template_fields = self.uploads_dataset_1.get_all_metadata_template_fields(
            include_uploads_dataset_fields=True,
        )
        assert len(metadata_template_fields) == 6

        assert metadata_template_fields[0].pk == metadata_template_1_metadata_template_field_1.pk
        assert metadata_template_fields[0].order == 0
        assert metadata_template_fields[1].pk == metadata_template_1_metadata_template_field_2.pk
        assert metadata_template_fields[1].order == 1
        assert metadata_template_fields[2].pk == metadata_template_2_metadata_template_field_1.pk
        assert metadata_template_fields[2].order == 2
        assert metadata_template_fields[3].pk == metadata_template_2_metadata_template_field_2.pk
        assert metadata_template_fields[3].order == 3
        assert metadata_template_fields[4].pk == metadata_template_3_metadata_template_field_1.pk
        assert metadata_template_fields[4].order == 4
        assert metadata_template_fields[5].pk == metadata_template_3_metadata_template_field_2.pk
        assert metadata_template_fields[5].order == 5

        uploads_dataset_2 = UploadsDataset.objects.create()
        uploads_dataset_2.metadata_template = metadata_template_3
        uploads_dataset_2.save()

        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        uploads_dataset_2_uploads_version_1 = UploadsVersion.objects.create(
            dataset=uploads_dataset_2,
            version_file=uploads_version_file,
        )

        uploads_dataset_2_uploads_version_1_metadata_1 = set_metadata(
            assigned_to_content_type=uploads_dataset_2_uploads_version_1.get_content_type(),
            assigned_to_object_id=uploads_dataset_2_uploads_version_1.pk,
            custom_key="uploads_dataset_2_uploads_version_1_metadata_1",
            value="uploads_dataset_2_uploads_version_1_metadata_1_value",
            order=0,
        )

        uploads_dataset_2_uploads_version_1_metadata_2 = set_metadata(
            assigned_to_content_type=uploads_dataset_2_uploads_version_1.get_content_type(),
            assigned_to_object_id=uploads_dataset_2_uploads_version_1.pk,
            custom_key="uploads_dataset_2_uploads_version_1_metadata_2",
            value="uploads_dataset_2_uploads_version_1_metadata_2_value",
            order=1,
        )

        metadata = uploads_dataset_2_uploads_version_1.metadata.all()
        assert len(metadata) == 2
        assert uploads_dataset_2.latest_version == uploads_dataset_2_uploads_version_1

        uploads_dataset_2.publish(folder=folder_1)
        assert uploads_dataset_2.latest_version != uploads_dataset_2_uploads_version_1

        metadata = uploads_dataset_2.latest_version.metadata.all()
        assert len(metadata) == 8

        assert metadata[0].custom_key == uploads_dataset_2_uploads_version_1_metadata_1.custom_key
        assert metadata[0].order == 0
        assert metadata[1].custom_key == uploads_dataset_2_uploads_version_1_metadata_2.custom_key
        assert metadata[1].order == 1
        assert metadata[2].custom_key == metadata_template_1_metadata_template_field_1.custom_key
        assert metadata[2].order == 2
        assert metadata[3].custom_key == metadata_template_1_metadata_template_field_2.custom_key
        assert metadata[3].order == 3
        assert metadata[4].custom_key == metadata_template_2_metadata_template_field_1.custom_key
        assert metadata[4].order == 4
        assert metadata[5].custom_key == metadata_template_2_metadata_template_field_2.custom_key
        assert metadata[5].order == 5
        assert metadata[6].custom_key == metadata_template_3_metadata_template_field_1.custom_key
        assert metadata[6].order == 6
        assert metadata[7].custom_key == metadata_template_3_metadata_template_field_2.custom_key
        assert metadata[7].order == 7

        uploads_dataset_3 = UploadsDataset.objects.create()
        uploads_dataset_3.save()

        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        uploads_dataset_3_uploads_version_1 = UploadsVersion.objects.create(
            dataset=uploads_dataset_3,
            version_file=uploads_version_file,
        )

        uploads_dataset_3_uploads_version_1_metadata_1 = set_metadata(
            assigned_to_content_type=uploads_dataset_3_uploads_version_1.get_content_type(),
            assigned_to_object_id=uploads_dataset_3_uploads_version_1.pk,
            custom_key="uploads_dataset_3_uploads_version_1_metadata_1",
            value="uploads_dataset_3_uploads_version_1_metadata_1_value",
            order=0,
        )

        uploads_dataset_3_uploads_version_1_metadata_2 = set_metadata(
            assigned_to_content_type=uploads_dataset_3_uploads_version_1.get_content_type(),
            assigned_to_object_id=uploads_dataset_3_uploads_version_1.pk,
            custom_key="uploads_dataset_3_uploads_version_1_metadata_2",
            value="uploads_dataset_3_uploads_version_1_metadata_2_value",
            order=3,
        )

        metadata = uploads_dataset_3_uploads_version_1.metadata.all()
        assert len(metadata) == 2
        assert uploads_dataset_3.latest_version == uploads_dataset_3_uploads_version_1

        uploads_dataset_3.publish(folder=folder_1)
        assert uploads_dataset_3.latest_version != uploads_dataset_3_uploads_version_1

        metadata = uploads_dataset_3.latest_version.metadata.all()
        assert len(metadata) == 6

        assert metadata[0].custom_key == uploads_dataset_3_uploads_version_1_metadata_1.custom_key
        assert metadata[0].order == 0
        assert metadata[1].custom_key == uploads_dataset_3_uploads_version_1_metadata_2.custom_key
        assert metadata[1].order == 3
        assert metadata[2].custom_key == metadata_template_1_metadata_template_field_1.custom_key
        assert metadata[2].order == 4
        assert metadata[3].custom_key == metadata_template_1_metadata_template_field_2.custom_key
        assert metadata[3].order == 5
        assert metadata[4].custom_key == metadata_template_2_metadata_template_field_1.custom_key
        assert metadata[4].order == 6
        assert metadata[5].custom_key == metadata_template_2_metadata_template_field_2.custom_key
        assert metadata[5].order == 7

    def test_publish_with_missing_active_metadata_template_reference(self):
        """
        Ensure we can publish a dataset and if for whatever reason an active metadata template didn't get assigned
        it will be on publish
        """
        metadata_template_container_1 = MetadataTemplateContainer.objects.create(
            name="Metadata template container 1",
        )

        metadata_template_1 = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_1,
        )

        metadata_template_field_1 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_1,
            custom_key="metadata_template_field_1",
            field_type=MetadataFieldType.TEXT,
            mandatory=False,
        )
        metadata_template_field_1.set_value("Text")

        storage_1 = DynamicStorage.objects.get(
            default=True,
        )

        dataset_1 = UploadsDataset.objects.create(
            name="Dataset 1",
        )

        project = Project.objects.create(
            name="Test project",
        )

        folder = project.folders.first()
        folder.storage = storage_1
        folder.save()

        uploads_version_file_1 = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        uploads_version_1 = UploadsVersion.objects.create(
            dataset=dataset_1,
        )
        uploads_version_1.version_file = uploads_version_file_1
        uploads_version_1.save()

        dataset_1.publish(folder=folder.pk)

        assert dataset_1.latest_version == uploads_version_1
        assert dataset_1.latest_version.metadata_templates.count() == 0

        dataset_1.publication_date = None
        dataset_1.folder = None
        dataset_1.metadata_template = metadata_template_1
        dataset_1.save()

        assert dataset_1.latest_version == uploads_version_1
        assert dataset_1.latest_version.metadata_templates.count() == 0

        dataset_1.publish(folder=folder.pk)

        assert dataset_1.latest_version != uploads_version_1
        assert dataset_1.latest_version.metadata_templates.count() == 1

    def test_publish_is_atomic_when_version_publish_fails(self):
        """
        Ensure publishing is rolled back if publishing one of the versions fails.
        """
        project = Project.objects.create(
            name="Atomic publish project",
        )
        folder = project.folders.first()

        metadata_template_container = MetadataTemplateContainer.objects.create(
            name="Atomic publish metadata template container",
            assigned_to_content_type=folder.get_content_type(),
            assigned_to_object_id=folder.pk,
        )
        metadata_template = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container,
        )
        metadata_template_field = MetadataTemplateField.objects.create(
            metadata_template=metadata_template,
            custom_key="atomic_publish_metadata_template_field",
            field_type=MetadataFieldType.TEXT,
            order=0,
        )
        metadata_template_field.set_value("default")
        folder.metadata_template = metadata_template
        folder.save()

        dataset = UploadsDataset.objects.create(
            name="Atomic publish dataset",
            folder=folder,
        )

        UploadsVersion.objects.create(
            dataset=dataset,
            version_file=None,
            name="Broken version",
        )
        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )
        UploadsVersion.objects.create(
            dataset=dataset,
            version_file=uploads_version_file,
            name="Valid version",
        )

        assert dataset.uploads_versions.count() == 2
        assert dataset.publication_date is None

        with pytest.raises(ValidationError, match="has no file attached"):
            dataset.publish(folder=folder.pk)

        dataset.refresh_from_db()
        assert dataset.publication_date is None
        assert dataset.uploads_versions.count() == 2
        assert dataset.uploads_versions.filter(publication_date__isnull=False).count() == 0
        assert dataset.latest_version.metadata.filter(metadata_template_field=metadata_template_field).count() == 0

    def test_republish_does_not_duplicate_metadata_template_fields(self):
        """
        Ensure republishing does not duplicate metadata template fields.
        """
        project = Project.objects.create(
            name="Republish project",
        )
        folder = project.folders.first()

        metadata_template_container = MetadataTemplateContainer.objects.create(
            name="Republish metadata template container",
            assigned_to_content_type=folder.get_content_type(),
            assigned_to_object_id=folder.pk,
        )
        metadata_template = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container,
        )
        metadata_template_field = MetadataTemplateField.objects.create(
            metadata_template=metadata_template,
            custom_key="republish_metadata_template_field",
            field_type=MetadataFieldType.TEXT,
            order=0,
        )
        metadata_template_field.set_value("default")
        folder.metadata_template = metadata_template
        folder.save()

        dataset = UploadsDataset.objects.create(
            name="Republish dataset",
            folder=folder,
        )
        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )
        UploadsVersion.objects.create(
            dataset=dataset,
            version_file=uploads_version_file,
            name="Initial version",
        )

        dataset.publish(folder=folder.pk)
        dataset.refresh_from_db()
        assert dataset.latest_version.metadata.filter(metadata_template_field=metadata_template_field).count() == 1
        assert dataset.uploads_versions.count() == 2

        dataset.publication_date = None
        dataset.save()
        dataset.publish(folder=folder.pk)

        dataset.refresh_from_db()
        assert dataset.uploads_versions.count() == 2
        assert dataset.latest_version.metadata.filter(metadata_template_field=metadata_template_field).count() == 1

    def test_upgrade_latest_version_metadata(self):
        """
        Ensure we can automatically upgrade metadata on datasets to currently active metadata template versions.
        """
        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        uploads_version_1 = UploadsVersion.objects.create(
            dataset=self.uploads_dataset_1,
            version_file=uploads_version_file,
        )

        metadata_template_container_1 = MetadataTemplateContainer.objects.create(
            name="metadata_template_container_1",
        )

        metadata_template_1_v1 = MetadataTemplate.objects.create(
            name="metadata_template_1_v1",
            metadata_template_container=metadata_template_container_1,
        )

        metadata_template_1_v1_metadata_template_field_1 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_1_v1,
            custom_key="metadata_template_1_v1_metadata_template_field_1",
            field_type=MetadataFieldType.TEXT,
            order=0,
        )
        metadata_template_1_v1_metadata_template_field_1.set_value("1337")

        metadata_template_1_v1_metadata_template_field_2 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_1_v1,
            custom_key="metadata_template_1_v1_metadata_template_field_2",
            field_type=MetadataFieldType.INTEGER,
            order=1,
        )
        metadata_template_1_v1_metadata_template_field_2.set_value("1337")

        assert MetadataTemplateContainer.objects.count() == 1
        assert MetadataTemplate.objects.count() == 1
        assert MetadataTemplateField.objects.count() == 2

        metadata_template_container_2 = MetadataTemplateContainer.objects.create(
            name="metadata_template_container_2",
        )

        metadata_template_2_v1 = MetadataTemplate.objects.create(
            name="metadata_template_2_v1",
            metadata_template_container=metadata_template_container_2,
        )

        metadata_template_2_v1_metadata_template_field_1 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_2_v1,
            custom_key="metadata_template_2_v1_metadata_template_field_1",
            field_type=MetadataFieldType.DECIMAL,
            order=0,
        )
        metadata_template_2_v1_metadata_template_field_1.set_value("13.37")

        metadata_template_2_v1_metadata_template_field_2 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_2_v1,
            custom_key="metadata_template_2_v1_metadata_template_field_2",
            field_type=MetadataFieldType.SELECTION,
            config=dict(
                options=[
                    "Option 1",
                    "Option 2",
                    "Option 3",
                ],
            ),
            order=1,
        )
        metadata_template_2_v1_metadata_template_field_2.set_value("Option 1")

        assert MetadataTemplateContainer.objects.count() == 2
        assert MetadataTemplate.objects.count() == 2
        assert MetadataTemplateField.objects.count() == 4

        metadata_template_container_3 = MetadataTemplateContainer.objects.create(
            name="metadata_template_container_3",
        )

        metadata_template_3_v1 = MetadataTemplate.objects.create(
            name="metadata_template_3_v1",
            metadata_template_container=metadata_template_container_3,
        )

        metadata_template_3_v1_metadata_template_field_1 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_3_v1,
            custom_key="metadata_template_3_v1_metadata_template_field_1",
            field_type=MetadataFieldType.DATETIME,
            order=0,
        )
        metadata_template_3_v1_metadata_template_field_1.set_value("2025-12-01 12:00:00")

        metadata_template_3_v1_metadata_template_field_2 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_3_v1,
            custom_key="metadata_template_3_v1_metadata_template_field_2",
            field_type=MetadataFieldType.DATETIME,
            order=1,
        )
        metadata_template_3_v1_metadata_template_field_2.set_value("2025-12-01 12:00:00")

        assert MetadataTemplateContainer.objects.count() == 3
        assert MetadataTemplate.objects.count() == 3
        assert MetadataTemplateField.objects.count() == 6

        storage_1 = DynamicStorage.objects.get(
            storage_type=DEFAULT_STORAGE_TYPE,
            default=True,
        )

        project_1 = Project.objects.create(
            name="Project 1",
            metadata_template=metadata_template_1_v1,
        )

        folder_1 = Folder.objects.get(pk=project_1.folders.first().pk)
        folder_1.storage = storage_1
        folder_1.metadata_template = metadata_template_2_v1
        folder_1.save()

        self.uploads_dataset_1.metadata_template = metadata_template_3_v1
        self.uploads_dataset_1.save()

        assert project_1.metadata_template == metadata_template_1_v1
        assert folder_1.metadata_template == metadata_template_2_v1
        assert self.uploads_dataset_1.metadata_template == metadata_template_3_v1
        assert self.uploads_dataset_1.uploads_versions.count() == 1

        self.uploads_dataset_1.publish(folder=folder_1)

        assert self.uploads_dataset_1.uploads_versions.count() == 2
        assert self.uploads_dataset_1.latest_version != uploads_version_1
        assert self.uploads_dataset_1.latest_version.metadata_templates.count() == 3
        assert self.uploads_dataset_1.latest_version.metadata.count() == 6

        assert (
            self.uploads_dataset_1.latest_version.metadata.filter(
                metadata_template_field__in=[
                    metadata_template_1_v1_metadata_template_field_1.pk,
                    metadata_template_1_v1_metadata_template_field_2.pk,
                    metadata_template_2_v1_metadata_template_field_1.pk,
                    metadata_template_2_v1_metadata_template_field_2.pk,
                    metadata_template_3_v1_metadata_template_field_1.pk,
                    metadata_template_3_v1_metadata_template_field_2.pk,
                ],
            ).count()
            == 6
        )

        # Nothing should change
        assert self.uploads_dataset_1.upgrade_latest_version_metadata() == (True, None)

        assert (
            self.uploads_dataset_1.latest_version.metadata.filter(
                metadata_template_field__in=[
                    metadata_template_1_v1_metadata_template_field_1.pk,
                    metadata_template_1_v1_metadata_template_field_2.pk,
                    metadata_template_2_v1_metadata_template_field_1.pk,
                    metadata_template_2_v1_metadata_template_field_2.pk,
                    metadata_template_3_v1_metadata_template_field_1.pk,
                    metadata_template_3_v1_metadata_template_field_2.pk,
                ],
            ).count()
            == 6
        )

        metadata_template_1_v2 = MetadataTemplate.objects.create(
            name="metadata_template_1_v2",
            metadata_template_container=metadata_template_container_1,
        )

        metadata_template_1_v2_metadata_template_field_1 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_1_v2,
            custom_key="metadata_template_1_v2_metadata_template_field_1",
            field_type=MetadataFieldType.INTEGER,
            order=0,
            previous_version_field=metadata_template_1_v1_metadata_template_field_1,
        )
        metadata_template_1_v2_metadata_template_field_1.set_value(None)

        metadata_template_1_v2_metadata_template_field_2 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_1_v2,
            custom_key="metadata_template_1_v2_metadata_template_field_2",
            field_type=MetadataFieldType.DECIMAL,
            order=1,
            previous_version_field=metadata_template_1_v1_metadata_template_field_2,
        )
        metadata_template_1_v2_metadata_template_field_2.set_value(None)

        assert MetadataTemplateContainer.objects.count() == 3
        assert MetadataTemplate.objects.count() == 4
        assert MetadataTemplateField.objects.count() == 8

        project_1.metadata_template = metadata_template_1_v2
        project_1.save()

        # Project metadata template fields should be upgraded
        assert self.uploads_dataset_1.upgrade_latest_version_metadata() == (True, None)
        assert self.uploads_dataset_1.latest_version.metadata.count() == 6
        assert (
            self.uploads_dataset_1.latest_version.metadata_templates.filter(
                pk__in=[
                    metadata_template_1_v2.pk,
                    metadata_template_2_v1.pk,
                    metadata_template_3_v1.pk,
                ],
            ).count()
            == 3
        )
        assert (
            self.uploads_dataset_1.latest_version.metadata.filter(
                metadata_template_field__in=[
                    metadata_template_1_v2_metadata_template_field_1.pk,
                    metadata_template_1_v2_metadata_template_field_2.pk,
                    metadata_template_2_v1_metadata_template_field_1.pk,
                    metadata_template_2_v1_metadata_template_field_2.pk,
                    metadata_template_3_v1_metadata_template_field_1.pk,
                    metadata_template_3_v1_metadata_template_field_2.pk,
                ],
            ).count()
            == 6
        )
        assert (
            self.uploads_dataset_1.latest_version.metadata.filter(
                metadata_template_field__in=[
                    metadata_template_1_v1_metadata_template_field_1.pk,
                    metadata_template_1_v1_metadata_template_field_2.pk,
                ],
            ).count()
            == 0
        )

        metadata = self.uploads_dataset_1.latest_version.metadata.filter(
            metadata_template_field=metadata_template_1_v2_metadata_template_field_1.pk,
        ).first()
        assert metadata.field_type == MetadataFieldType.INTEGER
        assert metadata.get_value() == "1337"

        metadata = self.uploads_dataset_1.latest_version.metadata.filter(
            metadata_template_field=metadata_template_1_v2_metadata_template_field_2.pk,
        ).first()
        assert metadata.field_type == MetadataFieldType.DECIMAL
        assert metadata.get_value() == "1337"

        metadata_template_2_v2 = MetadataTemplate.objects.create(
            name="metadata_template_2_v2",
            metadata_template_container=metadata_template_container_2,
        )

        metadata_template_2_v2_metadata_template_field_1 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_2_v2,
            custom_key="metadata_template_2_v2_metadata_template_field_1",
            field_type=MetadataFieldType.TEXT,
            order=0,
            previous_version_field=metadata_template_2_v1_metadata_template_field_1,
        )
        metadata_template_2_v2_metadata_template_field_1.set_value(None)

        metadata_template_2_v2_metadata_template_field_2 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_2_v2,
            custom_key="metadata_template_2_v2_metadata_template_field_2",
            field_type=MetadataFieldType.SELECTION,
            config=dict(
                options=[
                    "Option 1",
                    "Option 2",
                    "Option 3",
                    "Option 4",
                ],
            ),
            order=1,
            previous_version_field=metadata_template_2_v1_metadata_template_field_2,
        )
        metadata_template_2_v2_metadata_template_field_2.set_value("Option 2")

        assert MetadataTemplateContainer.objects.count() == 3
        assert MetadataTemplate.objects.count() == 5
        assert MetadataTemplateField.objects.count() == 10

        folder_1.metadata_template = metadata_template_2_v2
        folder_1.save()

        # Folder metadata template fields should be upgraded
        assert self.uploads_dataset_1.upgrade_latest_version_metadata() == (True, None)
        assert self.uploads_dataset_1.latest_version.metadata.count() == 6
        assert (
            self.uploads_dataset_1.latest_version.metadata_templates.filter(
                pk__in=[
                    metadata_template_1_v2.pk,
                    metadata_template_2_v2.pk,
                    metadata_template_3_v1.pk,
                ],
            ).count()
            == 3
        )
        assert (
            self.uploads_dataset_1.latest_version.metadata.filter(
                metadata_template_field__in=[
                    metadata_template_1_v2_metadata_template_field_1.pk,
                    metadata_template_1_v2_metadata_template_field_2.pk,
                    metadata_template_2_v2_metadata_template_field_1.pk,
                    metadata_template_2_v2_metadata_template_field_2.pk,
                    metadata_template_3_v1_metadata_template_field_1.pk,
                    metadata_template_3_v1_metadata_template_field_2.pk,
                ],
            ).count()
            == 6
        )
        assert (
            self.uploads_dataset_1.latest_version.metadata.filter(
                metadata_template_field__in=[
                    metadata_template_1_v1_metadata_template_field_1.pk,
                    metadata_template_1_v1_metadata_template_field_2.pk,
                    metadata_template_2_v1_metadata_template_field_1.pk,
                    metadata_template_2_v1_metadata_template_field_2.pk,
                ],
            ).count()
            == 0
        )

        metadata = self.uploads_dataset_1.latest_version.metadata.filter(
            metadata_template_field=metadata_template_2_v2_metadata_template_field_1.pk,
        ).first()
        assert metadata.field_type == MetadataFieldType.TEXT
        assert metadata.get_value() == "13.37"

        metadata = self.uploads_dataset_1.latest_version.metadata.filter(
            metadata_template_field=metadata_template_2_v2_metadata_template_field_2.pk,
        ).first()
        assert metadata.field_type == MetadataFieldType.SELECTION
        assert metadata.get_value() == "Option 1"

        metadata_template_3_v2 = MetadataTemplate.objects.create(
            name="metadata_template_3_v2",
            metadata_template_container=metadata_template_container_3,
        )

        metadata_template_3_v2_metadata_template_field_1 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_3_v2,
            custom_key="metadata_template_3_v2_metadata_template_field_1",
            field_type=MetadataFieldType.DATE,
            order=0,
            previous_version_field=metadata_template_3_v1_metadata_template_field_1,
        )
        metadata_template_3_v2_metadata_template_field_1.set_value(None)

        metadata_template_3_v2_metadata_template_field_2 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_3_v2,
            custom_key="metadata_template_3_v2_metadata_template_field_2",
            field_type=MetadataFieldType.TIME,
            order=1,
            previous_version_field=metadata_template_3_v1_metadata_template_field_2,
        )
        metadata_template_3_v2_metadata_template_field_2.set_value(None)

        assert MetadataTemplateContainer.objects.count() == 3
        assert MetadataTemplate.objects.count() == 6
        assert MetadataTemplateField.objects.count() == 12

        self.uploads_dataset_1.metadata_template = metadata_template_3_v2
        self.uploads_dataset_1.save()

        # Dataset metadata template fields should be upgraded
        assert self.uploads_dataset_1.upgrade_latest_version_metadata() == (True, None)
        assert self.uploads_dataset_1.latest_version.metadata.count() == 6
        assert (
            self.uploads_dataset_1.latest_version.metadata_templates.filter(
                pk__in=[
                    metadata_template_1_v2.pk,
                    metadata_template_2_v2.pk,
                    metadata_template_3_v2.pk,
                ],
            ).count()
            == 3
        )
        assert (
            self.uploads_dataset_1.latest_version.metadata.filter(
                metadata_template_field__in=[
                    metadata_template_1_v2_metadata_template_field_1.pk,
                    metadata_template_1_v2_metadata_template_field_2.pk,
                    metadata_template_2_v2_metadata_template_field_1.pk,
                    metadata_template_2_v2_metadata_template_field_2.pk,
                    metadata_template_3_v2_metadata_template_field_1.pk,
                    metadata_template_3_v2_metadata_template_field_2.pk,
                ],
            ).count()
            == 6
        )
        assert (
            self.uploads_dataset_1.latest_version.metadata.filter(
                metadata_template_field__in=[
                    metadata_template_1_v1_metadata_template_field_1.pk,
                    metadata_template_1_v1_metadata_template_field_2.pk,
                    metadata_template_2_v1_metadata_template_field_1.pk,
                    metadata_template_2_v1_metadata_template_field_2.pk,
                    metadata_template_3_v1_metadata_template_field_1.pk,
                    metadata_template_3_v1_metadata_template_field_2.pk,
                ],
            ).count()
            == 0
        )

        metadata = self.uploads_dataset_1.latest_version.metadata.filter(
            metadata_template_field=metadata_template_3_v2_metadata_template_field_1.pk,
        ).first()
        assert metadata.field_type == MetadataFieldType.DATE
        assert metadata.get_value() == "2025-12-01"

        metadata = self.uploads_dataset_1.latest_version.metadata.filter(
            metadata_template_field=metadata_template_3_v2_metadata_template_field_2.pk,
        ).first()
        assert metadata.field_type == MetadataFieldType.TIME
        assert metadata.get_value() == "12:00:00"

        metadata_template_3_v3 = MetadataTemplate.objects.create(
            name="metadata_template_3_v3",
            metadata_template_container=metadata_template_container_3,
        )

        metadata_template_3_v3_metadata_template_field_1 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_3_v3,
            custom_key="metadata_template_3_v3_metadata_template_field_1",
            field_type=MetadataFieldType.DATETIME,
            order=0,
            previous_version_field=metadata_template_3_v2_metadata_template_field_1,
        )
        metadata_template_3_v3_metadata_template_field_1.set_value(None)

        assert MetadataTemplateContainer.objects.count() == 3
        assert MetadataTemplate.objects.count() == 7
        assert MetadataTemplateField.objects.count() == 13

        self.uploads_dataset_1.metadata_template = metadata_template_3_v3
        self.uploads_dataset_1.save()

        # Dataset metadata template fields should be upgraded
        assert self.uploads_dataset_1.upgrade_latest_version_metadata() == (True, None)
        assert self.uploads_dataset_1.latest_version.metadata.count() == 6
        assert (
            self.uploads_dataset_1.latest_version.metadata_templates.filter(
                pk__in=[
                    metadata_template_1_v2.pk,
                    metadata_template_2_v2.pk,
                    metadata_template_3_v3.pk,
                ],
            ).count()
            == 3
        )
        assert (
            self.uploads_dataset_1.latest_version.metadata.filter(
                metadata_template_field__in=[
                    metadata_template_1_v2_metadata_template_field_1.pk,
                    metadata_template_1_v2_metadata_template_field_2.pk,
                    metadata_template_2_v2_metadata_template_field_1.pk,
                    metadata_template_2_v2_metadata_template_field_2.pk,
                    metadata_template_3_v3_metadata_template_field_1.pk,
                ],
            ).count()
            == 5
        )
        assert (
            self.uploads_dataset_1.latest_version.metadata.filter(
                metadata_template_field=None,
            ).count()
            == 1
        )
        assert (
            self.uploads_dataset_1.latest_version.metadata.filter(
                metadata_template_field__in=[
                    metadata_template_1_v1_metadata_template_field_1.pk,
                    metadata_template_1_v1_metadata_template_field_2.pk,
                    metadata_template_2_v1_metadata_template_field_1.pk,
                    metadata_template_2_v1_metadata_template_field_2.pk,
                    metadata_template_3_v1_metadata_template_field_1.pk,
                    metadata_template_3_v1_metadata_template_field_2.pk,
                    metadata_template_3_v2_metadata_template_field_1.pk,
                    metadata_template_3_v2_metadata_template_field_2.pk,
                ],
            ).count()
            == 0
        )

        metadata = self.uploads_dataset_1.latest_version.metadata.filter(
            metadata_template_field=metadata_template_3_v3_metadata_template_field_1.pk,
        ).first()
        assert metadata.field_type == MetadataFieldType.DATETIME
        assert metadata.get_value() == "2025-12-01 00:00:00"

    def test_upgrade_latest_version_metadata_fails_for_invalid_type_conversion(self):
        """
        Ensure metadata upgrade fails if a value cannot be converted to the new field type.
        """
        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        UploadsVersion.objects.create(
            dataset=self.uploads_dataset_1,
            version_file=uploads_version_file,
        )

        metadata_template_container_1 = MetadataTemplateContainer.objects.create(
            name="metadata_template_container_1",
        )

        metadata_template_1_v1 = MetadataTemplate.objects.create(
            name="metadata_template_1_v1",
            metadata_template_container=metadata_template_container_1,
        )

        metadata_template_1_v1_metadata_template_field_1 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_1_v1,
            custom_key="metadata_template_1_v1_metadata_template_field_1",
            field_type=MetadataFieldType.TEXT,
            order=0,
        )
        metadata_template_1_v1_metadata_template_field_1.set_value("A")

        storage_1 = DynamicStorage.objects.get(
            storage_type=DEFAULT_STORAGE_TYPE,
            default=True,
        )

        project_1 = Project.objects.create(
            name="Project 1",
        )

        folder_1 = Folder.objects.get(pk=project_1.folders.first().pk)
        folder_1.storage = storage_1
        folder_1.save()

        self.uploads_dataset_1.metadata_template = metadata_template_1_v1
        self.uploads_dataset_1.save()
        self.uploads_dataset_1.publish(folder=folder_1)

        assert self.uploads_dataset_1.uploads_versions.count() == 2
        assert self.uploads_dataset_1.latest_version.metadata.count() == 1

        metadata_template_1_v2 = MetadataTemplate.objects.create(
            name="metadata_template_1_v2",
            metadata_template_container=metadata_template_container_1,
        )

        metadata_template_1_v2_metadata_template_field_1 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_1_v2,
            custom_key="metadata_template_1_v2_metadata_template_field_1",
            field_type=MetadataFieldType.INTEGER,
            order=0,
            previous_version_field=metadata_template_1_v1_metadata_template_field_1,
        )
        metadata_template_1_v2_metadata_template_field_1.set_value(None)

        self.uploads_dataset_1.metadata_template = metadata_template_1_v2
        self.uploads_dataset_1.save()

        assert self.uploads_dataset_1.is_latest_version_upgradeable(max_upgrade=True) is True

        before_latest_version_pk = self.uploads_dataset_1.latest_version.pk
        before_versions_count = self.uploads_dataset_1.uploads_versions.count()

        success, error = self.uploads_dataset_1.upgrade_latest_version_metadata(max_upgrade=True)

        assert success is False
        assert error is not None
        assert "must be an integer" in error

        self.uploads_dataset_1.refresh_from_db()

        assert self.uploads_dataset_1.uploads_versions.count() == before_versions_count
        assert self.uploads_dataset_1.latest_version.pk == before_latest_version_pk
        assert (
            self.uploads_dataset_1.latest_version.metadata.filter(
                metadata_template_field=metadata_template_1_v1_metadata_template_field_1,
            ).count()
            == 1
        )
        assert (
            self.uploads_dataset_1.latest_version.metadata.filter(
                metadata_template_field=metadata_template_1_v2_metadata_template_field_1,
            ).count()
            == 0
        )

        metadata = self.uploads_dataset_1.latest_version.metadata.get(
            metadata_template_field=metadata_template_1_v1_metadata_template_field_1,
        )
        assert metadata.field_type == MetadataFieldType.TEXT
        assert metadata.get_value() == "A"

    def test_upgrade_latest_version_metadata_selection_to_tag(self):
        """
        Ensure metadata upgrade converts selection values to tag values.
        """
        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        UploadsVersion.objects.create(
            dataset=self.uploads_dataset_1,
            version_file=uploads_version_file,
        )

        metadata_template_container_1 = MetadataTemplateContainer.objects.create(
            name="metadata_template_container_1",
        )

        metadata_template_1_v1 = MetadataTemplate.objects.create(
            name="metadata_template_1_v1",
            metadata_template_container=metadata_template_container_1,
        )

        metadata_template_1_v1_metadata_template_field_1 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_1_v1,
            custom_key="metadata_template_1_v1_metadata_template_field_1",
            field_type=MetadataFieldType.SELECTION,
            config=dict(
                options=[
                    "Tag 1",
                    "Tag 2",
                    "Tag 3",
                ],
            ),
            order=0,
        )
        metadata_template_1_v1_metadata_template_field_1.set_value("Tag 2")

        storage_1 = DynamicStorage.objects.get(
            storage_type=DEFAULT_STORAGE_TYPE,
            default=True,
        )

        project_1 = Project.objects.create(
            name="Project 1",
        )

        folder_1 = Folder.objects.get(pk=project_1.folders.first().pk)
        folder_1.storage = storage_1
        folder_1.save()

        self.uploads_dataset_1.metadata_template = metadata_template_1_v1
        self.uploads_dataset_1.save()
        self.uploads_dataset_1.publish(folder=folder_1)

        assert self.uploads_dataset_1.uploads_versions.count() == 2
        assert self.uploads_dataset_1.latest_version.metadata.count() == 1

        legacy_metadata = self.uploads_dataset_1.latest_version.metadata.get(
            metadata_template_field=metadata_template_1_v1_metadata_template_field_1,
        )
        Metadata.objects.filter(pk=legacy_metadata.pk).update(value={"value": "  Tag 2  "})

        metadata_template_1_v2 = MetadataTemplate.objects.create(
            name="metadata_template_1_v2",
            metadata_template_container=metadata_template_container_1,
        )

        metadata_template_1_v2_metadata_template_field_1 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_1_v2,
            custom_key="metadata_template_1_v2_metadata_template_field_1",
            field_type=MetadataFieldType.TAG,
            config=dict(
                options=[
                    "Tag 1",
                    "Tag 2",
                    "Tag 3",
                ],
            ),
            order=0,
            previous_version_field=metadata_template_1_v1_metadata_template_field_1,
        )
        metadata_template_1_v2_metadata_template_field_1.set_value(None)

        self.uploads_dataset_1.metadata_template = metadata_template_1_v2
        self.uploads_dataset_1.save()

        assert self.uploads_dataset_1.is_latest_version_upgradeable(max_upgrade=True) is True

        before_latest_version_pk = self.uploads_dataset_1.latest_version.pk
        before_versions_count = self.uploads_dataset_1.uploads_versions.count()

        success, error = self.uploads_dataset_1.upgrade_latest_version_metadata(max_upgrade=True)

        assert success is True
        assert error is None

        self.uploads_dataset_1.refresh_from_db()

        assert self.uploads_dataset_1.uploads_versions.count() == before_versions_count + 1
        assert self.uploads_dataset_1.latest_version.pk != before_latest_version_pk
        assert (
            self.uploads_dataset_1.latest_version.metadata.filter(
                metadata_template_field=metadata_template_1_v1_metadata_template_field_1,
            ).count()
            == 0
        )
        assert (
            self.uploads_dataset_1.latest_version.metadata.filter(
                metadata_template_field=metadata_template_1_v2_metadata_template_field_1,
            ).count()
            == 1
        )

        metadata = self.uploads_dataset_1.latest_version.metadata.get(
            metadata_template_field=metadata_template_1_v2_metadata_template_field_1,
        )
        assert metadata.field_type == MetadataFieldType.TAG
        assert metadata.get_value() == [
            "Tag 2",
        ]

    def test_upgrade_latest_version_metadata_fails_for_tag_to_selection_with_multiple_values(self):
        """
        Ensure metadata upgrade fails when converting multiple tag values to a single selection value.
        """
        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        UploadsVersion.objects.create(
            dataset=self.uploads_dataset_1,
            version_file=uploads_version_file,
        )

        metadata_template_container_1 = MetadataTemplateContainer.objects.create(
            name="metadata_template_container_1",
        )

        metadata_template_1_v1 = MetadataTemplate.objects.create(
            name="metadata_template_1_v1",
            metadata_template_container=metadata_template_container_1,
        )

        metadata_template_1_v1_metadata_template_field_1 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_1_v1,
            custom_key="metadata_template_1_v1_metadata_template_field_1",
            field_type=MetadataFieldType.TAG,
            config=dict(
                options=[
                    "Tag 1",
                    "Tag 2",
                    "Tag 3",
                ],
            ),
            order=0,
        )
        metadata_template_1_v1_metadata_template_field_1.set_value(
            [
                "Tag 1",
                "Tag 2",
            ],
        )

        storage_1 = DynamicStorage.objects.get(
            storage_type=DEFAULT_STORAGE_TYPE,
            default=True,
        )

        project_1 = Project.objects.create(
            name="Project 1",
        )

        folder_1 = Folder.objects.get(pk=project_1.folders.first().pk)
        folder_1.storage = storage_1
        folder_1.save()

        self.uploads_dataset_1.metadata_template = metadata_template_1_v1
        self.uploads_dataset_1.save()
        self.uploads_dataset_1.publish(folder=folder_1)

        assert self.uploads_dataset_1.uploads_versions.count() == 2
        assert self.uploads_dataset_1.latest_version.metadata.count() == 1

        metadata_template_1_v2 = MetadataTemplate.objects.create(
            name="metadata_template_1_v2",
            metadata_template_container=metadata_template_container_1,
        )

        metadata_template_1_v2_metadata_template_field_1 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_1_v2,
            custom_key="metadata_template_1_v2_metadata_template_field_1",
            field_type=MetadataFieldType.SELECTION,
            config=dict(
                options=[
                    "Tag 1",
                    "Tag 2",
                    "Tag 3",
                ],
            ),
            order=0,
            previous_version_field=metadata_template_1_v1_metadata_template_field_1,
        )
        metadata_template_1_v2_metadata_template_field_1.set_value(None)

        self.uploads_dataset_1.metadata_template = metadata_template_1_v2
        self.uploads_dataset_1.save()

        assert self.uploads_dataset_1.is_latest_version_upgradeable(max_upgrade=True) is True

        before_latest_version_pk = self.uploads_dataset_1.latest_version.pk
        before_versions_count = self.uploads_dataset_1.uploads_versions.count()

        success, error = self.uploads_dataset_1.upgrade_latest_version_metadata(max_upgrade=True)

        assert success is False
        assert error is not None
        assert "multiple selected values" in error

        self.uploads_dataset_1.refresh_from_db()

        assert self.uploads_dataset_1.uploads_versions.count() == before_versions_count
        assert self.uploads_dataset_1.latest_version.pk == before_latest_version_pk
        assert (
            self.uploads_dataset_1.latest_version.metadata.filter(
                metadata_template_field=metadata_template_1_v1_metadata_template_field_1,
            ).count()
            == 1
        )
        assert (
            self.uploads_dataset_1.latest_version.metadata.filter(
                metadata_template_field=metadata_template_1_v2_metadata_template_field_1,
            ).count()
            == 0
        )

        metadata = self.uploads_dataset_1.latest_version.metadata.get(
            metadata_template_field=metadata_template_1_v1_metadata_template_field_1,
        )
        assert metadata.field_type == MetadataFieldType.TAG
        assert metadata.get_value() == [
            "Tag 1",
            "Tag 2",
        ]

    def test_max_upgrade_latest_version_metadata(self):
        """
        Ensure we can automatically upgrade metadata on datasets to the absolut latest metadata template versions.
        """
        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        uploads_version_1 = UploadsVersion.objects.create(
            dataset=self.uploads_dataset_1,
            version_file=uploads_version_file,
        )

        metadata_template_container_1 = MetadataTemplateContainer.objects.create(
            name="metadata_template_container_1",
        )

        metadata_template_1_v1 = MetadataTemplate.objects.create(
            name="metadata_template_1_v1",
            metadata_template_container=metadata_template_container_1,
        )

        metadata_template_1_v1_metadata_template_field_1 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_1_v1,
            custom_key="metadata_template_1_v1_metadata_template_field_1",
            field_type=MetadataFieldType.TEXT,
            order=0,
        )
        metadata_template_1_v1_metadata_template_field_1.set_value("1337")

        metadata_template_1_v1_metadata_template_field_2 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_1_v1,
            custom_key="metadata_template_1_v1_metadata_template_field_2",
            field_type=MetadataFieldType.INTEGER,
            order=1,
        )
        metadata_template_1_v1_metadata_template_field_2.set_value("1337")

        assert MetadataTemplateContainer.objects.count() == 1
        assert MetadataTemplate.objects.count() == 1
        assert MetadataTemplateField.objects.count() == 2

        metadata_template_container_2 = MetadataTemplateContainer.objects.create(
            name="metadata_template_container_2",
        )

        metadata_template_2_v1 = MetadataTemplate.objects.create(
            name="metadata_template_2_v1",
            metadata_template_container=metadata_template_container_2,
        )

        metadata_template_2_v1_metadata_template_field_1 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_2_v1,
            custom_key="metadata_template_2_v1_metadata_template_field_1",
            field_type=MetadataFieldType.DECIMAL,
            order=0,
        )
        metadata_template_2_v1_metadata_template_field_1.set_value("13.37")

        metadata_template_2_v1_metadata_template_field_2 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_2_v1,
            custom_key="metadata_template_2_v1_metadata_template_field_2",
            field_type=MetadataFieldType.SELECTION,
            config=dict(
                options=[
                    "Option 1",
                    "Option 2",
                    "Option 3",
                ],
            ),
            order=1,
        )
        metadata_template_2_v1_metadata_template_field_2.set_value("Option 1")

        assert MetadataTemplateContainer.objects.count() == 2
        assert MetadataTemplate.objects.count() == 2
        assert MetadataTemplateField.objects.count() == 4

        metadata_template_container_3 = MetadataTemplateContainer.objects.create(
            name="metadata_template_container_3",
        )

        metadata_template_3_v1 = MetadataTemplate.objects.create(
            name="metadata_template_3_v1",
            metadata_template_container=metadata_template_container_3,
        )

        metadata_template_3_v1_metadata_template_field_1 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_3_v1,
            custom_key="metadata_template_3_v1_metadata_template_field_1",
            field_type=MetadataFieldType.DATETIME,
            order=0,
        )
        metadata_template_3_v1_metadata_template_field_1.set_value("2025-12-01 12:00:00")

        metadata_template_3_v1_metadata_template_field_2 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_3_v1,
            custom_key="metadata_template_3_v1_metadata_template_field_2",
            field_type=MetadataFieldType.DATETIME,
            order=1,
        )
        metadata_template_3_v1_metadata_template_field_2.set_value("2025-12-01 12:00:00")

        assert MetadataTemplateContainer.objects.count() == 3
        assert MetadataTemplate.objects.count() == 3
        assert MetadataTemplateField.objects.count() == 6

        storage_1 = DynamicStorage.objects.get(
            storage_type=DEFAULT_STORAGE_TYPE,
            default=True,
        )

        project_1 = Project.objects.create(
            name="Project 1",
            metadata_template=metadata_template_1_v1,
        )

        folder_1 = Folder.objects.get(pk=project_1.folders.first().pk)
        folder_1.storage = storage_1
        folder_1.metadata_template = metadata_template_2_v1
        folder_1.save()

        self.uploads_dataset_1.metadata_template = metadata_template_3_v1
        self.uploads_dataset_1.save()

        assert project_1.metadata_template == metadata_template_1_v1
        assert folder_1.metadata_template == metadata_template_2_v1
        assert self.uploads_dataset_1.metadata_template == metadata_template_3_v1
        assert self.uploads_dataset_1.uploads_versions.count() == 1

        self.uploads_dataset_1.publish(folder=folder_1)

        assert self.uploads_dataset_1.uploads_versions.count() == 2
        assert self.uploads_dataset_1.latest_version != uploads_version_1
        assert self.uploads_dataset_1.latest_version.metadata_templates.count() == 3
        assert self.uploads_dataset_1.latest_version.metadata.count() == 6

        assert (
            self.uploads_dataset_1.latest_version.metadata.filter(
                metadata_template_field__in=[
                    metadata_template_1_v1_metadata_template_field_1.pk,
                    metadata_template_1_v1_metadata_template_field_2.pk,
                    metadata_template_2_v1_metadata_template_field_1.pk,
                    metadata_template_2_v1_metadata_template_field_2.pk,
                    metadata_template_3_v1_metadata_template_field_1.pk,
                    metadata_template_3_v1_metadata_template_field_2.pk,
                ],
            ).count()
            == 6
        )

        # Nothing should change
        assert self.uploads_dataset_1.upgrade_latest_version_metadata(max_upgrade=True) == (True, None)

        assert (
            self.uploads_dataset_1.latest_version.metadata.filter(
                metadata_template_field__in=[
                    metadata_template_1_v1_metadata_template_field_1.pk,
                    metadata_template_1_v1_metadata_template_field_2.pk,
                    metadata_template_2_v1_metadata_template_field_1.pk,
                    metadata_template_2_v1_metadata_template_field_2.pk,
                    metadata_template_3_v1_metadata_template_field_1.pk,
                    metadata_template_3_v1_metadata_template_field_2.pk,
                ],
            ).count()
            == 6
        )

        metadata_template_1_v2 = MetadataTemplate.objects.create(
            name="metadata_template_1_v2",
            metadata_template_container=metadata_template_container_1,
        )

        metadata_template_1_v2_metadata_template_field_1 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_1_v2,
            custom_key="metadata_template_1_v2_metadata_template_field_1",
            field_type=MetadataFieldType.INTEGER,
            order=0,
            previous_version_field=metadata_template_1_v1_metadata_template_field_1,
        )
        metadata_template_1_v2_metadata_template_field_1.set_value(None)

        metadata_template_1_v2_metadata_template_field_2 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_1_v2,
            custom_key="metadata_template_1_v2_metadata_template_field_2",
            field_type=MetadataFieldType.DECIMAL,
            order=1,
            previous_version_field=metadata_template_1_v1_metadata_template_field_2,
        )
        metadata_template_1_v2_metadata_template_field_2.set_value(None)

        assert MetadataTemplateContainer.objects.count() == 3
        assert MetadataTemplate.objects.count() == 4
        assert MetadataTemplateField.objects.count() == 8

        project_1.metadata_template = metadata_template_1_v2
        project_1.save()

        # Project metadata template fields should be upgraded
        assert self.uploads_dataset_1.upgrade_latest_version_metadata(max_upgrade=True) == (True, None)
        assert self.uploads_dataset_1.latest_version.metadata.count() == 6
        assert (
            self.uploads_dataset_1.latest_version.metadata_templates.filter(
                pk__in=[
                    metadata_template_1_v2.pk,
                    metadata_template_2_v1.pk,
                    metadata_template_3_v1.pk,
                ],
            ).count()
            == 3
        )
        assert (
            self.uploads_dataset_1.latest_version.metadata.filter(
                metadata_template_field__in=[
                    metadata_template_1_v2_metadata_template_field_1.pk,
                    metadata_template_1_v2_metadata_template_field_2.pk,
                    metadata_template_2_v1_metadata_template_field_1.pk,
                    metadata_template_2_v1_metadata_template_field_2.pk,
                    metadata_template_3_v1_metadata_template_field_1.pk,
                    metadata_template_3_v1_metadata_template_field_2.pk,
                ],
            ).count()
            == 6
        )
        assert (
            self.uploads_dataset_1.latest_version.metadata.filter(
                metadata_template_field__in=[
                    metadata_template_1_v1_metadata_template_field_1.pk,
                    metadata_template_1_v1_metadata_template_field_2.pk,
                ],
            ).count()
            == 0
        )

        metadata = self.uploads_dataset_1.latest_version.metadata.filter(
            metadata_template_field=metadata_template_1_v2_metadata_template_field_1.pk,
        ).first()
        assert metadata.field_type == MetadataFieldType.INTEGER
        assert metadata.get_value() == "1337"

        metadata = self.uploads_dataset_1.latest_version.metadata.filter(
            metadata_template_field=metadata_template_1_v2_metadata_template_field_2.pk,
        ).first()
        assert metadata.field_type == MetadataFieldType.DECIMAL
        assert metadata.get_value() == "1337"

        metadata_template_2_v2 = MetadataTemplate.objects.create(
            name="metadata_template_2_v2",
            metadata_template_container=metadata_template_container_2,
        )

        metadata_template_2_v2_metadata_template_field_1 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_2_v2,
            custom_key="metadata_template_2_v2_metadata_template_field_1",
            field_type=MetadataFieldType.TEXT,
            order=0,
            previous_version_field=metadata_template_2_v1_metadata_template_field_1,
        )
        metadata_template_2_v2_metadata_template_field_1.set_value(None)

        metadata_template_2_v2_metadata_template_field_2 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_2_v2,
            custom_key="metadata_template_2_v2_metadata_template_field_2",
            field_type=MetadataFieldType.SELECTION,
            config=dict(
                options=[
                    "Option 1",
                    "Option 2",
                    "Option 3",
                    "Option 4",
                ],
            ),
            order=1,
            previous_version_field=metadata_template_2_v1_metadata_template_field_2,
        )
        metadata_template_2_v2_metadata_template_field_2.set_value("Option 2")

        assert MetadataTemplateContainer.objects.count() == 3
        assert MetadataTemplate.objects.count() == 5
        assert MetadataTemplateField.objects.count() == 10

        folder_1.metadata_template = metadata_template_2_v2
        folder_1.save()

        # Folder metadata template fields should be upgraded
        assert self.uploads_dataset_1.upgrade_latest_version_metadata(max_upgrade=True) == (True, None)
        assert self.uploads_dataset_1.latest_version.metadata.count() == 6
        assert (
            self.uploads_dataset_1.latest_version.metadata_templates.filter(
                pk__in=[
                    metadata_template_1_v2.pk,
                    metadata_template_2_v2.pk,
                    metadata_template_3_v1.pk,
                ],
            ).count()
            == 3
        )
        assert (
            self.uploads_dataset_1.latest_version.metadata.filter(
                metadata_template_field__in=[
                    metadata_template_1_v2_metadata_template_field_1.pk,
                    metadata_template_1_v2_metadata_template_field_2.pk,
                    metadata_template_2_v2_metadata_template_field_1.pk,
                    metadata_template_2_v2_metadata_template_field_2.pk,
                    metadata_template_3_v1_metadata_template_field_1.pk,
                    metadata_template_3_v1_metadata_template_field_2.pk,
                ],
            ).count()
            == 6
        )
        assert (
            self.uploads_dataset_1.latest_version.metadata.filter(
                metadata_template_field__in=[
                    metadata_template_1_v1_metadata_template_field_1.pk,
                    metadata_template_1_v1_metadata_template_field_2.pk,
                    metadata_template_2_v1_metadata_template_field_1.pk,
                    metadata_template_2_v1_metadata_template_field_2.pk,
                ],
            ).count()
            == 0
        )

        metadata = self.uploads_dataset_1.latest_version.metadata.filter(
            metadata_template_field=metadata_template_2_v2_metadata_template_field_1.pk,
        ).first()
        assert metadata.field_type == MetadataFieldType.TEXT
        assert metadata.get_value() == "13.37"

        metadata = self.uploads_dataset_1.latest_version.metadata.filter(
            metadata_template_field=metadata_template_2_v2_metadata_template_field_2.pk,
        ).first()
        assert metadata.field_type == MetadataFieldType.SELECTION
        assert metadata.get_value() == "Option 1"

        metadata_template_3_v2 = MetadataTemplate.objects.create(
            name="metadata_template_3_v2",
            metadata_template_container=metadata_template_container_3,
        )

        metadata_template_3_v2_metadata_template_field_1 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_3_v2,
            custom_key="metadata_template_3_v2_metadata_template_field_1",
            field_type=MetadataFieldType.DATE,
            order=0,
            previous_version_field=metadata_template_3_v1_metadata_template_field_1,
        )
        metadata_template_3_v2_metadata_template_field_1.set_value(None)

        metadata_template_3_v2_metadata_template_field_2 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_3_v2,
            custom_key="metadata_template_3_v2_metadata_template_field_2",
            field_type=MetadataFieldType.TIME,
            order=1,
            previous_version_field=metadata_template_3_v1_metadata_template_field_2,
        )
        metadata_template_3_v2_metadata_template_field_2.set_value(None)

        assert MetadataTemplateContainer.objects.count() == 3
        assert MetadataTemplate.objects.count() == 6
        assert MetadataTemplateField.objects.count() == 12

        self.uploads_dataset_1.metadata_template = metadata_template_3_v2
        self.uploads_dataset_1.save()

        # Dataset metadata template fields should be upgraded
        assert self.uploads_dataset_1.upgrade_latest_version_metadata(max_upgrade=True) == (True, None)
        assert self.uploads_dataset_1.latest_version.metadata.count() == 6
        assert (
            self.uploads_dataset_1.latest_version.metadata_templates.filter(
                pk__in=[
                    metadata_template_1_v2.pk,
                    metadata_template_2_v2.pk,
                    metadata_template_3_v2.pk,
                ],
            ).count()
            == 3
        )
        assert (
            self.uploads_dataset_1.latest_version.metadata.filter(
                metadata_template_field__in=[
                    metadata_template_1_v2_metadata_template_field_1.pk,
                    metadata_template_1_v2_metadata_template_field_2.pk,
                    metadata_template_2_v2_metadata_template_field_1.pk,
                    metadata_template_2_v2_metadata_template_field_2.pk,
                    metadata_template_3_v2_metadata_template_field_1.pk,
                    metadata_template_3_v2_metadata_template_field_2.pk,
                ],
            ).count()
            == 6
        )
        assert (
            self.uploads_dataset_1.latest_version.metadata.filter(
                metadata_template_field__in=[
                    metadata_template_1_v1_metadata_template_field_1.pk,
                    metadata_template_1_v1_metadata_template_field_2.pk,
                    metadata_template_2_v1_metadata_template_field_1.pk,
                    metadata_template_2_v1_metadata_template_field_2.pk,
                    metadata_template_3_v1_metadata_template_field_1.pk,
                    metadata_template_3_v1_metadata_template_field_2.pk,
                ],
            ).count()
            == 0
        )

        metadata = self.uploads_dataset_1.latest_version.metadata.filter(
            metadata_template_field=metadata_template_3_v2_metadata_template_field_1.pk,
        ).first()
        assert metadata.field_type == MetadataFieldType.DATE
        assert metadata.get_value() == "2025-12-01"

        metadata = self.uploads_dataset_1.latest_version.metadata.filter(
            metadata_template_field=metadata_template_3_v2_metadata_template_field_2.pk,
        ).first()
        assert metadata.field_type == MetadataFieldType.TIME
        assert metadata.get_value() == "12:00:00"

        metadata_template_3_v3 = MetadataTemplate.objects.create(
            name="metadata_template_3_v3",
            metadata_template_container=metadata_template_container_3,
        )

        metadata_template_3_v3_metadata_template_field_1 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_3_v3,
            custom_key="metadata_template_3_v3_metadata_template_field_1",
            field_type=MetadataFieldType.DATETIME,
            order=0,
            previous_version_field=metadata_template_3_v2_metadata_template_field_1,
        )
        metadata_template_3_v3_metadata_template_field_1.set_value(None)

        assert MetadataTemplateContainer.objects.count() == 3
        assert MetadataTemplate.objects.count() == 7
        assert MetadataTemplateField.objects.count() == 13

        self.uploads_dataset_1.metadata_template = metadata_template_3_v3
        self.uploads_dataset_1.save()

        # Dataset metadata template fields should be upgraded
        assert self.uploads_dataset_1.upgrade_latest_version_metadata(max_upgrade=True) == (True, None)
        assert self.uploads_dataset_1.latest_version.metadata.count() == 6
        assert (
            self.uploads_dataset_1.latest_version.metadata_templates.filter(
                pk__in=[
                    metadata_template_1_v2.pk,
                    metadata_template_2_v2.pk,
                    metadata_template_3_v3.pk,
                ],
            ).count()
            == 3
        )
        assert (
            self.uploads_dataset_1.latest_version.metadata.filter(
                metadata_template_field__in=[
                    metadata_template_1_v2_metadata_template_field_1.pk,
                    metadata_template_1_v2_metadata_template_field_2.pk,
                    metadata_template_2_v2_metadata_template_field_1.pk,
                    metadata_template_2_v2_metadata_template_field_2.pk,
                    metadata_template_3_v3_metadata_template_field_1.pk,
                ],
            ).count()
            == 5
        )
        assert (
            self.uploads_dataset_1.latest_version.metadata.filter(
                metadata_template_field=None,
            ).count()
            == 1
        )
        assert (
            self.uploads_dataset_1.latest_version.metadata.filter(
                metadata_template_field__in=[
                    metadata_template_1_v1_metadata_template_field_1.pk,
                    metadata_template_1_v1_metadata_template_field_2.pk,
                    metadata_template_2_v1_metadata_template_field_1.pk,
                    metadata_template_2_v1_metadata_template_field_2.pk,
                    metadata_template_3_v1_metadata_template_field_1.pk,
                    metadata_template_3_v1_metadata_template_field_2.pk,
                    metadata_template_3_v2_metadata_template_field_1.pk,
                    metadata_template_3_v2_metadata_template_field_2.pk,
                ],
            ).count()
            == 0
        )

        metadata = self.uploads_dataset_1.latest_version.metadata.filter(
            metadata_template_field=metadata_template_3_v3_metadata_template_field_1.pk,
        ).first()
        assert metadata.field_type == MetadataFieldType.DATETIME
        assert metadata.get_value() == "2025-12-01 00:00:00"

    def test_publish_to_folder_with_view_permission_only(self, initial_users):
        """
        Ensure we can't publish a dataset to a folder with only view permissions
        """
        storage_1 = DynamicStorage.objects.get(
            default=True,
        )

        project_1 = Project.objects.create(
            name="Test project",
        )

        project_membership_user_2 = ProjectMembership.objects.create(
            project=project_1,
            member=initial_users["user_2"],
            is_project_admin=False,
            can_create_folders=False,
            is_metadata_template_admin=False,
        )

        folder_1 = project_1.folders.first()
        folder_1.storage = storage_1
        folder_1.save()

        folder_permission_user_2 = FolderPermission.objects.create(
            folder=folder_1,
            project_membership=project_membership_user_2,
            is_folder_admin=False,
            is_metadata_template_admin=False,
            can_edit=False,
        )

        set_request_for_user(initial_users["user_2"])

        dataset_1 = UploadsDataset.objects.create(
            name="Dataset 1",
        )

        uploads_version_file_1 = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        uploads_version_1 = UploadsVersion.objects.create(
            dataset=dataset_1,
        )
        uploads_version_1.version_file = uploads_version_file_1
        uploads_version_1.save()

        with pytest.raises(PermissionDenied):
            dataset_1.publish(folder=folder_1.pk)

        set_request_for_user(initial_users["user_1"])

        folder_permission_user_2.can_edit = True
        folder_permission_user_2.save()

        set_request_for_user(initial_users["user_2"])

        dataset_1.publish(folder=folder_1.pk)


@pytest.mark.django_db
class TestUploadsDatasetLockMixin:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        project_1 = Project.objects.create(
            name="Project 1",
        )

        self.dataset_1 = UploadsDataset.objects.create(
            name="Dataset 1",
            folder=project_1.folders.first(),
        )

    def test_lock(self, initial_users):
        """
        Ensure we have an expected lock behavior.
        """
        dataset = UploadsDataset.objects.get(pk=self.dataset_1.pk)
        dataset.name = "Dataset 1"
        dataset.save()

        dataset.refresh_from_db()
        assert dataset.name == "Dataset 1"
        assert dataset.locked is False
        assert dataset.locked_by is None
        assert dataset.locked_at is None

        dataset.lock()

        dataset.refresh_from_db()
        assert dataset.name == "Dataset 1"
        assert dataset.locked is True
        assert dataset.locked_by == initial_users["user_1"]
        assert dataset.locked_at is not None

        last_lock_time = dataset.locked_at

        dataset.lock()

        dataset.refresh_from_db()
        assert dataset.name == "Dataset 1"
        assert dataset.locked is True
        assert dataset.locked_by == initial_users["user_1"]
        assert dataset.locked_at is not None
        assert dataset.locked_at > last_lock_time

        dataset.name = "Dataset 1 - Edit #1"
        dataset.save()

        dataset.refresh_from_db()
        assert dataset.name == "Dataset 1 - Edit #1"
        assert dataset.locked is False
        assert dataset.locked_by is None
        assert dataset.locked_at is None

        dataset.lock()

        dataset.refresh_from_db()
        assert dataset.name == "Dataset 1 - Edit #1"
        assert dataset.locked is True
        assert dataset.locked_by == initial_users["user_1"]
        assert dataset.locked_at is not None

        dataset.unlock()

        dataset.refresh_from_db()
        assert dataset.name == "Dataset 1 - Edit #1"
        assert dataset.locked is False
        assert dataset.locked_by is None
        assert dataset.locked_at is None

        dataset.lock()
        last_lock_time = dataset.locked_at

        dataset.name = "Dataset 1 - Edit #2"
        dataset.save(auto_unlock=False)

        dataset.refresh_from_db()
        assert dataset.name == "Dataset 1 - Edit #2"
        assert dataset.locked is True
        assert dataset.locked_by == initial_users["user_1"]
        assert dataset.locked_at is not None
        assert dataset.locked_at == last_lock_time

        last_lock_time = dataset.locked_at

        set_request_for_user(initial_users["user_2"])

        with pytest.raises(PermissionDenied):
            dataset.name = "Dataset 1 - Edit #3"
            dataset.save()

        dataset.refresh_from_db()
        assert dataset.name == "Dataset 1 - Edit #2"
        assert dataset.locked_at == last_lock_time

        dataset.name = "Dataset 1 - Edit #3"
        dataset.locked_at = timezone.now() - timezone.timedelta(minutes=settings.MAX_LOCK_TIME)
        dataset.save()

        dataset.refresh_from_db()
        assert dataset.name == "Dataset 1 - Edit #3"
        assert dataset.locked is False
        assert dataset.locked_by is None
        assert dataset.locked_at is None


@pytest.mark.django_db
class TestUploadsVersionModel:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.uploads_version_1 = UploadsVersion.objects.create()

    def test_string_representation(self):
        """
        Ensure we get the correct string representation of a version.
        """
        assert str(self.uploads_version_1) == f"{self.uploads_version_1.dataset}: {self.uploads_version_1.pk}"

    def test_string_representation_with_version_name(self):
        """
        Ensure we get the correct string representation of a version when its name is set.
        """
        self.uploads_version_1.name = "v1"
        self.uploads_version_1.save()

        assert str(self.uploads_version_1) == f"{self.uploads_version_1.dataset}: {self.uploads_version_1.name}"

    def test_get_all_metadata(self):
        """
        Ensure we get all the metadata of a version and its linked version file, folder and project.
        """
        project_1 = Project.objects.create(
            name="Project 1",
        )

        set_metadata(
            assigned_to_content_type=project_1.get_content_type(),
            assigned_to_object_id=project_1.pk,
            custom_key="custom_key_1",
            value="custom_value_1",
        )

        assert len(project_1.metadata.all()) == 1

        set_metadata(
            assigned_to_content_type=project_1.folders.first().get_content_type(),
            assigned_to_object_id=project_1.folders.first().pk,
            custom_key="custom_key_1",
            value="custom_value_1",
        )

        assert len(project_1.folders.first().metadata.all()) == 1

        dataset_1 = UploadsDataset.objects.create(
            folder=project_1.folders.first(),
        )

        uploads_version_1 = UploadsVersion.objects.create(
            dataset=dataset_1,
        )

        set_metadata(
            assigned_to_content_type=uploads_version_1.get_content_type(),
            assigned_to_object_id=uploads_version_1.pk,
            custom_key="custom_key_1",
            value="custom_value_1",
        )

        assert len(uploads_version_1.metadata.all()) == 1

        uploads_version_file_1 = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        set_metadata(
            assigned_to_content_type=uploads_version_file_1.get_content_type(),
            assigned_to_object_id=uploads_version_file_1.pk,
            custom_key="custom_key_1",
            value="custom_value_1",
        )

        assert len(uploads_version_file_1.metadata.all()) == 1

        uploads_version_1.version_file = uploads_version_file_1
        uploads_version_1.save()

        assert len(uploads_version_1.get_all_metadata()) == 4

    def test_editing_already_published_versions(self):
        """
        Ensure we can't edit an already published version anymore which are not the latest version.
        """
        storage_1 = DynamicStorage.objects.get(
            default=True,
        )

        dataset_1 = UploadsDataset.objects.create(
            name="Dataset 1",
        )

        project = Project.objects.create(
            name="Test project",
        )

        folder = project.folders.first()
        folder.storage = storage_1
        folder.save()

        uploads_version_file_1 = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        uploads_version_1 = UploadsVersion.objects.create(
            dataset=dataset_1,
        )
        uploads_version_1.version_file = uploads_version_file_1
        uploads_version_1.save()

        uploads_version_1.dataset.publish(folder=folder.pk)
        uploads_version_1.save()

        uploads_version_2 = UploadsVersion.objects.create(
            dataset=dataset_1,
        )
        uploads_version_2.version_file = uploads_version_file_1
        uploads_version_2.save()

        # TODO: Fix publishing by dataset and its related missing storage problem
        # uploads_version_2.publish(dataset=uploads_version_1.dataset.pk)
        # uploads_version_2.save()

        # with pytest.raises(PermissionDenied):
        #     uploads_version_1.save()

    def test_delete_version_without_file(self):
        """
        Ensure deleting a version without an attached file does not fail.
        """
        dataset = UploadsDataset.objects.create(
            name="Dataset for deletion",
        )

        uploads_version = UploadsVersion.objects.create(
            dataset=dataset,
            version_file=None,
        )

        uploads_version.delete()
        assert not UploadsVersion.objects.filter(pk=uploads_version.pk).exists()


@pytest.mark.django_db
class TestUploadsVersionLockMixin:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        project_1 = Project.objects.create(
            name="Project 1",
        )

        self.dataset_1 = UploadsDataset.objects.create(
            name="Dataset 1",
            folder=project_1.folders.first(),
        )

        self.uploads_version_1 = UploadsVersion.objects.create(
            dataset=self.dataset_1,
        )

    def test_lock(self, initial_users):
        """
        Ensure we have an expected lock behavior.
        """
        dataset = UploadsDataset.objects.get(pk=self.dataset_1.pk)
        dataset.name = "Dataset 1"
        dataset.save()

        dataset.refresh_from_db()
        assert dataset.name == "Dataset 1"
        assert dataset.locked is False
        assert dataset.locked_by is None
        assert dataset.locked_at is None

        self.uploads_version_1.refresh_from_db()
        assert self.uploads_version_1.locked is False
        assert self.uploads_version_1.locked_by is None
        assert self.uploads_version_1.locked_at is None

        dataset.lock()

        dataset.refresh_from_db()
        assert dataset.name == "Dataset 1"
        assert dataset.locked is True
        assert dataset.locked_by == initial_users["user_1"]
        assert dataset.locked_at is not None

        self.uploads_version_1.refresh_from_db()
        assert self.uploads_version_1.locked is True
        assert self.uploads_version_1.locked_by == initial_users["user_1"]
        assert self.uploads_version_1.locked_at is not None

        last_lock_time = dataset.locked_at

        dataset.lock()

        dataset.refresh_from_db()
        assert dataset.name == "Dataset 1"
        assert dataset.locked is True
        assert dataset.locked_by == initial_users["user_1"]
        assert dataset.locked_at is not None
        assert dataset.locked_at > last_lock_time

        self.uploads_version_1.refresh_from_db()
        assert self.uploads_version_1.locked is True
        assert self.uploads_version_1.locked_by == initial_users["user_1"]
        assert self.uploads_version_1.locked_at is not None
        assert self.uploads_version_1.locked_at > last_lock_time

        dataset.name = "Dataset 1 - Edit #1"
        dataset.save()

        dataset.refresh_from_db()
        assert dataset.name == "Dataset 1 - Edit #1"
        assert dataset.locked is False
        assert dataset.locked_by is None
        assert dataset.locked_at is None

        self.uploads_version_1.refresh_from_db()
        assert self.uploads_version_1.locked is False
        assert self.uploads_version_1.locked_by is None
        assert self.uploads_version_1.locked_at is None

        dataset.lock()

        dataset.refresh_from_db()
        assert dataset.name == "Dataset 1 - Edit #1"
        assert dataset.locked is True
        assert dataset.locked_by == initial_users["user_1"]
        assert dataset.locked_at is not None

        self.uploads_version_1.refresh_from_db()
        assert self.uploads_version_1.locked is True
        assert self.uploads_version_1.locked_by == initial_users["user_1"]
        assert self.uploads_version_1.locked_at is not None

        dataset.unlock()

        dataset.refresh_from_db()
        assert dataset.name == "Dataset 1 - Edit #1"
        assert dataset.locked is False
        assert dataset.locked_by is None
        assert dataset.locked_at is None

        self.uploads_version_1.refresh_from_db()
        assert self.uploads_version_1.locked is False
        assert self.uploads_version_1.locked_by is None
        assert self.uploads_version_1.locked_at is None

        dataset.lock()
        last_lock_time = dataset.locked_at

        dataset.name = "Dataset 1 - Edit #2"
        dataset.save(auto_unlock=False)

        dataset.refresh_from_db()
        assert dataset.name == "Dataset 1 - Edit #2"
        assert dataset.locked is True
        assert dataset.locked_by == initial_users["user_1"]
        assert dataset.locked_at is not None
        assert dataset.locked_at == last_lock_time

        self.uploads_version_1.refresh_from_db()
        assert self.uploads_version_1.locked is True
        assert self.uploads_version_1.locked_by == initial_users["user_1"]
        assert self.uploads_version_1.locked_at is not None
        assert self.uploads_version_1.locked_at == last_lock_time

        last_lock_time = dataset.locked_at

        set_request_for_user(initial_users["user_2"])

        with pytest.raises(PermissionDenied):
            dataset.name = "Dataset 1 - Edit #3"
            dataset.save()

        dataset.refresh_from_db()
        assert dataset.name == "Dataset 1 - Edit #2"
        assert dataset.locked_at == last_lock_time

        self.uploads_version_1.refresh_from_db()
        assert self.uploads_version_1.locked_at == last_lock_time

        dataset.name = "Dataset 1 - Edit #3"
        dataset.locked_at = timezone.now() - timezone.timedelta(minutes=settings.MAX_LOCK_TIME)
        dataset.save()

        dataset.refresh_from_db()
        assert dataset.name == "Dataset 1 - Edit #3"
        assert dataset.locked is False
        assert dataset.locked_by is None
        assert dataset.locked_at is None

        self.uploads_version_1.refresh_from_db()
        assert self.uploads_version_1.locked is False
        assert self.uploads_version_1.locked_by is None
        assert self.uploads_version_1.locked_at is None


@pytest.mark.django_db
class TestUploadsVersionFileModel:
    @pytest.fixture(autouse=True)
    def _setup(self):
        self.uploads_version_file_1 = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
            status=UploadsVersionFile.Status.FINISHED,
        )


@pytest.mark.django_db
class TestUploadsVersionMetadataModel:
    @pytest.fixture(autouse=True)
    def _setup(self):
        self.uploads_version_1 = UploadsVersion.objects.create()

        self.uploads_version_metadata_1 = set_metadata(
            assigned_to_content_type=self.uploads_version_1.get_content_type(),
            assigned_to_object_id=self.uploads_version_1.pk,
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
                assigned_to_content_type=self.uploads_version_1.get_content_type(),
                assigned_to_object_id=self.uploads_version_1.pk,
                field=self.metadata_field_1,
                custom_key="custom_key_1",
                value="custom_value_1",
            )

        with pytest.raises(ValidationError):
            set_metadata(
                assigned_to_content_type=self.uploads_version_1.get_content_type(),
                assigned_to_object_id=self.uploads_version_1.pk,
                value="custom_value_1",
            )


@pytest.mark.django_db
class TestUploadsVersionFileMetadataModel:
    @pytest.fixture(autouse=True)
    def _setup(self):
        self.uploads_version_file_1 = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        self.uploads_version_file_metadata_1 = set_metadata(
            assigned_to_content_type=self.uploads_version_file_1.get_content_type(),
            assigned_to_object_id=self.uploads_version_file_1.pk,
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
                assigned_to_content_type=self.uploads_version_file_1.get_content_type(),
                assigned_to_object_id=self.uploads_version_file_1.pk,
                field=self.metadata_field_1,
                custom_key="custom_key_1",
                value="custom_value_1",
            )

        with pytest.raises(ValidationError):
            set_metadata(
                assigned_to_content_type=self.uploads_version_file_1.get_content_type(),
                assigned_to_object_id=self.uploads_version_file_1.pk,
                value="custom_value_1",
            )
