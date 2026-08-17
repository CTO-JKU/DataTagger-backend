import pytest
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.utils import timezone

from fdm.core.helpers import get_content_type_for_object, set_request_for_user
from fdm.folders.models import Folder
from fdm.metadata.enums import MetadataFieldType
from fdm.metadata.helpers import (
    check_metadata_template_permissions_for_object,
    convert_metadata_value,
    create_metadata_template_for_object,
    set_metadata,
)
from fdm.metadata.models import (
    Metadata,
    MetadataField,
    MetadataTemplate,
    MetadataTemplateContainer,
    MetadataTemplateField,
)
from fdm.projects.models import Project
from fdm.uploads.helpers import (
    create_uploads_version_with_new_file_for_dataset,
    create_uploads_version_with_new_metadata_for_dataset,
)
from fdm.uploads.models import UploadsDataset, UploadsVersion, UploadsVersionFile

sample_file = SimpleUploadedFile(
    "file.jpg",
    b"",
    content_type="image/jpg",
)


@pytest.mark.django_db
class TestMetadataTemplateContainerModel:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.metadata_template_container_1 = MetadataTemplateContainer.objects.create(
            name="Metadata template container 1",
        )

    def test_string_representation(self):
        """
        Ensure we get the correct string representation of a metadata template container.
        """
        assert str(self.metadata_template_container_1) == self.metadata_template_container_1.name

    def test_latest_version(self):
        """
        Ensure we get the latest version of a metadata template container.
        """
        metadata_template_1 = MetadataTemplate.objects.create(
            metadata_template_container=self.metadata_template_container_1,
        )

        assert self.metadata_template_container_1.latest_metadata_template.pk == metadata_template_1.pk

        metadata_template_2 = MetadataTemplate.objects.create(
            metadata_template_container=self.metadata_template_container_1,
        )

        assert self.metadata_template_container_1.latest_metadata_template.pk == metadata_template_2.pk

    def test_restore(self):
        """
        Ensure we can restore a metadata template.
        """
        assert self.metadata_template_container_1.metadata_templates.count() == 0

        metadata_template_1 = MetadataTemplate.objects.create(
            metadata_template_container=self.metadata_template_container_1,
        )

        assert metadata_template_1.metadata_template_fields.count() == 0
        assert self.metadata_template_container_1.metadata_templates.count() == 1
        assert self.metadata_template_container_1.latest_metadata_template == metadata_template_1

        with pytest.raises(PermissionDenied):
            self.metadata_template_container_1.restore_version(
                metadata_template=metadata_template_1,
            )

        metadata_template_2 = MetadataTemplate.objects.create(
            metadata_template_container=self.metadata_template_container_1,
        )

        metadata_template_field_1 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_2,
            custom_key="custom_key_1",
            field_type=MetadataFieldType.TEXT,
            mandatory=False,
            order=7,
        )
        metadata_template_field_1.set_value("custom_value_1")

        assert metadata_template_2.metadata_template_fields.count() == 1
        assert self.metadata_template_container_1.metadata_templates.count() == 2
        assert self.metadata_template_container_1.latest_metadata_template == metadata_template_2

        metadata_template_3 = self.metadata_template_container_1.restore_version(
            metadata_template=metadata_template_1,
        )

        assert metadata_template_3.metadata_template_fields.count() == 0
        assert self.metadata_template_container_1.metadata_templates.count() == 3
        assert self.metadata_template_container_1.latest_metadata_template == metadata_template_3
        assert metadata_template_1 != metadata_template_3

        metadata_template_4 = self.metadata_template_container_1.restore_version(
            metadata_template=metadata_template_2,
        )

        assert metadata_template_4.metadata_template_fields.count() == 1
        assert self.metadata_template_container_1.metadata_templates.count() == 4
        assert self.metadata_template_container_1.latest_metadata_template == metadata_template_4
        assert metadata_template_2 != metadata_template_4
        assert metadata_template_4.metadata_template_fields.get().order == metadata_template_field_1.order

        with pytest.raises(PermissionDenied):
            self.metadata_template_container_1.restore_version(
                metadata_template=metadata_template_4,
            )

        metadata_template_container_2 = MetadataTemplateContainer.objects.create(
            name="Metadata template container 2",
        )

        uploads_version_5 = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_2,
        )

        with pytest.raises(PermissionDenied):
            self.metadata_template_container_1.restore_version(
                metadata_template=uploads_version_5,
            )

        assert self.metadata_template_container_1.metadata_templates.count() == 4
        assert self.metadata_template_container_1.latest_metadata_template == metadata_template_4


@pytest.mark.django_db
class TestMetadataModel:
    def test_field_type_fallback(self):
        """
        Ensure we get the correct field type on the metadata itself if a field is linked to it.
        """
        metadata_field_1 = MetadataField.objects.create(
            key="metadata_field_1",
            field_type=MetadataFieldType.INTEGER,
        )

        metadata_1 = set_metadata(
            field=metadata_field_1,
            value=1,
        )
        assert metadata_1.field_type == MetadataFieldType.INTEGER

    def test_past_and_future_and_related_metadata(self, initial_users):
        """
        Ensure we have a hierarchical structure of past, future, and related metadata.
        """
        uploads_dataset_1 = UploadsDataset.objects.create(
            name="Dataset 1",
        )

        create_uploads_version_with_new_file_for_dataset(
            dataset=uploads_dataset_1,
            uploaded_file=sample_file,
        )

        uploads_version_1 = create_uploads_version_with_new_metadata_for_dataset(
            dataset=uploads_dataset_1,
            metadata_list=[
                dict(
                    custom_key="custom_key_1",
                ),
            ],
        )

        metadata_1 = Metadata.objects.get(
            assigned_to_content_type=uploads_version_1.get_content_type(),
            assigned_to_object_id=uploads_version_1.pk,
            custom_key="custom_key_1",
        )

        assert UploadsDataset.objects.count() == 1
        assert UploadsVersion.objects.count() == 2
        assert (
            Metadata.objects.filter(
                assigned_to_content_type=ContentType.objects.get_for_model(UploadsVersion),
            ).count()
            == 1
        )

        uploads_version_2 = create_uploads_version_with_new_metadata_for_dataset(
            dataset=uploads_dataset_1,
            metadata_list=[
                dict(
                    custom_key="custom_key_1",
                    previous_version_field=metadata_1,
                ),
            ],
        )

        metadata_2 = Metadata.objects.get(
            assigned_to_content_type=uploads_version_2.get_content_type(),
            assigned_to_object_id=uploads_version_2.pk,
            custom_key="custom_key_1",
        )

        assert UploadsDataset.objects.count() == 1
        assert UploadsVersion.objects.count() == 3
        assert (
            Metadata.objects.filter(
                assigned_to_content_type=ContentType.objects.get_for_model(UploadsVersion),
            ).count()
            == 2
        )

        uploads_version_3 = create_uploads_version_with_new_metadata_for_dataset(
            dataset=uploads_dataset_1,
            metadata_list=[
                dict(
                    custom_key="custom_key_1",
                    previous_version_field=metadata_2,
                ),
            ],
        )

        metadata_3 = Metadata.objects.get(
            assigned_to_content_type=uploads_version_3.get_content_type(),
            assigned_to_object_id=uploads_version_3.pk,
            custom_key="custom_key_1",
        )

        assert UploadsDataset.objects.count() == 1
        assert UploadsVersion.objects.count() == 4
        assert (
            Metadata.objects.filter(
                assigned_to_content_type=ContentType.objects.get_for_model(UploadsVersion),
            ).count()
            == 3
        )

        uploads_version_4 = create_uploads_version_with_new_metadata_for_dataset(
            dataset=uploads_dataset_1,
            metadata_list=[
                dict(
                    custom_key="custom_key_1",
                    previous_version_field=metadata_3,
                ),
            ],
        )

        metadata_4 = Metadata.objects.get(
            assigned_to_content_type=uploads_version_4.get_content_type(),
            assigned_to_object_id=uploads_version_4.pk,
            custom_key="custom_key_1",
        )

        assert UploadsDataset.objects.count() == 1
        assert UploadsVersion.objects.count() == 5
        assert (
            Metadata.objects.filter(
                assigned_to_content_type=ContentType.objects.get_for_model(UploadsVersion),
            ).count()
            == 4
        )

        uploads_version_5 = create_uploads_version_with_new_metadata_for_dataset(
            dataset=uploads_dataset_1,
            metadata_list=[
                dict(
                    custom_key="custom_key_1",
                    previous_version_field=metadata_4,
                ),
            ],
        )

        metadata_5 = Metadata.objects.get(
            assigned_to_content_type=uploads_version_5.get_content_type(),
            assigned_to_object_id=uploads_version_5.pk,
            custom_key="custom_key_1",
        )

        assert UploadsDataset.objects.count() == 1
        assert UploadsVersion.objects.count() == 6
        assert (
            Metadata.objects.filter(
                assigned_to_content_type=ContentType.objects.get_for_model(UploadsVersion),
            ).count()
            == 5
        )

        uploads_version_6 = create_uploads_version_with_new_metadata_for_dataset(
            dataset=uploads_dataset_1,
            metadata_list=[
                dict(
                    custom_key="custom_key_1",
                ),
            ],
        )

        metadata_6 = Metadata.objects.get(
            assigned_to_content_type=uploads_version_6.get_content_type(),
            assigned_to_object_id=uploads_version_6.pk,
            custom_key="custom_key_1",
        )

        assert UploadsDataset.objects.count() == 1
        assert UploadsVersion.objects.count() == 7
        assert (
            Metadata.objects.filter(
                assigned_to_content_type=ContentType.objects.get_for_model(UploadsVersion),
            ).count()
            == 6
        )

        uploads_dataset_2 = UploadsDataset.objects.create(
            name="Dataset 2",
        )

        create_uploads_version_with_new_file_for_dataset(
            dataset=uploads_dataset_2,
            uploaded_file=sample_file,
        )

        uploads_version_7 = create_uploads_version_with_new_metadata_for_dataset(
            dataset=uploads_dataset_2,
            metadata_list=[
                dict(
                    custom_key="custom_key_1",
                ),
            ],
        )

        metadata_7 = Metadata.objects.get(
            assigned_to_content_type=uploads_version_7.get_content_type(),
            assigned_to_object_id=uploads_version_7.pk,
            custom_key="custom_key_1",
        )

        assert UploadsDataset.objects.count() == 2
        assert UploadsVersion.objects.count() == 9
        assert (
            Metadata.objects.filter(
                assigned_to_content_type=ContentType.objects.get_for_model(UploadsVersion),
            ).count()
            == 7
        )

        assert metadata_1.previous_version_field is None
        assert metadata_2.previous_version_field == metadata_1
        assert metadata_3.previous_version_field == metadata_2
        assert metadata_4.previous_version_field == metadata_3
        assert metadata_5.previous_version_field == metadata_4
        assert metadata_6.previous_version_field is None
        assert metadata_7.previous_version_field is None

        past_version_fields = metadata_1.get_past_version_fields()
        assert len(past_version_fields) == 0
        assert past_version_fields == []

        future_version_fields = metadata_1.get_future_version_fields()
        assert len(future_version_fields) == 4
        assert future_version_fields[0] == metadata_2
        assert future_version_fields[1] == metadata_3
        assert future_version_fields[2] == metadata_4
        assert future_version_fields[3] == metadata_5

        past_version_fields = metadata_2.get_past_version_fields()
        assert len(past_version_fields) == 1
        assert past_version_fields[0] == metadata_1

        future_version_fields = metadata_2.get_future_version_fields()
        assert len(future_version_fields) == 3
        assert future_version_fields[0] == metadata_3
        assert future_version_fields[1] == metadata_4
        assert future_version_fields[2] == metadata_5

        past_version_fields = metadata_3.get_past_version_fields()
        assert len(past_version_fields) == 2
        assert past_version_fields[0] == metadata_2
        assert past_version_fields[1] == metadata_1

        future_version_fields = metadata_3.get_future_version_fields()
        assert len(future_version_fields) == 2
        assert future_version_fields[0] == metadata_4
        assert future_version_fields[1] == metadata_5

        past_version_fields = metadata_4.get_past_version_fields()
        assert len(past_version_fields) == 3
        assert past_version_fields[0] == metadata_3
        assert past_version_fields[1] == metadata_2
        assert past_version_fields[2] == metadata_1

        future_version_fields = metadata_4.get_future_version_fields()
        assert len(future_version_fields) == 1
        assert future_version_fields[0] == metadata_5

        past_version_fields = metadata_5.get_past_version_fields()
        assert len(past_version_fields) == 4
        assert past_version_fields[0] == metadata_4
        assert past_version_fields[1] == metadata_3
        assert past_version_fields[2] == metadata_2
        assert past_version_fields[3] == metadata_1

        future_version_fields = metadata_5.get_future_version_fields()
        assert len(future_version_fields) == 0
        assert future_version_fields == []

        past_version_fields = metadata_6.get_past_version_fields()
        assert len(past_version_fields) == 0
        assert past_version_fields == []

        future_version_fields = metadata_6.get_future_version_fields()
        assert len(future_version_fields) == 0
        assert future_version_fields == []

        past_version_fields = metadata_7.get_past_version_fields()
        assert len(past_version_fields) == 0
        assert past_version_fields == []

        future_version_fields = metadata_7.get_future_version_fields()
        assert len(future_version_fields) == 0
        assert future_version_fields == []

        assert metadata_1.is_version_of_past_field(metadata_2) is False
        assert metadata_1.is_version_of_past_field(metadata_3) is False
        assert metadata_1.is_version_of_past_field(metadata_4) is False
        assert metadata_1.is_version_of_past_field(metadata_5) is False
        assert metadata_1.is_version_of_future_field(metadata_2) is True
        assert metadata_1.is_version_of_future_field(metadata_3) is True
        assert metadata_1.is_version_of_future_field(metadata_4) is True
        assert metadata_1.is_version_of_future_field(metadata_5) is True
        assert metadata_1.is_related_field(metadata_2) is True
        assert metadata_1.is_related_field(metadata_3) is True
        assert metadata_1.is_related_field(metadata_4) is True
        assert metadata_1.is_related_field(metadata_5) is True

        assert metadata_2.is_version_of_past_field(metadata_1) is True
        assert metadata_2.is_version_of_past_field(metadata_3) is False
        assert metadata_2.is_version_of_past_field(metadata_4) is False
        assert metadata_2.is_version_of_past_field(metadata_5) is False
        assert metadata_2.is_version_of_future_field(metadata_1) is False
        assert metadata_2.is_version_of_future_field(metadata_3) is True
        assert metadata_2.is_version_of_future_field(metadata_4) is True
        assert metadata_2.is_version_of_future_field(metadata_5) is True
        assert metadata_2.is_related_field(metadata_1) is True
        assert metadata_2.is_related_field(metadata_3) is True
        assert metadata_2.is_related_field(metadata_4) is True
        assert metadata_2.is_related_field(metadata_5) is True

        assert metadata_3.is_version_of_past_field(metadata_1) is True
        assert metadata_3.is_version_of_past_field(metadata_2) is True
        assert metadata_3.is_version_of_past_field(metadata_4) is False
        assert metadata_3.is_version_of_past_field(metadata_5) is False
        assert metadata_3.is_version_of_future_field(metadata_1) is False
        assert metadata_3.is_version_of_future_field(metadata_2) is False
        assert metadata_3.is_version_of_future_field(metadata_4) is True
        assert metadata_3.is_version_of_future_field(metadata_5) is True
        assert metadata_3.is_related_field(metadata_1) is True
        assert metadata_3.is_related_field(metadata_2) is True
        assert metadata_3.is_related_field(metadata_4) is True
        assert metadata_3.is_related_field(metadata_5) is True

        assert metadata_4.is_version_of_past_field(metadata_1) is True
        assert metadata_4.is_version_of_past_field(metadata_2) is True
        assert metadata_4.is_version_of_past_field(metadata_3) is True
        assert metadata_4.is_version_of_past_field(metadata_5) is False
        assert metadata_4.is_version_of_future_field(metadata_1) is False
        assert metadata_4.is_version_of_future_field(metadata_2) is False
        assert metadata_4.is_version_of_future_field(metadata_3) is False
        assert metadata_4.is_version_of_future_field(metadata_5) is True
        assert metadata_4.is_related_field(metadata_1) is True
        assert metadata_4.is_related_field(metadata_2) is True
        assert metadata_4.is_related_field(metadata_3) is True
        assert metadata_4.is_related_field(metadata_5) is True

        assert metadata_5.is_version_of_past_field(metadata_1) is True
        assert metadata_5.is_version_of_past_field(metadata_2) is True
        assert metadata_5.is_version_of_past_field(metadata_3) is True
        assert metadata_5.is_version_of_past_field(metadata_4) is True
        assert metadata_5.is_version_of_future_field(metadata_1) is False
        assert metadata_5.is_version_of_future_field(metadata_2) is False
        assert metadata_5.is_version_of_future_field(metadata_3) is False
        assert metadata_5.is_version_of_future_field(metadata_4) is False
        assert metadata_5.is_related_field(metadata_1) is True
        assert metadata_5.is_related_field(metadata_2) is True
        assert metadata_5.is_related_field(metadata_3) is True
        assert metadata_5.is_related_field(metadata_4) is True

    def test_active_metadata_template_duplicates(self, initial_users):
        """
        Ensure we can have only one active metadata template of the same instance in the hierarchy of
        project > folder > dataset.
        """
        set_request_for_user(initial_users["user_1"])

        metadata_template_container_1 = MetadataTemplateContainer.objects.create(
            name="Metadata template container 1",
        )

        metadata_template_1 = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_1,
        )

        metadata_template_container_2 = MetadataTemplateContainer.objects.create(
            name="Metadata template container 2",
        )

        metadata_template_2 = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_2,
        )

        metadata_template_container_3 = MetadataTemplateContainer.objects.create(
            name="Metadata template container 3",
        )

        metadata_template_3 = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_3,
        )

        project_1 = Project.objects.create(
            name="Project 1",
        )

        project_1_folder_1 = Folder.objects.create(
            name="Folder 1",
            project=project_1,
        )

        project_1_folder_1_dataset_1 = UploadsDataset.objects.create(
            name="Dataset 1",
            folder=project_1_folder_1,
        )

        project_1_folder_1_dataset_2 = UploadsDataset.objects.create(
            name="Dataset 2",
            folder=project_1_folder_1,
        )

        project_1_folder_2 = Folder.objects.create(
            name="Folder 2",
            project=project_1,
        )

        project_1_folder_2_dataset_1 = UploadsDataset.objects.create(
            name="Dataset 1",
            folder=project_1_folder_2,
        )

        project_1_folder_2_dataset_2 = UploadsDataset.objects.create(
            name="Dataset 2",
            folder=project_1_folder_2,
        )

        project_2 = Project.objects.create(
            name="Project 2",
        )

        project_2_folder_1 = Folder.objects.create(
            name="Folder 1",
            project=project_2,
        )

        project_2_folder_1_dataset_1 = UploadsDataset.objects.create(
            name="Dataset 1",
            folder=project_2_folder_1,
        )

        project_2_folder_1_dataset_2 = UploadsDataset.objects.create(
            name="Dataset 2",
            folder=project_2_folder_1,
        )

        project_2_folder_2 = Folder.objects.create(
            name="Folder 2",
            project=project_2,
        )

        project_2_folder_2_dataset_1 = UploadsDataset.objects.create(
            name="Dataset 1",
            folder=project_2_folder_2,
        )

        project_2_folder_2_dataset_2 = UploadsDataset.objects.create(
            name="Dataset 2",
            folder=project_2_folder_2,
        )

        # Test case 1: folders have the advantage
        project_1.metadata_template = metadata_template_1
        project_1.save()

        with pytest.raises(ValidationError):
            project_1_folder_1.metadata_template = metadata_template_1
            project_1_folder_1.save()

        with pytest.raises(ValidationError):
            project_1_folder_2.metadata_template = metadata_template_1
            project_1_folder_2.save()

        with pytest.raises(ValidationError):
            project_1_folder_1_dataset_1.metadata_template = metadata_template_1
            project_1_folder_1_dataset_1.save()

        with pytest.raises(ValidationError):
            project_1_folder_1_dataset_2.metadata_template = metadata_template_1
            project_1_folder_1_dataset_2.save()

        with pytest.raises(ValidationError):
            project_1_folder_2_dataset_1.metadata_template = metadata_template_1
            project_1_folder_2_dataset_1.save()

        with pytest.raises(ValidationError):
            project_1_folder_2_dataset_2.metadata_template = metadata_template_1
            project_1_folder_2_dataset_2.save()

        project_2.metadata_template = metadata_template_1
        project_2.save()

        with pytest.raises(ValidationError):
            project_2_folder_1.metadata_template = metadata_template_1
            project_2_folder_1.save()

        with pytest.raises(ValidationError):
            project_2_folder_2.metadata_template = metadata_template_1
            project_2_folder_2.save()

        with pytest.raises(ValidationError):
            project_2_folder_1_dataset_1.metadata_template = metadata_template_1
            project_2_folder_1_dataset_1.save()

        with pytest.raises(ValidationError):
            project_2_folder_1_dataset_2.metadata_template = metadata_template_1
            project_2_folder_1_dataset_2.save()

        with pytest.raises(ValidationError):
            project_2_folder_2_dataset_1.metadata_template = metadata_template_1
            project_2_folder_2_dataset_1.save()

        with pytest.raises(ValidationError):
            project_2_folder_2_dataset_2.metadata_template = metadata_template_1
            project_2_folder_2_dataset_2.save()

        # Reset everything
        project_1.metadata_template = None
        project_1.save()
        project_1_folder_1.metadata_template = None
        project_1_folder_1.save()
        project_1_folder_1_dataset_1.metadata_template = None
        project_1_folder_1_dataset_1.save()
        project_1_folder_1_dataset_2.metadata_template = None
        project_1_folder_1_dataset_2.save()
        project_1_folder_2.metadata_template = None
        project_1_folder_2.save()
        project_1_folder_2_dataset_1.metadata_template = None
        project_1_folder_2_dataset_1.save()
        project_1_folder_2_dataset_2.metadata_template = None
        project_1_folder_2_dataset_2.save()
        project_2.metadata_template = None
        project_2.save()
        project_2_folder_1.metadata_template = None
        project_2_folder_1.save()
        project_2_folder_1_dataset_1.metadata_template = None
        project_2_folder_1_dataset_1.save()
        project_2_folder_1_dataset_2.metadata_template = None
        project_2_folder_1_dataset_2.save()
        project_2_folder_2.metadata_template = None
        project_2_folder_2.save()
        project_2_folder_2_dataset_1.metadata_template = None
        project_2_folder_2_dataset_1.save()
        project_2_folder_2_dataset_2.metadata_template = None
        project_2_folder_2_dataset_2.save()

        # Test case 2: folders have the advantage
        project_1_folder_1.metadata_template = metadata_template_1
        project_1_folder_1.save()

        project_1_folder_2.metadata_template = metadata_template_1
        project_1_folder_2.save()

        with pytest.raises(ValidationError):
            project_1.metadata_template = metadata_template_1
            project_1.save()

        with pytest.raises(ValidationError):
            project_1_folder_1_dataset_1.metadata_template = metadata_template_1
            project_1_folder_1_dataset_1.save()

        with pytest.raises(ValidationError):
            project_1_folder_1_dataset_2.metadata_template = metadata_template_1
            project_1_folder_1_dataset_2.save()

        with pytest.raises(ValidationError):
            project_1_folder_2_dataset_1.metadata_template = metadata_template_1
            project_1_folder_2_dataset_1.save()

        with pytest.raises(ValidationError):
            project_1_folder_2_dataset_2.metadata_template = metadata_template_1
            project_1_folder_2_dataset_2.save()

        project_2_folder_1.metadata_template = metadata_template_1
        project_2_folder_1.save()

        project_2_folder_2.metadata_template = metadata_template_1
        project_2_folder_2.save()

        with pytest.raises(ValidationError):
            project_2.metadata_template = metadata_template_1
            project_2.save()

        with pytest.raises(ValidationError):
            project_2_folder_1_dataset_1.metadata_template = metadata_template_1
            project_2_folder_1_dataset_1.save()

        with pytest.raises(ValidationError):
            project_2_folder_1_dataset_2.metadata_template = metadata_template_1
            project_2_folder_1_dataset_2.save()

        with pytest.raises(ValidationError):
            project_2_folder_2_dataset_1.metadata_template = metadata_template_1
            project_2_folder_2_dataset_1.save()

        with pytest.raises(ValidationError):
            project_2_folder_2_dataset_2.metadata_template = metadata_template_1
            project_2_folder_2_dataset_2.save()

        # Reset everything
        project_1.metadata_template = None
        project_1.save()
        project_1_folder_1.metadata_template = None
        project_1_folder_1.save()
        project_1_folder_1_dataset_1.metadata_template = None
        project_1_folder_1_dataset_1.save()
        project_1_folder_1_dataset_2.metadata_template = None
        project_1_folder_1_dataset_2.save()
        project_1_folder_2.metadata_template = None
        project_1_folder_2.save()
        project_1_folder_2_dataset_1.metadata_template = None
        project_1_folder_2_dataset_1.save()
        project_1_folder_2_dataset_2.metadata_template = None
        project_1_folder_2_dataset_2.save()
        project_2.metadata_template = None
        project_2.save()
        project_2_folder_1.metadata_template = None
        project_2_folder_1.save()
        project_2_folder_1_dataset_1.metadata_template = None
        project_2_folder_1_dataset_1.save()
        project_2_folder_1_dataset_2.metadata_template = None
        project_2_folder_1_dataset_2.save()
        project_2_folder_2.metadata_template = None
        project_2_folder_2.save()
        project_2_folder_2_dataset_1.metadata_template = None
        project_2_folder_2_dataset_1.save()
        project_2_folder_2_dataset_2.metadata_template = None
        project_2_folder_2_dataset_2.save()

        # Test case 3: datasets have the advantage
        project_1_folder_1_dataset_1.metadata_template = metadata_template_1
        project_1_folder_1_dataset_1.save()

        project_1_folder_1_dataset_2.metadata_template = metadata_template_1
        project_1_folder_1_dataset_2.save()

        project_1_folder_2_dataset_1.metadata_template = metadata_template_1
        project_1_folder_2_dataset_1.save()

        project_1_folder_2_dataset_2.metadata_template = metadata_template_1
        project_1_folder_2_dataset_2.save()

        with pytest.raises(ValidationError):
            project_1.metadata_template = metadata_template_1
            project_1.save()

        with pytest.raises(ValidationError):
            project_1_folder_1.metadata_template = metadata_template_1
            project_1_folder_1.save()

        with pytest.raises(ValidationError):
            project_1_folder_2.metadata_template = metadata_template_1
            project_1_folder_2.save()

        project_2_folder_1_dataset_1.metadata_template = metadata_template_1
        project_2_folder_1_dataset_1.save()

        project_2_folder_1_dataset_2.metadata_template = metadata_template_1
        project_2_folder_1_dataset_2.save()

        project_2_folder_2_dataset_1.metadata_template = metadata_template_1
        project_2_folder_2_dataset_1.save()

        project_2_folder_2_dataset_2.metadata_template = metadata_template_1
        project_2_folder_2_dataset_2.save()

        with pytest.raises(ValidationError):
            project_2.metadata_template = metadata_template_1
            project_2.save()

        with pytest.raises(ValidationError):
            project_2_folder_1.metadata_template = metadata_template_1
            project_2_folder_1.save()

        with pytest.raises(ValidationError):
            project_2_folder_2.metadata_template = metadata_template_1
            project_2_folder_2.save()

        # Reset everything
        project_1.metadata_template = None
        project_1.save()
        project_1_folder_1.metadata_template = None
        project_1_folder_1.save()
        project_1_folder_1_dataset_1.metadata_template = None
        project_1_folder_1_dataset_1.save()
        project_1_folder_1_dataset_2.metadata_template = None
        project_1_folder_1_dataset_2.save()
        project_1_folder_2.metadata_template = None
        project_1_folder_2.save()
        project_1_folder_2_dataset_1.metadata_template = None
        project_1_folder_2_dataset_1.save()
        project_1_folder_2_dataset_2.metadata_template = None
        project_1_folder_2_dataset_2.save()
        project_2.metadata_template = None
        project_2.save()
        project_2_folder_1.metadata_template = None
        project_2_folder_1.save()
        project_2_folder_1_dataset_1.metadata_template = None
        project_2_folder_1_dataset_1.save()
        project_2_folder_1_dataset_2.metadata_template = None
        project_2_folder_1_dataset_2.save()
        project_2_folder_2.metadata_template = None
        project_2_folder_2.save()
        project_2_folder_2_dataset_1.metadata_template = None
        project_2_folder_2_dataset_1.save()
        project_2_folder_2_dataset_2.metadata_template = None
        project_2_folder_2_dataset_2.save()

        # Test case 4: different metadata templates on the same hierarchy level
        project_1.metadata_template = metadata_template_1
        project_1.save()

        project_1_folder_1.metadata_template = metadata_template_2
        project_1_folder_1.save()

        project_1_folder_2.metadata_template = metadata_template_2
        project_1_folder_2.save()

        project_1_folder_1_dataset_1.metadata_template = metadata_template_3
        project_1_folder_1_dataset_1.save()

        project_1_folder_1_dataset_2.metadata_template = metadata_template_3
        project_1_folder_1_dataset_2.save()

        project_1_folder_2_dataset_1.metadata_template = metadata_template_3
        project_1_folder_2_dataset_1.save()

        project_1_folder_2_dataset_2.metadata_template = metadata_template_3
        project_1_folder_2_dataset_2.save()

        project_2.metadata_template = metadata_template_1
        project_2.save()

        project_2_folder_1.metadata_template = metadata_template_2
        project_2_folder_1.save()

        project_2_folder_2.metadata_template = metadata_template_2
        project_2_folder_2.save()

        project_2_folder_1_dataset_1.metadata_template = metadata_template_3
        project_2_folder_1_dataset_1.save()

        project_2_folder_1_dataset_2.metadata_template = metadata_template_3
        project_2_folder_1_dataset_2.save()

        project_2_folder_2_dataset_1.metadata_template = metadata_template_3
        project_2_folder_2_dataset_1.save()

        project_2_folder_2_dataset_2.metadata_template = metadata_template_3
        project_2_folder_2_dataset_2.save()


@pytest.mark.django_db
class TestMetadataFieldModel:
    @pytest.fixture(autouse=True)
    def _setup(self):
        self.metadata_field_1 = MetadataField.objects.create(
            key="metadata_field_1",
            field_type=MetadataFieldType.TEXT,
            read_only=False,
        )

    def test_string_representation(self):
        """
        Ensure we get the correct string representation of a metadata field.
        """
        assert str(self.metadata_field_1) == f"{self.metadata_field_1.key} ({self.metadata_field_1.field_type})"

    def test_validate_integer(self):
        """
        Ensure we get a correct validation of an integer value.
        """
        assert Metadata.objects.count() == 0

        set_metadata(
            custom_key="integer_1",
            field_type=MetadataFieldType.INTEGER,
            value=0,
        )
        assert Metadata.objects.count() == 1
        assert Metadata.objects.latest("creation_date").get_value() == "0"

        set_metadata(
            custom_key="integer_2",
            field_type=MetadataFieldType.INTEGER,
            value=1337,
        )
        assert Metadata.objects.count() == 2
        assert Metadata.objects.latest("creation_date").get_value() == "1337"

        set_metadata(
            custom_key="integer_3",
            field_type=MetadataFieldType.INTEGER,
            value=-7331,
        )
        assert Metadata.objects.count() == 3
        assert Metadata.objects.latest("creation_date").get_value() == "-7331"

        set_metadata(
            custom_key="integer_4",
            field_type=MetadataFieldType.INTEGER,
            value="0",
        )
        assert Metadata.objects.count() == 4
        assert Metadata.objects.latest("creation_date").get_value() == "0"

        set_metadata(
            custom_key="integer_5",
            field_type=MetadataFieldType.INTEGER,
            value="1337",
        )
        assert Metadata.objects.count() == 5
        assert Metadata.objects.latest("creation_date").get_value() == "1337"

        set_metadata(
            custom_key="integer_6",
            field_type=MetadataFieldType.INTEGER,
            value="-7331",
        )
        assert Metadata.objects.count() == 6
        assert Metadata.objects.latest("creation_date").get_value() == "-7331"

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="integer_7",
                field_type=MetadataFieldType.INTEGER,
                value="Text",
            )
        assert Metadata.objects.count() == 6

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="integer_8",
                field_type=MetadataFieldType.INTEGER,
                value=13.37,
            )
        assert Metadata.objects.count() == 6

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="integer_9",
                field_type=MetadataFieldType.INTEGER,
                value="13.37",
            )
        assert Metadata.objects.count() == 6

        set_metadata(
            custom_key="integer_10",
            field_type=MetadataFieldType.INTEGER,
            value=None,
        )
        assert Metadata.objects.count() == 7

        set_metadata(
            custom_key="integer_11",
            field_type=MetadataFieldType.INTEGER,
            value="",
        )
        assert Metadata.objects.count() == 8

    def test_validate_decimal(self):
        """
        Ensure we get a correct validation of a decimal numeral value.
        """
        assert Metadata.objects.count() == 0

        set_metadata(
            custom_key="decimal_1",
            field_type=MetadataFieldType.DECIMAL,
            value=0,
        )
        assert Metadata.objects.count() == 1
        assert Metadata.objects.latest("creation_date").get_value() == "0"

        set_metadata(
            custom_key="decimal_2",
            field_type=MetadataFieldType.DECIMAL,
            value=1337,
        )
        assert Metadata.objects.count() == 2
        assert Metadata.objects.latest("creation_date").get_value() == "1337"

        set_metadata(
            custom_key="decimal_3",
            field_type=MetadataFieldType.DECIMAL,
            value=-7331,
        )
        assert Metadata.objects.count() == 3
        assert Metadata.objects.latest("creation_date").get_value() == "-7331"

        set_metadata(
            custom_key="decimal_4",
            field_type=MetadataFieldType.DECIMAL,
            value=0.0,
        )
        assert Metadata.objects.count() == 4
        assert Metadata.objects.latest("creation_date").get_value() == "0.0"

        set_metadata(
            custom_key="decimal_5",
            field_type=MetadataFieldType.DECIMAL,
            value=13.37,
        )
        assert Metadata.objects.count() == 5
        assert Metadata.objects.latest("creation_date").get_value() == "13.37"

        set_metadata(
            custom_key="decimal_6",
            field_type=MetadataFieldType.DECIMAL,
            value="0",
        )
        assert Metadata.objects.count() == 6
        assert Metadata.objects.latest("creation_date").get_value() == "0"

        set_metadata(
            custom_key="decimal_7",
            field_type=MetadataFieldType.DECIMAL,
            value="1337",
        )
        assert Metadata.objects.count() == 7
        assert Metadata.objects.latest("creation_date").get_value() == "1337"

        set_metadata(
            custom_key="decimal_8",
            field_type=MetadataFieldType.DECIMAL,
            value="-7331",
        )
        assert Metadata.objects.count() == 8
        assert Metadata.objects.latest("creation_date").get_value() == "-7331"

        set_metadata(
            custom_key="decimal_9",
            field_type=MetadataFieldType.DECIMAL,
            value="0.0",
        )
        assert Metadata.objects.count() == 9
        assert Metadata.objects.latest("creation_date").get_value() == "0.0"

        set_metadata(
            custom_key="decimal_10",
            field_type=MetadataFieldType.DECIMAL,
            value="13.37",
        )
        assert Metadata.objects.count() == 10
        assert Metadata.objects.latest("creation_date").get_value() == "13.37"

        set_metadata(
            custom_key="decimal_11",
            field_type=MetadataFieldType.DECIMAL,
            value="1e10",
        )
        assert Metadata.objects.count() == 11
        assert Metadata.objects.latest("creation_date").get_value() == "1e10"

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="decimal_12",
                field_type=MetadataFieldType.DECIMAL,
                value="Text",
            )
        assert Metadata.objects.count() == 11

        set_metadata(
            custom_key="decimal_13",
            field_type=MetadataFieldType.DECIMAL,
            value=None,
        )
        assert Metadata.objects.count() == 12

        set_metadata(
            custom_key="decimal_14",
            field_type=MetadataFieldType.DECIMAL,
            value="",
        )
        assert Metadata.objects.count() == 13

    def test_validate_datetime(self):
        """
        Ensure we get a correct validation of a datetime value.
        """
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_1",
                field_type=MetadataFieldType.DATETIME,
                value=1337,
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_2",
                field_type=MetadataFieldType.DATETIME,
                value=13.37,
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_3",
                field_type=MetadataFieldType.DATETIME,
                value="18-02-2025",
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_4",
                field_type=MetadataFieldType.DATETIME,
                value="02-18-2025",
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_5",
                field_type=MetadataFieldType.DATETIME,
                value="18.02.2025",
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_6",
                field_type=MetadataFieldType.DATETIME,
                value="02.18.2025",
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_7",
                field_type=MetadataFieldType.DATETIME,
                value="18/02/2025",
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_8",
                field_type=MetadataFieldType.DATETIME,
                value="02/18/2025",
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_9",
                field_type=MetadataFieldType.DATETIME,
                value="2025-18-02",
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_10",
                field_type=MetadataFieldType.DATETIME,
                value="2025-02-18",
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_11",
                field_type=MetadataFieldType.DATETIME,
                value="2025.18.02",
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_12",
                field_type=MetadataFieldType.DATETIME,
                value="2025.02.18",
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_13",
                field_type=MetadataFieldType.DATETIME,
                value="2025/18/02",
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_14",
                field_type=MetadataFieldType.DATETIME,
                value="2025/02/18",
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_15",
                field_type=MetadataFieldType.DATETIME,
                value="2025 Feb 18",
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_16",
                field_type=MetadataFieldType.DATETIME,
                value="Feb 18 2025",
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_17",
                field_type=MetadataFieldType.DATETIME,
                value="2025 February 18",
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_18",
                field_type=MetadataFieldType.DATETIME,
                value="February 18 2025",
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_19",
                field_type=MetadataFieldType.DATETIME,
                value="2025-2-18",
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_20",
                field_type=MetadataFieldType.DATETIME,
                value="2025-2-2",
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_21",
                field_type=MetadataFieldType.DATETIME,
                value="25-2-2",
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_22",
                field_type=MetadataFieldType.DATETIME,
                value="1900-02-29",
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_23",
                field_type=MetadataFieldType.DATETIME,
                value="1900-02-29 00:00",
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_24",
                field_type=MetadataFieldType.DATETIME,
                value="1900-02-29 00:00:00",
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_25",
                field_type=MetadataFieldType.DATETIME,
                value="2000-02-29",
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_26",
                field_type=MetadataFieldType.DATETIME,
                value="2000-02-29 00:00",
            )
        assert Metadata.objects.count() == 0

        set_metadata(
            custom_key="datetime_27",
            field_type=MetadataFieldType.DATETIME,
            value="2000-02-29 00:00:00",
        )
        assert Metadata.objects.count() == 1

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_28",
                field_type=MetadataFieldType.DATETIME,
                value="2004-02-29",
            )
        assert Metadata.objects.count() == 1

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_29",
                field_type=MetadataFieldType.DATETIME,
                value="2004-02-29 00:00",
            )
        assert Metadata.objects.count() == 1

        set_metadata(
            custom_key="datetime_30",
            field_type=MetadataFieldType.DATETIME,
            value="2004-02-29 00:00:00",
        )
        assert Metadata.objects.count() == 2

        set_metadata(
            custom_key="datetime_31",
            field_type=MetadataFieldType.DATETIME,
            value="2025-02-18 20:00:00",
        )
        assert Metadata.objects.count() == 3

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_32",
                field_type=MetadataFieldType.DATETIME,
                value="2025-02-18 24:00:00",
            )
        assert Metadata.objects.count() == 3

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_33",
                field_type=MetadataFieldType.DATETIME,
                value="10:00",
            )
        assert Metadata.objects.count() == 3

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_34",
                field_type=MetadataFieldType.DATETIME,
                value="10:00:00",
            )
        assert Metadata.objects.count() == 3

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_35",
                field_type=MetadataFieldType.DATETIME,
                value="10:00:00.0000",
            )
        assert Metadata.objects.count() == 3

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_36",
                field_type=MetadataFieldType.DATETIME,
                value="2025-02-18 00:00:00.0000",
            )
        assert Metadata.objects.count() == 3

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="datetime_37",
                field_type=MetadataFieldType.DATETIME,
                value="Text",
            )
        assert Metadata.objects.count() == 3

        set_metadata(
            custom_key="datetime_38",
            field_type=MetadataFieldType.DATETIME,
            value=None,
        )
        assert Metadata.objects.count() == 4

        set_metadata(
            custom_key="datetime_39",
            field_type=MetadataFieldType.DATETIME,
            value="",
        )
        assert Metadata.objects.count() == 5

    def test_validate_date(self):
        """
        Ensure we get a correct validation of a date value.
        """
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="date_1",
                field_type=MetadataFieldType.DATE,
                value=1337,
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="date_2",
                field_type=MetadataFieldType.DATE,
                value=13.37,
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="date_3",
                field_type=MetadataFieldType.DATE,
                value="18-02-2025",
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="date_4",
                field_type=MetadataFieldType.DATE,
                value="02-18-2025",
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="date_5",
                field_type=MetadataFieldType.DATE,
                value="18.02.2025",
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="date_6",
                field_type=MetadataFieldType.DATE,
                value="02.18.2025",
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="date_7",
                field_type=MetadataFieldType.DATE,
                value="18/02/2025",
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="date_8",
                field_type=MetadataFieldType.DATE,
                value="02/18/2025",
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="date_9",
                field_type=MetadataFieldType.DATE,
                value="2025-18-02",
            )
        assert Metadata.objects.count() == 0

        set_metadata(
            custom_key="date_10",
            field_type=MetadataFieldType.DATE,
            value="2025-02-18",
        )
        assert Metadata.objects.count() == 1

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="date_11",
                field_type=MetadataFieldType.DATE,
                value="2025.18.02",
            )
        assert Metadata.objects.count() == 1

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="date_12",
                field_type=MetadataFieldType.DATE,
                value="2025.02.18",
            )
        assert Metadata.objects.count() == 1

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="date_13",
                field_type=MetadataFieldType.DATE,
                value="2025/18/02",
            )
        assert Metadata.objects.count() == 1

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="date_14",
                field_type=MetadataFieldType.DATE,
                value="2025/02/18",
            )
        assert Metadata.objects.count() == 1

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="date_15",
                field_type=MetadataFieldType.DATE,
                value="2025 Feb 18",
            )
        assert Metadata.objects.count() == 1

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="date_16",
                field_type=MetadataFieldType.DATE,
                value="Feb 18 2025",
            )
        assert Metadata.objects.count() == 1

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="date_17",
                field_type=MetadataFieldType.DATE,
                value="2025 February 18",
            )
        assert Metadata.objects.count() == 1

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="date_18",
                field_type=MetadataFieldType.DATE,
                value="February 18 2025",
            )
        assert Metadata.objects.count() == 1

        set_metadata(
            custom_key="date_19",
            field_type=MetadataFieldType.DATE,
            value="2025-2-18",
        )
        assert Metadata.objects.count() == 2

        set_metadata(
            custom_key="date_20",
            field_type=MetadataFieldType.DATE,
            value="2025-2-2",
        )
        assert Metadata.objects.count() == 3

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="date_21",
                field_type=MetadataFieldType.DATE,
                value="25-2-2",
            )
        assert Metadata.objects.count() == 3

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="date_22",
                field_type=MetadataFieldType.DATE,
                value="1900-02-29",
            )
        assert Metadata.objects.count() == 3

        set_metadata(
            custom_key="date_23",
            field_type=MetadataFieldType.DATE,
            value="2000-02-29",
        )
        assert Metadata.objects.count() == 4

        set_metadata(
            custom_key="date_24",
            field_type=MetadataFieldType.DATE,
            value="2004-02-29",
        )
        assert Metadata.objects.count() == 5

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="date_25",
                field_type=MetadataFieldType.DATE,
                value="2004-02-29 00:00",
            )
        assert Metadata.objects.count() == 5

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="date_26",
                field_type=MetadataFieldType.DATE,
                value="2004-02-29 00:00:00",
            )
        assert Metadata.objects.count() == 5

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="date_27",
                field_type=MetadataFieldType.DATE,
                value="Text",
            )
        assert Metadata.objects.count() == 5

        set_metadata(
            custom_key="date_28",
            field_type=MetadataFieldType.DATE,
            value=None,
        )
        assert Metadata.objects.count() == 6

        set_metadata(
            custom_key="date_29",
            field_type=MetadataFieldType.DATE,
            value="",
        )
        assert Metadata.objects.count() == 7

    def test_validate_time(self):
        """
        Ensure we get a correct validation of a time value.
        """
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="time_1",
                field_type=MetadataFieldType.TIME,
                value=1337,
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="time_2",
                field_type=MetadataFieldType.TIME,
                value=13.37,
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="time_3",
                field_type=MetadataFieldType.TIME,
                value="2025-02-18",
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="time_4",
                field_type=MetadataFieldType.TIME,
                value="2025-02-18 00:00",
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="time_5",
                field_type=MetadataFieldType.TIME,
                value="2025-02-18 00:00:00",
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="time_6",
                field_type=MetadataFieldType.TIME,
                value="10:00",
            )
        assert Metadata.objects.count() == 0

        set_metadata(
            custom_key="time_7",
            field_type=MetadataFieldType.TIME,
            value="10:00:00",
        )
        assert Metadata.objects.count() == 1

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="time_8",
                field_type=MetadataFieldType.TIME,
                value="20:00",
            )
        assert Metadata.objects.count() == 1

        set_metadata(
            custom_key="time_9",
            field_type=MetadataFieldType.TIME,
            value="20:00:00",
        )
        assert Metadata.objects.count() == 2

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="time_10",
                field_type=MetadataFieldType.TIME,
                value="20:00:00.0000",
            )
        assert Metadata.objects.count() == 2

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="time_11",
                field_type=MetadataFieldType.TIME,
                value="2004-02-29 00:00",
            )
        assert Metadata.objects.count() == 2

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="time_12",
                field_type=MetadataFieldType.TIME,
                value="2004-02-29 00:00:00",
            )
        assert Metadata.objects.count() == 2

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="time_13",
                field_type=MetadataFieldType.TIME,
                value="2004-02-29 00:00:00.0000",
            )
        assert Metadata.objects.count() == 2

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="time_14",
                field_type=MetadataFieldType.TIME,
                value="Text",
            )
        assert Metadata.objects.count() == 2

        set_metadata(
            custom_key="time_15",
            field_type=MetadataFieldType.TIME,
            value=None,
        )
        assert Metadata.objects.count() == 3

        set_metadata(
            custom_key="time_16",
            field_type=MetadataFieldType.TIME,
            value="",
        )
        assert Metadata.objects.count() == 4

    def test_validate_selection(self):
        """
        Ensure we get a correct validation of a selection value.
        """
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="selection_1",
                field_type=MetadataFieldType.SELECTION,
                value="Option 1",
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="selection_2",
                field_type=MetadataFieldType.SELECTION,
                value="Option 1",
                config=dict(
                    key="value",
                ),
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="selection_3",
                field_type=MetadataFieldType.SELECTION,
                value="Option 1",
                config=dict(
                    options="Option 1",
                ),
            )
        assert Metadata.objects.count() == 0

        set_metadata(
            custom_key="selection_4",
            field_type=MetadataFieldType.SELECTION,
            value="Option 1",
            config=dict(
                options=[
                    "Option 1",
                ],
            ),
        )
        assert Metadata.objects.count() == 1

        metadata = set_metadata(
            custom_key="selection_5",
            field_type=MetadataFieldType.SELECTION,
            value="Option 2",
            config=dict(
                options=[
                    "Option 1",
                    "Option 2",
                    "Option 3",
                ],
            ),
        )
        assert Metadata.objects.count() == 2
        assert metadata.get_value() == "Option 2"
        assert metadata.config is not None
        assert "options" in metadata.config
        assert len(metadata.config["options"]) == 3
        assert all(
            option
            in [
                "Option 1",
                "Option 2",
                "Option 3",
            ]
            for option in metadata.config["options"]
        )

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="selection_6",
                field_type=MetadataFieldType.SELECTION,
                value="",
                config=dict(
                    options=[
                        "",
                        "Option 1",
                        "Option 2",
                        "Option 3",
                    ],
                ),
            )
        assert Metadata.objects.count() == 2

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="selection_7",
                field_type=MetadataFieldType.SELECTION,
                value=None,
                config=dict(
                    options=[
                        "",
                        "Option 1",
                        "Option 2",
                        "Option 3",
                    ],
                ),
            )
        assert Metadata.objects.count() == 2

        set_metadata(
            custom_key="selection_8",
            field_type=MetadataFieldType.SELECTION,
            value=1,
            config=dict(
                options=[
                    "1",
                ],
            ),
        )
        assert Metadata.objects.count() == 3

    def test_validate_multi_selection(self):
        """
        Ensure we get a correct validation of a multi selection value.
        """
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="multi_selection_1",
                field_type=MetadataFieldType.MULTI_SELECTION,
                value=[
                    "Option 1",
                ],
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="multi_selection_2",
                field_type=MetadataFieldType.MULTI_SELECTION,
                value=[
                    "Option 1",
                ],
                config=dict(
                    key="value",
                ),
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="multi_selection_3",
                field_type=MetadataFieldType.MULTI_SELECTION,
                value=[
                    "Option 1",
                ],
                config=dict(
                    options="Option 1",
                ),
            )
        assert Metadata.objects.count() == 0

        set_metadata(
            custom_key="multi_selection_4",
            field_type=MetadataFieldType.MULTI_SELECTION,
            value=[
                "Option 1",
            ],
            config=dict(
                options=[
                    "Option 1",
                ],
            ),
        )
        assert Metadata.objects.count() == 1

        metadata = set_metadata(
            custom_key="multi_selection_5",
            field_type=MetadataFieldType.MULTI_SELECTION,
            value=[
                "Option 1",
                "Option 3",
            ],
            config=dict(
                options=[
                    "Option 1",
                    "Option 2",
                    "Option 3",
                ],
            ),
        )
        assert Metadata.objects.count() == 2
        assert metadata.get_value() == [
            "Option 1",
            "Option 3",
        ]
        assert metadata.config is not None
        assert "options" in metadata.config
        assert len(metadata.config["options"]) == 3
        assert all(
            option
            in [
                "Option 1",
                "Option 2",
                "Option 3",
            ]
            for option in metadata.config["options"]
        )

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="multi_selection_6",
                field_type=MetadataFieldType.MULTI_SELECTION,
                value=[
                    "Option 4",
                ],
                config=dict(
                    options=[
                        "Option 1",
                        "Option 2",
                        "Option 3",
                    ],
                ),
            )
        assert Metadata.objects.count() == 2

        set_metadata(
            custom_key="multi_selection_7",
            field_type=MetadataFieldType.MULTI_SELECTION,
            value=[],
            config=dict(
                options=[
                    "Option 1",
                    "Option 2",
                    "Option 3",
                ],
            ),
        )
        assert Metadata.objects.count() == 3

        set_metadata(
            custom_key="multi_selection_8",
            field_type=MetadataFieldType.MULTI_SELECTION,
            value=None,
            config=dict(
                options=[
                    "Option 1",
                    "Option 2",
                    "Option 3",
                ],
            ),
        )
        assert Metadata.objects.count() == 4

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="multi_selection_9",
                field_type=MetadataFieldType.MULTI_SELECTION,
                value="",
                config=dict(
                    options=[
                        "Option 1",
                        "Option 2",
                        "Option 3",
                    ],
                ),
            )
        assert Metadata.objects.count() == 4

        metadata = set_metadata(
            custom_key="multi_selection_10",
            field_type=MetadataFieldType.MULTI_SELECTION,
            value=1,
            config=dict(
                options=[
                    "1",
                ],
            ),
        )
        assert Metadata.objects.count() == 5
        assert metadata.get_value() == [
            "1",
        ]

    def test_validate_tag(self):
        """
        Ensure we get a correct validation of a tag value.
        """
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="tag_1",
                field_type=MetadataFieldType.TAG,
                value=[
                    "Tag 1",
                ],
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="tag_2",
                field_type=MetadataFieldType.TAG,
                value=[
                    "Tag 1",
                ],
                config=dict(
                    key="value",
                ),
            )
        assert Metadata.objects.count() == 0

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="tag_3",
                field_type=MetadataFieldType.TAG,
                value=[
                    "Tag 1",
                ],
                config=dict(
                    options="Tag 1",
                ),
            )
        assert Metadata.objects.count() == 0

        set_metadata(
            custom_key="tag_4",
            field_type=MetadataFieldType.TAG,
            value=[
                "Tag 1",
            ],
            config=dict(
                options=[
                    "Tag 1",
                ],
            ),
        )
        assert Metadata.objects.count() == 1

        metadata = set_metadata(
            custom_key="tag_5",
            field_type=MetadataFieldType.TAG,
            value=[
                "Tag 1",
                "Tag 3",
            ],
            config=dict(
                options=[
                    "Tag 1",
                    "Tag 2",
                    "Tag 3",
                ],
            ),
        )
        assert Metadata.objects.count() == 2
        assert metadata.get_value() == [
            "Tag 1",
            "Tag 3",
        ]
        assert metadata.config is not None
        assert "options" in metadata.config
        assert len(metadata.config["options"]) == 3
        assert all(
            option
            in [
                "Tag 1",
                "Tag 2",
                "Tag 3",
            ]
            for option in metadata.config["options"]
        )

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="tag_6",
                field_type=MetadataFieldType.TAG,
                value=[
                    "Tag 4",
                ],
                config=dict(
                    options=[
                        "Tag 1",
                        "Tag 2",
                        "Tag 3",
                    ],
                ),
            )
        assert Metadata.objects.count() == 2

        set_metadata(
            custom_key="tag_7",
            field_type=MetadataFieldType.TAG,
            value=[],
            config=dict(
                options=[
                    "Tag 1",
                    "Tag 2",
                    "Tag 3",
                ],
            ),
        )
        assert Metadata.objects.count() == 3

        set_metadata(
            custom_key="tag_8",
            field_type=MetadataFieldType.TAG,
            value=None,
            config=dict(
                options=[
                    "Tag 1",
                    "Tag 2",
                    "Tag 3",
                ],
            ),
        )
        assert Metadata.objects.count() == 4

        with pytest.raises(ValidationError):
            set_metadata(
                custom_key="tag_9",
                field_type=MetadataFieldType.TAG,
                value="",
                config=dict(
                    options=[
                        "Tag 1",
                        "Tag 2",
                        "Tag 3",
                    ],
                ),
            )
        assert Metadata.objects.count() == 4

        metadata = set_metadata(
            custom_key="tag_10",
            field_type=MetadataFieldType.TAG,
            value=1,
            config=dict(
                options=[
                    "1",
                ],
            ),
        )
        assert Metadata.objects.count() == 5
        assert metadata.get_value() == [
            "1",
        ]

    def test_convert_multi_selection_to_selection_raises_for_multiple_values(self):
        """
        Ensure we don't silently lose values when converting to single selection.
        """
        with pytest.raises(ValidationError):
            convert_metadata_value(
                value=[
                    "Option 1",
                    "Option 2",
                ],
                from_field_type=MetadataFieldType.MULTI_SELECTION,
                to_field_type=MetadataFieldType.SELECTION,
            )

    def test_convert_tag_to_selection_raises_for_multiple_values(self):
        """
        Ensure we don't silently lose values when converting tag values to single selection.
        """
        with pytest.raises(ValidationError):
            convert_metadata_value(
                value=[
                    "Tag 1",
                    "Tag 2",
                ],
                from_field_type=MetadataFieldType.TAG,
                to_field_type=MetadataFieldType.SELECTION,
            )

    def test_convert_selection_to_text_raises(self):
        """
        Ensure we don't silently lose selection semantics when converting to text.
        """
        with pytest.raises(ValidationError):
            convert_metadata_value(
                value="Option 1",
                from_field_type=MetadataFieldType.SELECTION,
                to_field_type=MetadataFieldType.TEXT,
            )

    def test_convert_multi_selection_to_text_raises(self):
        """
        Ensure we don't silently lose selection semantics when converting to text.
        """
        with pytest.raises(ValidationError):
            convert_metadata_value(
                value=[
                    "Option 1",
                    "Option 2",
                ],
                from_field_type=MetadataFieldType.MULTI_SELECTION,
                to_field_type=MetadataFieldType.TEXT,
            )

    def test_convert_tag_to_text_raises(self):
        """
        Ensure we don't silently lose selection semantics when converting to text.
        """
        with pytest.raises(ValidationError):
            convert_metadata_value(
                value=[
                    "Tag 1",
                    "Tag 2",
                ],
                from_field_type=MetadataFieldType.TAG,
                to_field_type=MetadataFieldType.TEXT,
            )

    def test_convert_integer_to_selection(self):
        """
        Ensure non-WYSIWYG values can be converted to selection values.
        """
        assert (
            convert_metadata_value(
                value=1337,
                from_field_type=MetadataFieldType.INTEGER,
                to_field_type=MetadataFieldType.SELECTION,
            )
            == "1337"
        )

    def test_convert_numeric_string_to_integer(self):
        """
        Ensure numeric strings can be converted to integer values.
        """
        assert (
            convert_metadata_value(
                value="1",
                from_field_type=MetadataFieldType.TEXT,
                to_field_type=MetadataFieldType.INTEGER,
            )
            == "1"
        )

    def test_convert_non_numeric_string_to_integer_raises(self):
        """
        Ensure non-numeric strings cannot be converted to integer values.
        """
        with pytest.raises(ValidationError):
            convert_metadata_value(
                value="A",
                from_field_type=MetadataFieldType.TEXT,
                to_field_type=MetadataFieldType.INTEGER,
            )

    def test_convert_integer_to_multi_selection(self):
        """
        Ensure non-WYSIWYG values can be converted to multi selection values.
        """
        assert convert_metadata_value(
            value=1337,
            from_field_type=MetadataFieldType.INTEGER,
            to_field_type=MetadataFieldType.MULTI_SELECTION,
        ) == [
            "1337",
        ]

    def test_convert_integer_to_tag(self):
        """
        Ensure non-WYSIWYG values can be converted to tag values.
        """
        assert convert_metadata_value(
            value=1337,
            from_field_type=MetadataFieldType.INTEGER,
            to_field_type=MetadataFieldType.TAG,
        ) == [
            "1337",
        ]

    def test_convert_wysiwyg_to_selection_raises(self):
        """
        Ensure WYSIWYG values cannot be converted to selection values.
        """
        with pytest.raises(ValidationError):
            convert_metadata_value(
                value={
                    "paragraph": "Text",
                },
                from_field_type=MetadataFieldType.WYSIWYG,
                to_field_type=MetadataFieldType.SELECTION,
            )

    def test_convert_wysiwyg_to_multi_selection_raises(self):
        """
        Ensure WYSIWYG values cannot be converted to multi selection values.
        """
        with pytest.raises(ValidationError):
            convert_metadata_value(
                value={
                    "paragraph": "Text",
                },
                from_field_type=MetadataFieldType.WYSIWYG,
                to_field_type=MetadataFieldType.MULTI_SELECTION,
            )

    def test_convert_wysiwyg_to_tag_raises(self):
        """
        Ensure WYSIWYG values cannot be converted to tag values.
        """
        with pytest.raises(ValidationError):
            convert_metadata_value(
                value={
                    "paragraph": "Text",
                },
                from_field_type=MetadataFieldType.WYSIWYG,
                to_field_type=MetadataFieldType.TAG,
            )

    def test_delete(self):
        """
        Ensure we can delete a metadata field if it is not in use.
        """
        assert MetadataField.objects.count() == 1

        self.metadata_field_1.delete()
        assert MetadataField.objects.count() == 0

    def test_delete_protection_due_to_linked_metadata(self):
        """
        Ensure we can't delete a metadata field if it is in use.
        """
        set_metadata(
            field=self.metadata_field_1,
            value="custom_value_1",
        )

        with pytest.raises(PermissionDenied):
            self.metadata_field_1.delete()

    def test_delete_protection_due_to_linked_metadata_field(self):
        """
        Ensure we can't delete a metadata field if it is in use.
        """
        metadata_template_container_1 = MetadataTemplateContainer.objects.create(
            name="Global metadata template container 1",
        )

        metadata_template_1 = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_1,
        )

        MetadataTemplateField.objects.create(
            metadata_template=metadata_template_1,
            field=self.metadata_field_1,
        )

        with pytest.raises(PermissionDenied):
            self.metadata_field_1.delete()

    def test_field_type_protection_due_to_linked_metadata(self):
        """
        Ensure we can't change the field type of a metadata field if it is in use.
        """
        self.metadata_field_1.field_type = MetadataFieldType.DECIMAL
        self.metadata_field_1.save()

        set_metadata(
            field=self.metadata_field_1,
            value=13.37,
        )

        with pytest.raises(PermissionDenied):
            self.metadata_field_1.field_type = MetadataFieldType.INTEGER
            self.metadata_field_1.save()

    def test_field_type_protection_due_to_linked_metadata_field(self):
        """
        Ensure we can't change the field type of a metadata field if it is in use.
        """
        self.metadata_field_1.field_type = MetadataFieldType.DECIMAL
        self.metadata_field_1.save()

        metadata_template_container_1 = MetadataTemplateContainer.objects.create(
            name="Global metadata template container 1",
        )

        metadata_template_1 = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_1,
        )

        MetadataTemplateField.objects.create(
            metadata_template=metadata_template_1,
            field=self.metadata_field_1,
        )

        with pytest.raises(PermissionDenied):
            self.metadata_field_1.field_type = MetadataFieldType.INTEGER
            self.metadata_field_1.save()


@pytest.mark.django_db
class TestMetadataTemplateModel:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.project_1 = Project.objects.create(
            name="Project 1",
        )

    def test_creation_helper(self, initial_users):
        """
        Ensure we can create a new metadata template with a helper function.
        """
        metadata_template = create_metadata_template_for_object(
            metadata_template_data={
                "name": "Metadata template 1",
            },
            metadata_template_fields=[
                {
                    "custom_key": "custom_key_1",
                    "value": "custom value",
                    "mandatory": True,
                },
                {
                    "custom_key": "custom_key_2",
                    "mandatory": False,
                },
            ],
            assigned_to_object=self.project_1,
        )
        assert metadata_template is not None
        assert MetadataTemplate.objects.count() == 1

        metadata_template = create_metadata_template_for_object(
            metadata_template_data={
                "name": "Metadata template 2",
            },
            metadata_template_fields=[
                {
                    "custom_key": "custom_key_1",
                    "value": "custom value",
                    "mandatory": True,
                },
                {
                    "custom_key": "custom_key_2",
                    "mandatory": False,
                },
            ],
            assigned_to_object=self.project_1.folders.first(),
        )
        assert metadata_template is not None
        assert MetadataTemplate.objects.count() == 2

        metadata_template = create_metadata_template_for_object(
            metadata_template_data={
                "name": "Metadata template 3",
            },
            metadata_template_fields=[
                {
                    "custom_key": "custom_key_1",
                    "value": "custom value",
                    "mandatory": True,
                },
                {
                    "custom_key": "custom_key_2",
                    "mandatory": False,
                },
            ],
            assigned_to_object=self.project_1.folders.first(),
            user=initial_users["user_1"],
        )
        assert metadata_template is not None
        assert MetadataTemplate.objects.count() == 3

        with transaction.atomic():
            with pytest.raises(PermissionDenied):
                create_metadata_template_for_object(
                    metadata_template_data={
                        "name": "Metadata template 4",
                    },
                    metadata_template_fields=[
                        {
                            "custom_key": "custom_key_1",
                            "value": "custom value",
                            "mandatory": True,
                        },
                        {
                            "custom_key": "custom_key_2",
                            "mandatory": False,
                        },
                    ],
                    assigned_to_object=self.project_1.folders.first(),
                    user=initial_users["user_2"],
                )

        assert MetadataTemplate.objects.count() == 3

    def test_permission_helper(self, initial_users):
        """
        Ensure we can check the permissions for creating a new metadata template with a helper function.
        """
        check_metadata_template_permissions_for_object(
            content_type=get_content_type_for_object(self.project_1.get_content_type()),
            object_id=self.project_1.pk,
            user=initial_users["user_1"],
        )

        with pytest.raises(PermissionDenied):
            check_metadata_template_permissions_for_object(
                content_type=get_content_type_for_object(self.project_1.get_content_type()),
                object_id=self.project_1.pk,
                user=initial_users["user_2"],
            )

    def test_past_and_future_and_related_metadata_template_fields(self, initial_users):
        """
        Ensure we have a hierarchical structure of past, future, and related metadata template fields.
        """
        metadata_template_container_1 = MetadataTemplateContainer.objects.create(
            name="Project metadata template container 1",
            assigned_to_content_type=self.project_1.get_content_type(),
            assigned_to_object_id=self.project_1.pk,
        )

        metadata_template_1 = MetadataTemplate.objects.create(
            name="Metadata template v1",
            metadata_template_container=metadata_template_container_1,
        )

        metadata_template_field_1 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_1,
            custom_key="custom_key_1",
            previous_version_field=None,
        )

        assert MetadataTemplateContainer.objects.count() == 1
        assert MetadataTemplate.objects.count() == 1
        assert MetadataTemplateField.objects.count() == 1

        metadata_template_2 = MetadataTemplate.objects.create(
            name="Metadata template v2",
            metadata_template_container=metadata_template_container_1,
        )

        metadata_template_field_2 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_2,
            custom_key="custom_key_1",
            previous_version_field=metadata_template_field_1,
        )

        assert MetadataTemplateContainer.objects.count() == 1
        assert MetadataTemplate.objects.count() == 2
        assert MetadataTemplateField.objects.count() == 2

        metadata_template_3 = MetadataTemplate.objects.create(
            name="Metadata template v3",
            metadata_template_container=metadata_template_container_1,
        )

        metadata_template_field_3 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_3,
            custom_key="custom_key_1",
            previous_version_field=metadata_template_field_2,
        )

        assert MetadataTemplateContainer.objects.count() == 1
        assert MetadataTemplate.objects.count() == 3
        assert MetadataTemplateField.objects.count() == 3

        metadata_template_4 = MetadataTemplate.objects.create(
            name="Metadata template v4",
            metadata_template_container=metadata_template_container_1,
        )

        metadata_template_field_4 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_4,
            custom_key="custom_key_1",
            previous_version_field=metadata_template_field_3,
        )

        assert MetadataTemplateContainer.objects.count() == 1
        assert MetadataTemplate.objects.count() == 4
        assert MetadataTemplateField.objects.count() == 4

        metadata_template_5 = MetadataTemplate.objects.create(
            name="Metadata template v5",
            metadata_template_container=metadata_template_container_1,
        )

        metadata_template_field_5 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_5,
            custom_key="custom_key_1",
            previous_version_field=metadata_template_field_4,
        )

        assert MetadataTemplateContainer.objects.count() == 1
        assert MetadataTemplate.objects.count() == 5
        assert MetadataTemplateField.objects.count() == 5

        metadata_template_6 = MetadataTemplate.objects.create(
            name="Metadata template v6",
            metadata_template_container=metadata_template_container_1,
        )

        metadata_template_field_6 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_6,
            custom_key="custom_key_1",
            previous_version_field=None,
        )

        assert MetadataTemplateContainer.objects.count() == 1
        assert MetadataTemplate.objects.count() == 6
        assert MetadataTemplateField.objects.count() == 6

        metadata_template_container_2 = MetadataTemplateContainer.objects.create(
            name="Project metadata template container 2",
            assigned_to_content_type=self.project_1.get_content_type(),
            assigned_to_object_id=self.project_1.pk,
        )

        metadata_template_7 = MetadataTemplate.objects.create(
            name="Metadata template 2 v1",
            metadata_template_container=metadata_template_container_2,
        )

        metadata_template_field_7 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_7,
            custom_key="custom_key_1",
            previous_version_field=None,
        )

        assert MetadataTemplateContainer.objects.count() == 2
        assert MetadataTemplate.objects.count() == 7
        assert MetadataTemplateField.objects.count() == 7

        assert metadata_template_field_1.previous_version_field is None
        assert metadata_template_field_2.previous_version_field == metadata_template_field_1
        assert metadata_template_field_3.previous_version_field == metadata_template_field_2
        assert metadata_template_field_4.previous_version_field == metadata_template_field_3
        assert metadata_template_field_5.previous_version_field == metadata_template_field_4
        assert metadata_template_field_6.previous_version_field is None
        assert metadata_template_field_7.previous_version_field is None

        past_version_fields = metadata_template_field_1.get_past_version_fields()
        assert len(past_version_fields) == 0
        assert past_version_fields == []

        future_version_fields = metadata_template_field_1.get_future_version_fields()
        assert len(future_version_fields) == 4
        assert future_version_fields[0] == metadata_template_field_2
        assert future_version_fields[1] == metadata_template_field_3
        assert future_version_fields[2] == metadata_template_field_4
        assert future_version_fields[3] == metadata_template_field_5

        past_version_fields = metadata_template_field_2.get_past_version_fields()
        assert len(past_version_fields) == 1
        assert past_version_fields[0] == metadata_template_field_1

        future_version_fields = metadata_template_field_2.get_future_version_fields()
        assert len(future_version_fields) == 3
        assert future_version_fields[0] == metadata_template_field_3
        assert future_version_fields[1] == metadata_template_field_4
        assert future_version_fields[2] == metadata_template_field_5

        past_version_fields = metadata_template_field_3.get_past_version_fields()
        assert len(past_version_fields) == 2
        assert past_version_fields[0] == metadata_template_field_2
        assert past_version_fields[1] == metadata_template_field_1

        future_version_fields = metadata_template_field_3.get_future_version_fields()
        assert len(future_version_fields) == 2
        assert future_version_fields[0] == metadata_template_field_4
        assert future_version_fields[1] == metadata_template_field_5

        past_version_fields = metadata_template_field_4.get_past_version_fields()
        assert len(past_version_fields) == 3
        assert past_version_fields[0] == metadata_template_field_3
        assert past_version_fields[1] == metadata_template_field_2
        assert past_version_fields[2] == metadata_template_field_1

        future_version_fields = metadata_template_field_4.get_future_version_fields()
        assert len(future_version_fields) == 1
        assert future_version_fields[0] == metadata_template_field_5

        past_version_fields = metadata_template_field_5.get_past_version_fields()
        assert len(past_version_fields) == 4
        assert past_version_fields[0] == metadata_template_field_4
        assert past_version_fields[1] == metadata_template_field_3
        assert past_version_fields[2] == metadata_template_field_2
        assert past_version_fields[3] == metadata_template_field_1

        future_version_fields = metadata_template_field_5.get_future_version_fields()
        assert len(future_version_fields) == 0
        assert future_version_fields == []

        past_version_fields = metadata_template_field_6.get_past_version_fields()
        assert len(past_version_fields) == 0
        assert past_version_fields == []

        future_version_fields = metadata_template_field_6.get_future_version_fields()
        assert len(future_version_fields) == 0
        assert future_version_fields == []

        past_version_fields = metadata_template_field_7.get_past_version_fields()
        assert len(past_version_fields) == 0
        assert past_version_fields == []

        future_version_fields = metadata_template_field_7.get_future_version_fields()
        assert len(future_version_fields) == 0
        assert future_version_fields == []

        assert metadata_template_field_1.is_version_of_past_field(metadata_template_field_2) is False
        assert metadata_template_field_1.is_version_of_past_field(metadata_template_field_3) is False
        assert metadata_template_field_1.is_version_of_past_field(metadata_template_field_4) is False
        assert metadata_template_field_1.is_version_of_past_field(metadata_template_field_5) is False
        assert metadata_template_field_1.is_version_of_future_field(metadata_template_field_2) is True
        assert metadata_template_field_1.is_version_of_future_field(metadata_template_field_3) is True
        assert metadata_template_field_1.is_version_of_future_field(metadata_template_field_4) is True
        assert metadata_template_field_1.is_version_of_future_field(metadata_template_field_5) is True
        assert metadata_template_field_1.is_related_field(metadata_template_field_2) is True
        assert metadata_template_field_1.is_related_field(metadata_template_field_3) is True
        assert metadata_template_field_1.is_related_field(metadata_template_field_4) is True
        assert metadata_template_field_1.is_related_field(metadata_template_field_5) is True
        assert metadata_template_field_1.get_latest_version_field() == metadata_template_field_5

        assert metadata_template_field_2.is_version_of_past_field(metadata_template_field_1) is True
        assert metadata_template_field_2.is_version_of_past_field(metadata_template_field_3) is False
        assert metadata_template_field_2.is_version_of_past_field(metadata_template_field_4) is False
        assert metadata_template_field_2.is_version_of_past_field(metadata_template_field_5) is False
        assert metadata_template_field_2.is_version_of_future_field(metadata_template_field_1) is False
        assert metadata_template_field_2.is_version_of_future_field(metadata_template_field_3) is True
        assert metadata_template_field_2.is_version_of_future_field(metadata_template_field_4) is True
        assert metadata_template_field_2.is_version_of_future_field(metadata_template_field_5) is True
        assert metadata_template_field_2.is_related_field(metadata_template_field_1) is True
        assert metadata_template_field_2.is_related_field(metadata_template_field_3) is True
        assert metadata_template_field_2.is_related_field(metadata_template_field_4) is True
        assert metadata_template_field_2.is_related_field(metadata_template_field_5) is True
        assert metadata_template_field_2.get_latest_version_field() == metadata_template_field_5

        assert metadata_template_field_3.is_version_of_past_field(metadata_template_field_1) is True
        assert metadata_template_field_3.is_version_of_past_field(metadata_template_field_2) is True
        assert metadata_template_field_3.is_version_of_past_field(metadata_template_field_4) is False
        assert metadata_template_field_3.is_version_of_past_field(metadata_template_field_5) is False
        assert metadata_template_field_3.is_version_of_future_field(metadata_template_field_1) is False
        assert metadata_template_field_3.is_version_of_future_field(metadata_template_field_2) is False
        assert metadata_template_field_3.is_version_of_future_field(metadata_template_field_4) is True
        assert metadata_template_field_3.is_version_of_future_field(metadata_template_field_5) is True
        assert metadata_template_field_3.is_related_field(metadata_template_field_1) is True
        assert metadata_template_field_3.is_related_field(metadata_template_field_2) is True
        assert metadata_template_field_3.is_related_field(metadata_template_field_4) is True
        assert metadata_template_field_3.is_related_field(metadata_template_field_5) is True
        assert metadata_template_field_3.get_latest_version_field() == metadata_template_field_5

        assert metadata_template_field_4.is_version_of_past_field(metadata_template_field_1) is True
        assert metadata_template_field_4.is_version_of_past_field(metadata_template_field_2) is True
        assert metadata_template_field_4.is_version_of_past_field(metadata_template_field_3) is True
        assert metadata_template_field_4.is_version_of_past_field(metadata_template_field_5) is False
        assert metadata_template_field_4.is_version_of_future_field(metadata_template_field_1) is False
        assert metadata_template_field_4.is_version_of_future_field(metadata_template_field_2) is False
        assert metadata_template_field_4.is_version_of_future_field(metadata_template_field_3) is False
        assert metadata_template_field_4.is_version_of_future_field(metadata_template_field_5) is True
        assert metadata_template_field_4.is_related_field(metadata_template_field_1) is True
        assert metadata_template_field_4.is_related_field(metadata_template_field_2) is True
        assert metadata_template_field_4.is_related_field(metadata_template_field_3) is True
        assert metadata_template_field_4.is_related_field(metadata_template_field_5) is True
        assert metadata_template_field_4.get_latest_version_field() == metadata_template_field_5

        assert metadata_template_field_5.is_version_of_past_field(metadata_template_field_1) is True
        assert metadata_template_field_5.is_version_of_past_field(metadata_template_field_2) is True
        assert metadata_template_field_5.is_version_of_past_field(metadata_template_field_3) is True
        assert metadata_template_field_5.is_version_of_past_field(metadata_template_field_4) is True
        assert metadata_template_field_5.is_version_of_future_field(metadata_template_field_1) is False
        assert metadata_template_field_5.is_version_of_future_field(metadata_template_field_2) is False
        assert metadata_template_field_5.is_version_of_future_field(metadata_template_field_3) is False
        assert metadata_template_field_5.is_version_of_future_field(metadata_template_field_4) is False
        assert metadata_template_field_5.is_related_field(metadata_template_field_1) is True
        assert metadata_template_field_5.is_related_field(metadata_template_field_2) is True
        assert metadata_template_field_5.is_related_field(metadata_template_field_3) is True
        assert metadata_template_field_5.is_related_field(metadata_template_field_4) is True
        assert metadata_template_field_5.get_latest_version_field() == metadata_template_field_5

    def test_latest_metadata_template_field_resolves_requested_branch(self, initial_users):
        """
        Ensure target template resolution works when field history branches.
        """
        metadata_template_container = MetadataTemplateContainer.objects.create(
            name="Project metadata template container 1",
            assigned_to_content_type=self.project_1.get_content_type(),
            assigned_to_object_id=self.project_1.pk,
        )
        metadata_template_v1 = MetadataTemplate.objects.create(
            name="Metadata template v1",
            metadata_template_container=metadata_template_container,
        )
        metadata_template_field_v1 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_v1,
            custom_key="custom_key_1",
        )

        metadata_template_v2 = MetadataTemplate.objects.create(
            name="Metadata template v2",
            metadata_template_container=metadata_template_container,
        )
        metadata_template_field_v2 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_v2,
            custom_key="custom_key_1",
            previous_version_field=metadata_template_field_v1,
        )

        metadata_template_v3 = MetadataTemplate.objects.create(
            name="Restored metadata template v3",
            metadata_template_container=metadata_template_container,
        )
        metadata_template_field_v3 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_v3,
            custom_key="custom_key_1",
            previous_version_field=metadata_template_field_v1,
        )

        assert metadata_template_field_v1.get_future_version_fields(metadata_template=metadata_template_v2) == [
            metadata_template_field_v2,
        ]
        assert metadata_template_field_v1.get_future_version_fields(metadata_template=metadata_template_v3) == [
            metadata_template_field_v3,
        ]
        assert (
            metadata_template_field_v1.get_latest_version_field(
                metadata_template=metadata_template_v3,
            )
            == metadata_template_field_v3
        )

    def test_latest_metadata_template_field_resolves_edited_restored_branch(self, initial_users):
        """
        Ensure target template resolution follows restored branches that receive later edits.
        """
        metadata_template_container = MetadataTemplateContainer.objects.create(
            name="Project metadata template container 1",
            assigned_to_content_type=self.project_1.get_content_type(),
            assigned_to_object_id=self.project_1.pk,
        )
        metadata_template_v1 = MetadataTemplate.objects.create(
            name="Metadata template v1",
            metadata_template_container=metadata_template_container,
        )
        metadata_template_field_v1 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_v1,
            custom_key="custom_key_1",
        )

        metadata_template_v2 = MetadataTemplate.objects.create(
            name="Metadata template v2",
            metadata_template_container=metadata_template_container,
        )
        MetadataTemplateField.objects.create(
            metadata_template=metadata_template_v2,
            custom_key="custom_key_1",
            previous_version_field=metadata_template_field_v1,
        )

        metadata_template_v3 = MetadataTemplate.objects.create(
            name="Restored metadata template v3",
            metadata_template_container=metadata_template_container,
        )
        metadata_template_field_v3 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_v3,
            custom_key="custom_key_1",
            previous_version_field=metadata_template_field_v1,
        )

        metadata_template_v4 = MetadataTemplate.objects.create(
            name="Edited restored metadata template v4",
            metadata_template_container=metadata_template_container,
        )
        metadata_template_field_v4 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_v4,
            custom_key="custom_key_1",
            previous_version_field=metadata_template_field_v3,
        )

        assert metadata_template_field_v1.get_future_version_fields(metadata_template=metadata_template_v4) == [
            metadata_template_field_v3,
            metadata_template_field_v4,
        ]
        assert (
            metadata_template_field_v1.get_latest_version_field(
                metadata_template=metadata_template_v4,
            )
            == metadata_template_field_v4
        )


@pytest.mark.django_db
class TestMetadataTemplateContainerLockMixin:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.metadata_template_container_1 = MetadataTemplateContainer.objects.create(
            name="Global metadata template container 1",
        )

        self.metadata_template_1 = MetadataTemplate.objects.create(
            metadata_template_container=self.metadata_template_container_1,
        )

    def test_lock(self, initial_users):
        """
        Ensure we have an expected lock behavior.
        """
        initial_users["user_2"].is_global_metadata_template_admin = True
        initial_users["user_2"].save()

        self.metadata_template_container_1.refresh_from_db()
        assert self.metadata_template_container_1.name == "Global metadata template container 1"
        assert self.metadata_template_container_1.locked is False
        assert self.metadata_template_container_1.locked_by is None
        assert self.metadata_template_container_1.locked_at is None

        self.metadata_template_container_1.lock()

        self.metadata_template_container_1.refresh_from_db()
        assert self.metadata_template_container_1.name == "Global metadata template container 1"
        assert self.metadata_template_container_1.locked is True
        assert self.metadata_template_container_1.locked_by == initial_users["user_1"]
        assert self.metadata_template_container_1.locked_at is not None

        last_lock_time = self.metadata_template_container_1.locked_at

        self.metadata_template_container_1.lock()

        self.metadata_template_container_1.refresh_from_db()
        assert self.metadata_template_container_1.name == "Global metadata template container 1"
        assert self.metadata_template_container_1.locked is True
        assert self.metadata_template_container_1.locked_by == initial_users["user_1"]
        assert self.metadata_template_container_1.locked_at is not None
        assert self.metadata_template_container_1.locked_at > last_lock_time

        self.metadata_template_container_1.name = "Global metadata template container 1 - Edit #1"
        self.metadata_template_container_1.save()

        self.metadata_template_container_1.refresh_from_db()
        assert self.metadata_template_container_1.name == "Global metadata template container 1 - Edit #1"
        assert self.metadata_template_container_1.locked is False
        assert self.metadata_template_container_1.locked_by is None
        assert self.metadata_template_container_1.locked_at is None

        self.metadata_template_container_1.lock()

        self.metadata_template_container_1.refresh_from_db()
        assert self.metadata_template_container_1.name == "Global metadata template container 1 - Edit #1"
        assert self.metadata_template_container_1.locked is True
        assert self.metadata_template_container_1.locked_by == initial_users["user_1"]
        assert self.metadata_template_container_1.locked_at is not None

        self.metadata_template_container_1.unlock()

        self.metadata_template_container_1.refresh_from_db()
        assert self.metadata_template_container_1.name == "Global metadata template container 1 - Edit #1"
        assert self.metadata_template_container_1.locked is False
        assert self.metadata_template_container_1.locked_by is None
        assert self.metadata_template_container_1.locked_at is None

        self.metadata_template_container_1.lock()
        last_lock_time = self.metadata_template_container_1.locked_at

        self.metadata_template_container_1.name = "Global metadata template container 1 - Edit #2"
        self.metadata_template_container_1.save(auto_unlock=False)

        self.metadata_template_container_1.refresh_from_db()
        assert self.metadata_template_container_1.name == "Global metadata template container 1 - Edit #2"
        assert self.metadata_template_container_1.locked is True
        assert self.metadata_template_container_1.locked_by == initial_users["user_1"]
        assert self.metadata_template_container_1.locked_at is not None
        assert self.metadata_template_container_1.locked_at == last_lock_time

        last_lock_time = self.metadata_template_container_1.locked_at

        set_request_for_user(initial_users["user_2"])

        with pytest.raises(PermissionDenied):
            self.metadata_template_container_1.name = "Global metadata template container 1 - Edit #3"
            self.metadata_template_container_1.save()

        self.metadata_template_container_1.refresh_from_db()
        assert self.metadata_template_container_1.name == "Global metadata template container 1 - Edit #2"
        assert self.metadata_template_container_1.locked_at == last_lock_time

        self.metadata_template_container_1.name = "Global metadata template container 1 - Edit #3"
        self.metadata_template_container_1.locked_at = timezone.now() - timezone.timedelta(
            minutes=settings.MAX_LOCK_TIME,
        )
        self.metadata_template_container_1.save()

        self.metadata_template_container_1.refresh_from_db()
        assert self.metadata_template_container_1.name == "Global metadata template container 1 - Edit #3"
        assert self.metadata_template_container_1.locked is False
        assert self.metadata_template_container_1.locked_by is None
        assert self.metadata_template_container_1.locked_at is None


@pytest.mark.django_db
class TestMetadataTemplateLockMixin:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.metadata_template_container_1 = MetadataTemplateContainer.objects.create(
            name="Global metadata template container 1",
        )

        self.metadata_template_1 = MetadataTemplate.objects.create(
            metadata_template_container=self.metadata_template_container_1,
        )

    def test_lock(self, initial_users):
        """
        Ensure we have an expected lock behavior.
        """
        initial_users["user_2"].is_global_metadata_template_admin = True
        initial_users["user_2"].save()

        self.metadata_template_container_1.name = "Metadata template container 1"
        self.metadata_template_container_1.save()

        self.metadata_template_container_1.refresh_from_db()
        assert self.metadata_template_container_1.name == "Metadata template container 1"
        assert self.metadata_template_container_1.locked is False
        assert self.metadata_template_container_1.locked_by is None
        assert self.metadata_template_container_1.locked_at is None

        self.metadata_template_1.refresh_from_db()
        assert self.metadata_template_1.locked is False
        assert self.metadata_template_1.locked_by is None
        assert self.metadata_template_1.locked_at is None

        self.metadata_template_container_1.lock()

        self.metadata_template_container_1.refresh_from_db()
        assert self.metadata_template_container_1.name == "Metadata template container 1"
        assert self.metadata_template_container_1.locked is True
        assert self.metadata_template_container_1.locked_by == initial_users["user_1"]
        assert self.metadata_template_container_1.locked_at is not None

        self.metadata_template_1.refresh_from_db()
        assert self.metadata_template_1.locked is True
        assert self.metadata_template_1.locked_by == initial_users["user_1"]
        assert self.metadata_template_1.locked_at is not None

        last_lock_time = self.metadata_template_container_1.locked_at

        self.metadata_template_container_1.lock()

        self.metadata_template_container_1.refresh_from_db()
        assert self.metadata_template_container_1.name == "Metadata template container 1"
        assert self.metadata_template_container_1.locked is True
        assert self.metadata_template_container_1.locked_by == initial_users["user_1"]
        assert self.metadata_template_container_1.locked_at is not None
        assert self.metadata_template_container_1.locked_at > last_lock_time

        self.metadata_template_1.refresh_from_db()
        assert self.metadata_template_1.locked is True
        assert self.metadata_template_1.locked_by == initial_users["user_1"]
        assert self.metadata_template_1.locked_at is not None
        assert self.metadata_template_1.locked_at > last_lock_time

        self.metadata_template_container_1.name = "Metadata template container 1 - Edit #1"
        self.metadata_template_container_1.save()

        self.metadata_template_container_1.refresh_from_db()
        assert self.metadata_template_container_1.name == "Metadata template container 1 - Edit #1"
        assert self.metadata_template_container_1.locked is False
        assert self.metadata_template_container_1.locked_by is None
        assert self.metadata_template_container_1.locked_at is None

        self.metadata_template_1.refresh_from_db()
        assert self.metadata_template_1.locked is False
        assert self.metadata_template_1.locked_by is None
        assert self.metadata_template_1.locked_at is None

        self.metadata_template_container_1.lock()

        self.metadata_template_container_1.refresh_from_db()
        assert self.metadata_template_container_1.name == "Metadata template container 1 - Edit #1"
        assert self.metadata_template_container_1.locked is True
        assert self.metadata_template_container_1.locked_by == initial_users["user_1"]
        assert self.metadata_template_container_1.locked_at is not None

        self.metadata_template_1.refresh_from_db()
        assert self.metadata_template_1.locked is True
        assert self.metadata_template_1.locked_by == initial_users["user_1"]
        assert self.metadata_template_1.locked_at is not None

        self.metadata_template_container_1.unlock()

        self.metadata_template_container_1.refresh_from_db()
        assert self.metadata_template_container_1.name == "Metadata template container 1 - Edit #1"
        assert self.metadata_template_container_1.locked is False
        assert self.metadata_template_container_1.locked_by is None
        assert self.metadata_template_container_1.locked_at is None

        self.metadata_template_1.refresh_from_db()
        assert self.metadata_template_1.locked is False
        assert self.metadata_template_1.locked_by is None
        assert self.metadata_template_1.locked_at is None

        self.metadata_template_container_1.lock()
        last_lock_time = self.metadata_template_container_1.locked_at

        self.metadata_template_container_1.name = "Metadata template container 1 - Edit #2"
        self.metadata_template_container_1.save(auto_unlock=False)

        self.metadata_template_container_1.refresh_from_db()
        assert self.metadata_template_container_1.name == "Metadata template container 1 - Edit #2"
        assert self.metadata_template_container_1.locked is True
        assert self.metadata_template_container_1.locked_by == initial_users["user_1"]
        assert self.metadata_template_container_1.locked_at is not None
        assert self.metadata_template_container_1.locked_at == last_lock_time

        self.metadata_template_1.refresh_from_db()
        assert self.metadata_template_1.locked is True
        assert self.metadata_template_1.locked_by == initial_users["user_1"]
        assert self.metadata_template_1.locked_at is not None
        assert self.metadata_template_1.locked_at == last_lock_time

        last_lock_time = self.metadata_template_container_1.locked_at

        set_request_for_user(initial_users["user_2"])

        with pytest.raises(PermissionDenied):
            self.metadata_template_container_1.name = "Metadata template container 1 - Edit #3"
            self.metadata_template_container_1.save()

        self.metadata_template_container_1.refresh_from_db()
        assert self.metadata_template_container_1.name == "Metadata template container 1 - Edit #2"
        assert self.metadata_template_container_1.locked_at == last_lock_time

        self.metadata_template_1.refresh_from_db()
        assert self.metadata_template_1.locked_at == last_lock_time

        self.metadata_template_container_1.name = "Metadata template container 1 - Edit #3"
        self.metadata_template_container_1.locked_at = timezone.now() - timezone.timedelta(
            minutes=settings.MAX_LOCK_TIME,
        )
        self.metadata_template_container_1.save()

        self.metadata_template_container_1.refresh_from_db()
        assert self.metadata_template_container_1.name == "Metadata template container 1 - Edit #3"
        assert self.metadata_template_container_1.locked is False
        assert self.metadata_template_container_1.locked_by is None
        assert self.metadata_template_container_1.locked_at is None

        self.metadata_template_1.refresh_from_db()
        assert self.metadata_template_1.locked is False
        assert self.metadata_template_1.locked_by is None
        assert self.metadata_template_1.locked_at is None
