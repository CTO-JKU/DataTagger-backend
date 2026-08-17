import uuid

import pytest
from conftest import auth_user
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from fdm.core.helpers import get_content_type_for_model, get_content_type_for_object, set_request_for_user
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
from fdm.uploads.models import UploadsDataset, UploadsVersion, UploadsVersionFile

sample_file = SimpleUploadedFile(
    "file.jpg",
    b"",
    content_type="image/jpg",
)


@pytest.mark.django_db
class TestMetadataFieldAPI:
    @pytest.fixture(autouse=True)
    def _setup(self):
        self.metadata_field_1 = MetadataField.objects.create(
            key="metadata_field_1",
            field_type=MetadataFieldType.TEXT,
            read_only=True,
        )

        self.metadata_field_2 = MetadataField.objects.create(
            key="metadata_field_2",
            field_type=MetadataFieldType.TEXT,
            read_only=False,
        )

    def test_read_metadata_fields_list(self, client):
        """
        Ensure we can read the metadata fields list.
        """
        url = reverse("metadata-field-list")

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 2

    def test_read_metadata_field_details(self, client):
        """
        Ensure we can read the metadata field details.
        """
        url = reverse(
            "metadata-field-detail",
            kwargs={
                "pk": self.metadata_field_1.pk,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["key"] == self.metadata_field_1.key


@pytest.mark.django_db
class TestMetadataAPI:
    def test_read_metadata_list(self, client):
        """
        Ensure we can read the metadata fields list.
        """
        url = reverse("metadata-list")

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0

        uploads_version_1 = UploadsVersion.objects.create()

        set_metadata(
            assigned_to_content_type=uploads_version_1.get_content_type(),
            assigned_to_object_id=uploads_version_1.pk,
            custom_key="custom_key_1",
            value="custom_value_1",
        )

        url = reverse("metadata-list")

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0

        url_filter = (
            f"{url}?assigned_to_content_type={get_content_type_for_object(uploads_version_1.get_content_type())}"
            f"&assigned_to_object_id={uploads_version_1.pk}"
        )

        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

    def test_bulk_add_metadata_to_uploads_datasets_validation(self, client, initial_users):
        """
        Ensure we can add metadata to uploads datasets in bulk and trigger all available validation errors.
        """
        set_request_for_user(initial_users["user_2"])

        project_1 = Project.objects.create(
            name="Project 1",
        )

        uploads_dataset_1 = UploadsDataset.objects.create(
            name="Dataset 1",
        )

        uploads_dataset_2 = UploadsDataset.objects.create(
            name="Dataset 2",
            folder=project_1.folders.first(),
            publication_date=timezone.now(),
        )

        set_request_for_user(initial_users["user_1"])
        auth_user(client, initial_users["user_1"])

        url = reverse("metadata-bulk-add-to-uploads-datasets")

        metadata_key_value = {
            "custom_key": "random_label_1",
            "value": "Random text 1",
        }

        metadata_pk_value = {
            "field": "7a49c23c-fa4d-4cb2-a619-abc4c2b3bbb1",
            "value": "Random text",
        }

        metadata_field_value = {
            "field": {
                "key": "field_key_6",
                "field_type": MetadataFieldType.TEXT,
            },
            "value": "Random text 2",
        }

        # This request must trigger: "You must provide at least one uploads dataset."
        response = client.post(
            url,
            {
                "metadata": [
                    metadata_key_value,
                    metadata_pk_value,
                    metadata_field_value,
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        # This request must trigger: "At least one uploads dataset provided does not exist."
        response = client.post(
            url,
            {
                "metadata": [
                    metadata_key_value,
                    metadata_pk_value,
                    metadata_field_value,
                ],
                "uploads_datasets": [
                    "7a49c23c-fa4d-4cb2-a619-abc4c2b3bbb1",
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

        # This request must trigger: "You must not edit any unpublished uploads datasets you haven't created yourself."
        response = client.post(
            url,
            {
                "metadata": [
                    metadata_key_value,
                    metadata_pk_value,
                    metadata_field_value,
                ],
                "uploads_datasets": [
                    uploads_dataset_1.pk,
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        # This request must trigger: "You must not edit any published uploads datasets in a folder you haven't got the permission to."
        response = client.post(
            url,
            {
                "metadata": [
                    metadata_key_value,
                    metadata_pk_value,
                    metadata_field_value,
                ],
                "uploads_datasets": [
                    uploads_dataset_2.pk,
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        # This request must trigger: "You must provide at least one metadata."
        response = client.post(
            url,
            {
                "metadata": [],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        # This request must trigger a validation error because of wrong metadata field typing
        response = client.post(
            url,
            {
                "metadata": [
                    {
                        "custom_key": "integer_1",
                        "field_type": MetadataFieldType.INTEGER,
                        "value": "Text",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        set_request_for_user(initial_users["user_1"])

        uploads_dataset_3 = UploadsDataset.objects.create()

        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        UploadsVersion.objects.create(
            dataset=uploads_dataset_3,
            version_file=uploads_version_file,
        )

        metadata_field_1 = MetadataField.objects.create(
            key="metadata_field_1",
            field_type=MetadataFieldType.TEXT,
            read_only=False,
        )
        metadata_pk_value["field"] = metadata_field_1.pk

        response = client.post(
            url,
            {
                "metadata": [
                    metadata_key_value,
                    metadata_pk_value,
                    metadata_field_value,
                ],
                "uploads_datasets": [
                    uploads_dataset_3.pk,
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

    def test_bulk_add_partially_valid_metadata_to_uploads_datasets(self, client, initial_users):
        """
        Ensure we get a correct validation of partially correct values.
        """
        set_request_for_user(initial_users["user_1"])

        url = reverse("metadata-bulk-add-to-uploads-datasets")

        uploads_dataset_1 = UploadsDataset.objects.create()

        uploads_dataset_2 = UploadsDataset.objects.create()

        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        UploadsVersion.objects.create(
            dataset=uploads_dataset_1,
            version_file=uploads_version_file,
        )

        UploadsVersion.objects.create(
            dataset=uploads_dataset_2,
            version_file=uploads_version_file,
        )

        assert Metadata.objects.count() == 0

        auth_user(client, initial_users["user_1"])

        response = client.post(
            url,
            {
                "metadata": [
                    {
                        "custom_key": "integer_1",
                        "field_type": MetadataFieldType.INTEGER,
                        "value": "Text",
                    },
                    {
                        "custom_key": "decimal_1",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": "Text",
                    },
                ],
                "uploads_datasets": [
                    uploads_dataset_1.pk,
                    uploads_dataset_2.pk,
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            url,
            {
                "metadata": [
                    {
                        "custom_key": "integer_1",
                        "field_type": MetadataFieldType.INTEGER,
                        "value": 1,
                    },
                    {
                        "custom_key": "decimal_1",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": "Text",
                    },
                ],
                "uploads_datasets": [
                    uploads_dataset_1.pk,
                    uploads_dataset_2.pk,
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            url,
            {
                "metadata": [
                    {
                        "custom_key": "integer_1",
                        "field_type": MetadataFieldType.INTEGER,
                        "value": "Text",
                    },
                    {
                        "custom_key": "decimal_1",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": 13.37,
                    },
                ],
                "uploads_datasets": [
                    uploads_dataset_1.pk,
                    uploads_dataset_2.pk,
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            url,
            {
                "metadata": [
                    {
                        "custom_key": "integer_1",
                        "field_type": MetadataFieldType.INTEGER,
                        "value": 1,
                    },
                    {
                        "custom_key": "decimal_1",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": 13.37,
                    },
                ],
                "uploads_datasets": [
                    uploads_dataset_1.pk,
                    uploads_dataset_2.pk,
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 4

    def test_bulk_add_metadata_to_uploads_datasets(self, client, initial_users):
        """
        Ensure we can add metadata to uploads datasets in bulk.
        """
        set_request_for_user(initial_users["user_1"])

        url = reverse("metadata-bulk-add-to-uploads-datasets")

        metadata_field_1 = MetadataField.objects.create(
            key="metadata_field_1",
            field_type=MetadataFieldType.TEXT,
            read_only=False,
        )

        metadata_key_value = {
            "custom_key": "random_label_1",
            "value": "Random text 1",
        }

        metadata_pk_value = {
            "field": metadata_field_1.pk,
            "value": "Random text",
        }

        metadata_field_value = {
            "field": {
                "key": "field_key_6",
                "field_type": MetadataFieldType.TEXT,
            },
            "value": "Random text 2",
        }

        uploads_dataset_1 = UploadsDataset.objects.create()

        uploads_dataset_2 = UploadsDataset.objects.create()

        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        UploadsVersion.objects.create(
            dataset=uploads_dataset_1,
            version_file=uploads_version_file,
        )

        UploadsVersion.objects.create(
            dataset=uploads_dataset_2,
            version_file=uploads_version_file,
        )

        assert (
            Metadata.objects.filter(
                assigned_to_content_type=uploads_dataset_1.latest_version.get_content_type(),
            ).count()
            == 0
        )

        auth_user(client, initial_users["user_1"])

        response = client.post(
            url,
            {
                "metadata": [
                    metadata_key_value,
                    metadata_pk_value,
                    metadata_field_value,
                ],
                "uploads_datasets": [
                    uploads_dataset_1.pk,
                    uploads_dataset_2.pk,
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert (
            Metadata.objects.filter(
                assigned_to_content_type=uploads_dataset_1.latest_version.get_content_type(),
            ).count()
            == 6
        )
        assert (
            Metadata.objects.filter(
                assigned_to_content_type=uploads_dataset_1.latest_version.get_content_type(),
                assigned_to_object_id=uploads_dataset_1.latest_version.pk,
            ).count()
            == 3
        )
        assert (
            Metadata.objects.filter(
                assigned_to_content_type=uploads_dataset_2.latest_version.get_content_type(),
                assigned_to_object_id=uploads_dataset_2.latest_version.pk,
            ).count()
            == 3
        )

        response = client.post(
            url,
            {
                "metadata": [
                    metadata_key_value,
                ],
                "uploads_datasets": [
                    uploads_dataset_1.pk,
                    uploads_dataset_2.pk,
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        assert (
            Metadata.objects.filter(
                assigned_to_content_type=uploads_dataset_1.latest_version.get_content_type(),
            ).count()
            == 14
        )
        assert (
            Metadata.objects.filter(
                assigned_to_content_type=uploads_dataset_1.latest_version.get_content_type(),
                assigned_to_object_id=uploads_dataset_1.latest_version.pk,
            ).count()
            == 4
        )
        assert (
            Metadata.objects.filter(
                assigned_to_content_type=uploads_dataset_2.latest_version.get_content_type(),
                assigned_to_object_id=uploads_dataset_2.latest_version.pk,
            ).count()
            == 4
        )

        response = client.post(
            url,
            {
                "metadata": [
                    metadata_key_value,
                ],
                "uploads_datasets": [
                    uploads_dataset_1.pk,
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        assert (
            Metadata.objects.filter(
                assigned_to_content_type=uploads_dataset_1.latest_version.get_content_type(),
            ).count()
            == 19
        )
        assert (
            Metadata.objects.filter(
                assigned_to_content_type=uploads_dataset_1.latest_version.get_content_type(),
                assigned_to_object_id=uploads_dataset_1.latest_version.pk,
            ).count()
            == 5
        )
        assert (
            Metadata.objects.filter(
                assigned_to_content_type=uploads_dataset_2.latest_version.get_content_type(),
                assigned_to_object_id=uploads_dataset_2.latest_version.pk,
            ).count()
            == 4
        )

    def test_partially_valid_metadata(self, client, initial_users):
        """
        Ensure we get a correct validation of partially correct values.
        """
        set_request_for_user(initial_users["user_1"])

        uploads_dataset_1 = UploadsDataset.objects.create()

        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        UploadsVersion.objects.create(
            dataset=uploads_dataset_1,
            version_file=uploads_version_file,
        )

        assert Metadata.objects.count() == 0

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": uploads_dataset_1.pk,
            },
        )
        action_url = f"{url}version/"

        auth_user(client, initial_users["user_1"])

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "integer_1",
                        "field_type": MetadataFieldType.INTEGER,
                        "value": "Text",
                    },
                    {
                        "custom_key": "decimal_1",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": "Text",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "integer_1",
                        "field_type": MetadataFieldType.INTEGER,
                        "value": 1,
                    },
                    {
                        "custom_key": "decimal_1",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": "Text",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "integer_1",
                        "field_type": MetadataFieldType.INTEGER,
                        "value": "Text",
                    },
                    {
                        "custom_key": "decimal_1",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": 13.37,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "integer_1",
                        "field_type": MetadataFieldType.INTEGER,
                        "value": 1,
                    },
                    {
                        "custom_key": "decimal_1",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": 13.37,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 2

    def test_metadata_with_metadata_template_field(self, client, initial_users):
        """
        Ensure we get a correct reference for a linked metadata template field.
        """
        set_request_for_user(initial_users["user_1"])

        uploads_dataset_1 = UploadsDataset.objects.create()

        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        UploadsVersion.objects.create(
            dataset=uploads_dataset_1,
            version_file=uploads_version_file,
        )

        metadata_template_container_1 = MetadataTemplateContainer.objects.create(
            name="Global metadata template container 1",
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

        assert metadata_template_1.metadata_template_fields.count() == 1

        metadata_template_container_2 = MetadataTemplateContainer.objects.create(
            name="Global metadata template container 2",
        )

        metadata_template_2 = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_2,
        )

        metadata_template_field_2 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_2,
            custom_key="metadata_template_field_2",
            field_type=MetadataFieldType.TEXT,
            mandatory=False,
        )
        metadata_template_field_2.set_value("Text")

        assert metadata_template_2.metadata_template_fields.count() == 1

        metadata_template_container_3 = MetadataTemplateContainer.objects.create(
            name="Global metadata template container 3",
        )

        metadata_template_3 = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_3,
        )

        metadata_template_field_3 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_3,
            custom_key="metadata_template_field_3",
            field_type=MetadataFieldType.INTEGER,
            mandatory=False,
        )
        metadata_template_field_3.set_value("1337")

        metadata_template_field_4 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_3,
            custom_key="metadata_template_field_4",
            field_type=MetadataFieldType.DECIMAL,
            mandatory=False,
        )
        metadata_template_field_4.set_value("13.37")

        assert metadata_template_3.metadata_template_fields.count() == 2

        project_1 = Project.objects.create(
            name="Project 1",
            metadata_template=metadata_template_2,
        )
        assert project_1.metadata_template == metadata_template_2
        assert project_1.metadata_template.metadata_template_fields.count() == 1

        folder_1 = project_1.folders.first()
        folder_1.metadata_template = metadata_template_3
        folder_1.save()
        assert folder_1.metadata_template == metadata_template_3
        assert folder_1.metadata_template.metadata_template_fields.count() == 2

        assert Metadata.objects.count() == 0

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": uploads_dataset_1.pk,
            },
        )
        version_action_url = f"{url}version/"
        file_action_url = f"{url}file/"
        publish_action_url = f"{url}publish/"

        auth_user(client, initial_users["user_1"])

        assert uploads_dataset_1.uploads_versions.count() == 1

        response = client.post(
            version_action_url,
            {
                "metadata": [
                    {
                        "custom_key": "text_1",
                        "field_type": MetadataFieldType.TEXT,
                        "value": "Without a metadata template field reference",
                        "metadata_template_field": None,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert uploads_dataset_1.uploads_versions.count() == 2
        assert (
            Metadata.objects.filter(
                assigned_to_content_type=uploads_dataset_1.latest_version.get_content_type(),
            ).count()
            == 1
        )
        assert Metadata.objects.filter(metadata_template_field__isnull=False).count() == 0

        response = client.post(
            version_action_url,
            {
                "metadata": [
                    {
                        "custom_key": "text_2",
                        "field_type": MetadataFieldType.TEXT,
                        "value": "With a metadata template field reference",
                        "metadata_template_field": metadata_template_field_1.pk,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert uploads_dataset_1.uploads_versions.count() == 3
        assert (
            Metadata.objects.filter(
                assigned_to_content_type=uploads_dataset_1.latest_version.get_content_type(),
            ).count()
            == 2
        )
        metadata = Metadata.objects.filter(
            assigned_to_content_type=uploads_dataset_1.latest_version.get_content_type(),
            assigned_to_object_id=uploads_dataset_1.latest_version.pk,
            metadata_template_field__isnull=False,
        )
        assert metadata.count() == 1
        assert metadata.first().metadata_template_field == metadata_template_field_1
        assert Metadata.objects.filter(metadata_template_field__isnull=False).count() == 1

        response = client.post(
            file_action_url,
            {
                "file": sample_file,
            },
            format="multipart",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert uploads_dataset_1.uploads_versions.count() == 4
        assert (
            Metadata.objects.filter(
                assigned_to_content_type=uploads_dataset_1.latest_version.get_content_type(),
            ).count()
            == 3
        )
        metadata = Metadata.objects.filter(
            assigned_to_content_type=uploads_dataset_1.latest_version.get_content_type(),
            assigned_to_object_id=uploads_dataset_1.latest_version.pk,
            metadata_template_field__isnull=False,
        )
        assert metadata.count() == 1
        assert metadata.first().metadata_template_field == metadata_template_field_1
        assert Metadata.objects.filter(metadata_template_field__isnull=False).count() == 2

        metadata_template_field_1.delete()

        assert (
            Metadata.objects.filter(
                assigned_to_content_type=uploads_dataset_1.latest_version.get_content_type(),
            ).count()
            == 3
        )
        assert Metadata.objects.filter(metadata_template_field__isnull=False).count() == 0

        response = client.post(
            publish_action_url,
            {
                "folder": folder_1.pk,
            },
            format="multipart",
        )
        assert response.status_code == status.HTTP_201_CREATED

        uploads_dataset_1.refresh_from_db()
        assert uploads_dataset_1.uploads_versions.count() == 5
        assert response.data["publication_date"] is not None
        assert response.data["folder"] is not None
        assert response.data["folder"]["pk"] == str(folder_1.pk)
        assert response.data["folder"]["pk"] == str(uploads_dataset_1.folder.pk)
        assert len(uploads_dataset_1.get_all_metadata_template_fields()) == 3
        assert (
            Metadata.objects.filter(
                assigned_to_content_type=uploads_dataset_1.latest_version.get_content_type(),
            ).count()
            == 7
        )
        assert (
            Metadata.objects.filter(
                assigned_to_content_type=uploads_dataset_1.latest_version.get_content_type(),
                assigned_to_object_id=uploads_dataset_1.latest_version.pk,
            ).count()
            == 4
        )
        metadata = Metadata.objects.filter(
            assigned_to_content_type=uploads_dataset_1.latest_version.get_content_type(),
            assigned_to_object_id=uploads_dataset_1.latest_version.pk,
        )
        assert (
            metadata.filter(
                metadata_template_field__isnull=False,
            ).count()
            == 3
        )
        assert all(
            metadata.metadata_template_field
            in [
                metadata_template_field_2,
                metadata_template_field_3,
                metadata_template_field_4,
            ]
            for metadata in metadata.filter(
                metadata_template_field__isnull=False,
            )
        )
        assert all(
            metadata.get_value()
            in [
                metadata_template_field_2.get_value(),
                metadata_template_field_3.get_value(),
                metadata_template_field_4.get_value(),
            ]
            for metadata in metadata.filter(
                metadata_template_field__isnull=False,
            )
        )
        assert (
            metadata.filter(
                metadata_template_field__isnull=True,
            )
            .first()
            .custom_key
            == "text_2"
        )
        assert (
            metadata.filter(
                metadata_template_field__isnull=True,
            )
            .first()
            .get_value()
            == "With a metadata template field reference"
        )

    def test_validate_integer(self, client, initial_users):
        """
        Ensure we get a correct validation of an integer value.
        """
        set_request_for_user(initial_users["user_1"])

        uploads_dataset_1 = UploadsDataset.objects.create()

        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        UploadsVersion.objects.create(
            dataset=uploads_dataset_1,
            version_file=uploads_version_file,
        )

        assert Metadata.objects.count() == 0

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": uploads_dataset_1.pk,
            },
        )
        action_url = f"{url}version/"

        auth_user(client, initial_users["user_1"])

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "integer_1",
                        "field_type": MetadataFieldType.INTEGER,
                        "value": 0,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 1

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "integer_2",
                        "field_type": MetadataFieldType.INTEGER,
                        "value": 1337,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 2

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "integer_3",
                        "field_type": MetadataFieldType.INTEGER,
                        "value": -7331,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 3

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "integer_4",
                        "field_type": MetadataFieldType.INTEGER,
                        "value": "0",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 4

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "integer_5",
                        "field_type": MetadataFieldType.INTEGER,
                        "value": "1337",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 5

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "integer_6",
                        "field_type": MetadataFieldType.INTEGER,
                        "value": "-7331",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 6

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "integer_7",
                        "field_type": MetadataFieldType.INTEGER,
                        "value": "Text",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 6

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "integer_8",
                        "field_type": MetadataFieldType.INTEGER,
                        "value": 13.37,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 6

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "integer_9",
                        "field_type": MetadataFieldType.INTEGER,
                        "value": "13.37",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 6

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "integer_10",
                        "field_type": MetadataFieldType.INTEGER,
                        "value": None,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 7

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "integer_11",
                        "field_type": MetadataFieldType.INTEGER,
                        "value": "",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 8

    def test_validate_decimal(self, client, initial_users):
        """
        Ensure we get a correct validation of a decimal numeral value.
        """
        set_request_for_user(initial_users["user_1"])

        uploads_dataset_1 = UploadsDataset.objects.create()

        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        UploadsVersion.objects.create(
            dataset=uploads_dataset_1,
            version_file=uploads_version_file,
        )

        assert Metadata.objects.count() == 0

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": uploads_dataset_1.pk,
            },
        )
        action_url = f"{url}version/"

        auth_user(client, initial_users["user_1"])

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "decimal_1",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": 0,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 1
        assert Metadata.objects.latest("creation_date").get_value() == "0"

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "decimal_2",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": 1337,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 2
        assert Metadata.objects.latest("creation_date").get_value() == "1337"

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "decimal_3",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": -7331,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 3
        assert Metadata.objects.latest("creation_date").get_value() == "-7331"

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "decimal_4",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": 0.0,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 4
        assert Metadata.objects.latest("creation_date").get_value() == "0.0"

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "decimal_5",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": 13.37,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 5
        assert Metadata.objects.latest("creation_date").get_value() == "13.37"

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "decimal_6",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": "0",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 6
        assert Metadata.objects.latest("creation_date").get_value() == "0"

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "decimal_7",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": "1337",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 7
        assert Metadata.objects.latest("creation_date").get_value() == "1337"

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "decimal_8",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": "-7331",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 8
        assert Metadata.objects.latest("creation_date").get_value() == "-7331"

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "decimal_9",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": "0.0",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 9
        assert Metadata.objects.latest("creation_date").get_value() == "0.0"

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "decimal_10",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": "13.37",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 10
        assert Metadata.objects.latest("creation_date").get_value() == "13.37"

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "decimal_11",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": "1e10",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 11
        assert Metadata.objects.latest("creation_date").get_value() == "1e10"

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "decimal_12",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": "Text",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 11

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "decimal_13",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": None,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 12

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "decimal_14",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": "",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 13

    def test_validate_datetime(self, client, initial_users):
        """
        Ensure we get a correct validation of a datetime value.
        """
        set_request_for_user(initial_users["user_1"])

        uploads_dataset_1 = UploadsDataset.objects.create()

        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        UploadsVersion.objects.create(
            dataset=uploads_dataset_1,
            version_file=uploads_version_file,
        )

        assert Metadata.objects.count() == 0

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": uploads_dataset_1.pk,
            },
        )
        action_url = f"{url}version/"

        auth_user(client, initial_users["user_1"])

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": 1337,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_2",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": 13.37,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_3",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "18-02-2025",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_4",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "02-18-2025",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_5",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "18.02.2025",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_6",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "02.18.2025",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_7",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "18/02/2025",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_8",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "02/18/2025",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_9",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2025-18-02",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_10",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2025-02-18",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_11",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2025.18.02",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_12",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2025.02.18",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_13",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2025/18/02",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_14",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2025/02/18",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_15",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2025 Feb 18",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_16",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "Feb 18 2025",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_17",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2025 February 18",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_18",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "February 18 2025",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_19",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2025-2-18",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_20",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2025-2-2",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_21",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "25-2-2",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_22",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "1900-02-29",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_23",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "1900-02-29 00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_24",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "1900-02-29 00:00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_25",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2000-02-29",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_26",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2000-02-29 00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_27",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2000-02-29 00:00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 1

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_28",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2004-02-29",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 1

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_29",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2004-02-29 00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 1

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_30",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2004-02-29 00:00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 2

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_31",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2025-02-18 20:00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 3

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_32",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2025-02-18 24:00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 3

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_33",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "10:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 3

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_34",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "10:00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 3

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_35",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "10:00:00.0000",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 3

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_36",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2025-02-18 00:00:00.0000",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 3

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_37",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "Text",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 3

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_38",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": None,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 4

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_39",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 5

    def test_validate_date(self, client, initial_users):
        """
        Ensure we get a correct validation of a date value.
        """
        set_request_for_user(initial_users["user_1"])

        uploads_dataset_1 = UploadsDataset.objects.create()

        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        UploadsVersion.objects.create(
            dataset=uploads_dataset_1,
            version_file=uploads_version_file,
        )

        assert Metadata.objects.count() == 0

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": uploads_dataset_1.pk,
            },
        )
        action_url = f"{url}version/"

        auth_user(client, initial_users["user_1"])

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "date_1",
                        "field_type": MetadataFieldType.DATE,
                        "value": 1337,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "date_2",
                        "field_type": MetadataFieldType.DATE,
                        "value": 13.37,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "date_3",
                        "field_type": MetadataFieldType.DATE,
                        "value": "18-02-2025",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "date_4",
                        "field_type": MetadataFieldType.DATE,
                        "value": "02-18-2025",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "date_5",
                        "field_type": MetadataFieldType.DATE,
                        "value": "18.02.2025",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "date_6",
                        "field_type": MetadataFieldType.DATE,
                        "value": "02.18.2025",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "date_7",
                        "field_type": MetadataFieldType.DATE,
                        "value": "18/02/2025",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "date_8",
                        "field_type": MetadataFieldType.DATE,
                        "value": "02/18/2025",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "date_9",
                        "field_type": MetadataFieldType.DATE,
                        "value": "2025-18-02",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "date_10",
                        "field_type": MetadataFieldType.DATE,
                        "value": "2025-02-18",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 1

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "date_11",
                        "field_type": MetadataFieldType.DATE,
                        "value": "2025.18.02",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 1

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "date_12",
                        "field_type": MetadataFieldType.DATE,
                        "value": "2025.02.18",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 1

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "date_13",
                        "field_type": MetadataFieldType.DATE,
                        "value": "2025/18/02",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 1

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "date_14",
                        "field_type": MetadataFieldType.DATE,
                        "value": "2025/02/18",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 1

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "date_15",
                        "field_type": MetadataFieldType.DATE,
                        "value": "2025 Feb 18",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 1

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "date_16",
                        "field_type": MetadataFieldType.DATE,
                        "value": "Feb 18 2025",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 1

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "date_17",
                        "field_type": MetadataFieldType.DATE,
                        "value": "2025 February 18",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 1

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "date_18",
                        "field_type": MetadataFieldType.DATE,
                        "value": "February 18 2025",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 1

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "date_19",
                        "field_type": MetadataFieldType.DATE,
                        "value": "2025-2-18",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 2

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "date_20",
                        "field_type": MetadataFieldType.DATE,
                        "value": "2025-2-2",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 3

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "date_21",
                        "field_type": MetadataFieldType.DATE,
                        "value": "25-2-2",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 3

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "date_22",
                        "field_type": MetadataFieldType.DATE,
                        "value": "1900-02-29",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 3

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "date_23",
                        "field_type": MetadataFieldType.DATE,
                        "value": "2000-02-29",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 4

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "date_24",
                        "field_type": MetadataFieldType.DATE,
                        "value": "2004-02-29",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 5

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "date_25",
                        "field_type": MetadataFieldType.DATE,
                        "value": "2004-02-29 00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 5

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "date_26",
                        "field_type": MetadataFieldType.DATE,
                        "value": "2004-02-29 00:00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 5

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "date_27",
                        "field_type": MetadataFieldType.DATE,
                        "value": "Text",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 5

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "date_28",
                        "field_type": MetadataFieldType.DATE,
                        "value": None,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 6

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "date_29",
                        "field_type": MetadataFieldType.DATE,
                        "value": "",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 7

    def test_validate_time(self, client, initial_users):
        """
        Ensure we get a correct validation of a time value.
        """
        set_request_for_user(initial_users["user_1"])

        uploads_dataset_1 = UploadsDataset.objects.create()

        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        UploadsVersion.objects.create(
            dataset=uploads_dataset_1,
            version_file=uploads_version_file,
        )

        assert Metadata.objects.count() == 0

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": uploads_dataset_1.pk,
            },
        )
        action_url = f"{url}version/"

        auth_user(client, initial_users["user_1"])

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "time_1",
                        "field_type": MetadataFieldType.TIME,
                        "value": 1337,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "time_2",
                        "field_type": MetadataFieldType.TIME,
                        "value": 13.37,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "time_3",
                        "field_type": MetadataFieldType.TIME,
                        "value": "2025-02-18",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "time_4",
                        "field_type": MetadataFieldType.TIME,
                        "value": "2025-02-18 00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "time_5",
                        "field_type": MetadataFieldType.TIME,
                        "value": "2025-02-18 00:00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "time_6",
                        "field_type": MetadataFieldType.TIME,
                        "value": "10:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "time_7",
                        "field_type": MetadataFieldType.TIME,
                        "value": "10:00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 1

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "time_8",
                        "field_type": MetadataFieldType.TIME,
                        "value": "20:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 1

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "time_9",
                        "field_type": MetadataFieldType.TIME,
                        "value": "20:00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 2

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "time_10",
                        "field_type": MetadataFieldType.TIME,
                        "value": "20:00:00.0000",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 2

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "time_11",
                        "field_type": MetadataFieldType.TIME,
                        "value": "2004-02-29 00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 2

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "time_12",
                        "field_type": MetadataFieldType.TIME,
                        "value": "2004-02-29 00:00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 2

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "time_13",
                        "field_type": MetadataFieldType.TIME,
                        "value": "2004-02-29 00:00:00.0000",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 2

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "time_14",
                        "field_type": MetadataFieldType.TIME,
                        "value": "Text",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 2

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "time_15",
                        "field_type": MetadataFieldType.TIME,
                        "value": None,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 3

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "time_16",
                        "field_type": MetadataFieldType.TIME,
                        "value": "",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 4

    def test_validate_selection(self, client, initial_users):
        """
        Ensure we get a correct validation of a selection value.
        """
        set_request_for_user(initial_users["user_1"])

        uploads_dataset_1 = UploadsDataset.objects.create()

        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        UploadsVersion.objects.create(
            dataset=uploads_dataset_1,
            version_file=uploads_version_file,
        )

        assert Metadata.objects.count() == 0

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": uploads_dataset_1.pk,
            },
        )
        action_url = f"{url}version/"

        auth_user(client, initial_users["user_1"])

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "selection_1",
                        "field_type": MetadataFieldType.SELECTION,
                        "value": "Option 1",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "selection_2",
                        "field_type": MetadataFieldType.SELECTION,
                        "value": "Option 1",
                        "config": {
                            "key": "value",
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "selection_3",
                        "field_type": MetadataFieldType.SELECTION,
                        "value": "Option 1",
                        "config": {
                            "options": "Option 1",
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "selection_4",
                        "field_type": MetadataFieldType.SELECTION,
                        "value": "Option 1",
                        "config": {
                            "options": [
                                "Option 1",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 1

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "selection_5",
                        "field_type": MetadataFieldType.SELECTION,
                        "value": "  Option 2  ",
                        "config": {
                            "options": [
                                "Option 1",
                                "  Option 2  ",
                                "Option 3",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 2

        url = reverse(
            "uploads-version-detail",
            kwargs={
                "pk": response.data["pk"],
            },
        )
        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK

        assert len(response.data["metadata"]) == 1
        assert response.data["metadata"][0]["value"] == "Option 2"
        assert response.data["metadata"][0]["config"] is not None
        assert "options" in response.data["metadata"][0]["config"]
        assert len(response.data["metadata"][0]["config"]["options"]) == 3
        assert all(
            option
            in [
                "Option 1",
                "Option 2",
                "Option 3",
            ]
            for option in response.data["metadata"][0]["config"]["options"]
        )

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "selection_6",
                        "field_type": MetadataFieldType.SELECTION,
                        "value": "",
                        "config": {
                            "options": [
                                "",
                                "Option 1",
                                "Option 2",
                                "Option 3",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 2

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "selection_7",
                        "field_type": MetadataFieldType.SELECTION,
                        "value": None,
                        "config": {
                            "options": [
                                "",
                                "Option 1",
                                "Option 2",
                                "Option 3",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 2

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "selection_8",
                        "field_type": MetadataFieldType.SELECTION,
                        "value": 1,
                        "config": {
                            "options": [
                                "1",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 3

    def test_validate_multi_selection(self, client, initial_users):
        """
        Ensure we get a correct validation of a multi selection value.
        """
        set_request_for_user(initial_users["user_1"])

        uploads_dataset_1 = UploadsDataset.objects.create()

        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        UploadsVersion.objects.create(
            dataset=uploads_dataset_1,
            version_file=uploads_version_file,
        )

        assert Metadata.objects.count() == 0

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": uploads_dataset_1.pk,
            },
        )
        action_url = f"{url}version/"

        auth_user(client, initial_users["user_1"])

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "multi_selection_1",
                        "field_type": MetadataFieldType.MULTI_SELECTION,
                        "value": [
                            "Option 1",
                        ],
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "multi_selection_2",
                        "field_type": MetadataFieldType.MULTI_SELECTION,
                        "value": [
                            "Option 1",
                        ],
                        "config": {
                            "key": "value",
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "multi_selection_3",
                        "field_type": MetadataFieldType.MULTI_SELECTION,
                        "value": [
                            "Option 1",
                        ],
                        "config": {
                            "options": "Option 1",
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "multi_selection_4",
                        "field_type": MetadataFieldType.MULTI_SELECTION,
                        "value": [
                            "Option 1",
                        ],
                        "config": {
                            "options": [
                                "Option 1",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 1

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "multi_selection_5",
                        "field_type": MetadataFieldType.MULTI_SELECTION,
                        "value": [
                            "Option 1",
                            "Option 3",
                        ],
                        "config": {
                            "options": [
                                "Option 1",
                                "Option 2",
                                "Option 3",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 2

        url = reverse(
            "uploads-version-detail",
            kwargs={
                "pk": response.data["pk"],
            },
        )
        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK

        assert len(response.data["metadata"]) == 1
        assert response.data["metadata"][0]["value"] == [
            "Option 1",
            "Option 3",
        ]
        assert response.data["metadata"][0]["config"] is not None
        assert "options" in response.data["metadata"][0]["config"]
        assert len(response.data["metadata"][0]["config"]["options"]) == 3
        assert all(
            option
            in [
                "Option 1",
                "Option 2",
                "Option 3",
            ]
            for option in response.data["metadata"][0]["config"]["options"]
        )

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "multi_selection_6",
                        "field_type": MetadataFieldType.MULTI_SELECTION,
                        "value": [
                            "Option 4",
                        ],
                        "config": {
                            "options": [
                                "Option 1",
                                "Option 2",
                                "Option 3",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 2

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "multi_selection_7",
                        "field_type": MetadataFieldType.MULTI_SELECTION,
                        "value": [],
                        "config": {
                            "options": [
                                "Option 1",
                                "Option 2",
                                "Option 3",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 3

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "multi_selection_8",
                        "field_type": MetadataFieldType.MULTI_SELECTION,
                        "value": None,
                        "config": {
                            "options": [
                                "Option 1",
                                "Option 2",
                                "Option 3",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 4

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "multi_selection_9",
                        "field_type": MetadataFieldType.MULTI_SELECTION,
                        "value": "",
                        "config": {
                            "options": [
                                "Option 1",
                                "Option 2",
                                "Option 3",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 4

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "multi_selection_10",
                        "field_type": MetadataFieldType.MULTI_SELECTION,
                        "value": 1,
                        "config": {
                            "options": [
                                "1",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 5
        assert response.data["metadata"][0]["value"] == [
            "1",
        ]

    def test_validate_tag(self, client, initial_users):
        """
        Ensure we get a correct validation of a tag value.
        """
        set_request_for_user(initial_users["user_1"])

        uploads_dataset_1 = UploadsDataset.objects.create()

        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        UploadsVersion.objects.create(
            dataset=uploads_dataset_1,
            version_file=uploads_version_file,
        )

        assert Metadata.objects.count() == 0

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": uploads_dataset_1.pk,
            },
        )
        action_url = f"{url}version/"

        auth_user(client, initial_users["user_1"])

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "tag_1",
                        "field_type": MetadataFieldType.TAG,
                        "value": [
                            "Tag 1",
                        ],
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "tag_2",
                        "field_type": MetadataFieldType.TAG,
                        "value": [
                            "Tag 1",
                        ],
                        "config": {
                            "key": "value",
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "tag_3",
                        "field_type": MetadataFieldType.TAG,
                        "value": [
                            "Tag 1",
                        ],
                        "config": {
                            "options": "Tag 1",
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 0

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "tag_4",
                        "field_type": MetadataFieldType.TAG,
                        "value": [
                            "Tag 1",
                        ],
                        "config": {
                            "options": [
                                "Tag 1",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 1

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "tag_5",
                        "field_type": MetadataFieldType.TAG,
                        "value": [
                            "Tag 1",
                            "Tag 3",
                        ],
                        "config": {
                            "options": [
                                "Tag 1",
                                "Tag 2",
                                "Tag 3",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 2

        url = reverse(
            "uploads-version-detail",
            kwargs={
                "pk": response.data["pk"],
            },
        )
        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK

        assert len(response.data["metadata"]) == 1
        assert response.data["metadata"][0]["value"] == [
            "Tag 1",
            "Tag 3",
        ]
        assert response.data["metadata"][0]["config"] is not None
        assert "options" in response.data["metadata"][0]["config"]
        assert len(response.data["metadata"][0]["config"]["options"]) == 3
        assert all(
            option
            in [
                "Tag 1",
                "Tag 2",
                "Tag 3",
            ]
            for option in response.data["metadata"][0]["config"]["options"]
        )

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "tag_6",
                        "field_type": MetadataFieldType.TAG,
                        "value": [
                            "Tag 4",
                        ],
                        "config": {
                            "options": [
                                "Tag 1",
                                "Tag 2",
                                "Tag 3",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 2

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "tag_7",
                        "field_type": MetadataFieldType.TAG,
                        "value": [],
                        "config": {
                            "options": [
                                "Tag 1",
                                "Tag 2",
                                "Tag 3",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 3

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "tag_8",
                        "field_type": MetadataFieldType.TAG,
                        "value": None,
                        "config": {
                            "options": [
                                "Tag 1",
                                "Tag 2",
                                "Tag 3",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 4

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "tag_9",
                        "field_type": MetadataFieldType.TAG,
                        "value": "",
                        "config": {
                            "options": [
                                "Tag 1",
                                "Tag 2",
                                "Tag 3",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 4

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "tag_10",
                        "field_type": MetadataFieldType.TAG,
                        "value": 1,
                        "config": {
                            "options": [
                                "1",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 5
        assert response.data["metadata"][0]["value"] == [
            "1",
        ]

    def test_metadata_template_field_validate_wysiwyg(self, client, initial_users):
        """
        Ensure we get a correct validation of a selection value.
        """
        set_request_for_user(initial_users["user_1"])

        uploads_dataset_1 = UploadsDataset.objects.create()

        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        UploadsVersion.objects.create(
            dataset=uploads_dataset_1,
            version_file=uploads_version_file,
        )

        assert Metadata.objects.count() == 0

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": uploads_dataset_1.pk,
            },
        )
        action_url = f"{url}version/"

        auth_user(client, initial_users["user_1"])

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "wysiwyg_1",
                        "field_type": MetadataFieldType.WYSIWYG,
                        "value": {
                            "paragraph": "Text",
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 1

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "wysiwyg_2",
                        "field_type": MetadataFieldType.WYSIWYG,
                        "value": {},
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 2

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "wysiwyg_3",
                        "field_type": MetadataFieldType.WYSIWYG,
                        "value": "Text",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Metadata.objects.count() == 2

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "wysiwyg_4",
                        "field_type": MetadataFieldType.WYSIWYG,
                        "value": None,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 3

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "wysiwyg_5",
                        "field_type": MetadataFieldType.WYSIWYG,
                        "value": "",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Metadata.objects.count() == 4

    def test_validate_field_types(self, client, initial_users):
        """
        Ensure we get the correct typing of metadata values.
        """
        set_request_for_user(initial_users["user_1"])

        uploads_dataset_1 = UploadsDataset.objects.create()

        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        UploadsVersion.objects.create(
            dataset=uploads_dataset_1,
            version_file=uploads_version_file,
        )

        assert Metadata.objects.count() == 0

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": uploads_dataset_1.pk,
            },
        )
        action_url = f"{url}version/"

        auth_user(client, initial_users["user_1"])

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "integer_1",
                        "field_type": MetadataFieldType.INTEGER,
                        "value": 1337,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["metadata"][0]["custom_key"] == "integer_1"
        assert response.data["metadata"][0]["field_type"] == MetadataFieldType.INTEGER
        assert response.data["metadata"][0]["value"] == "1337"
        assert response.data["metadata"][0]["config"] == {}

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "decimal_1",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": 13.37,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["metadata"][0]["custom_key"] == "decimal_1"
        assert response.data["metadata"][0]["field_type"] == MetadataFieldType.DECIMAL
        assert response.data["metadata"][0]["value"] == "13.37"
        assert response.data["metadata"][0]["config"] == {}

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2025-02-18 13:37:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["metadata"][0]["custom_key"] == "datetime_1"
        assert response.data["metadata"][0]["field_type"] == MetadataFieldType.DATETIME
        assert response.data["metadata"][0]["value"] == "2025-02-18 13:37:00"
        assert response.data["metadata"][0]["config"] == {}

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "date_1",
                        "field_type": MetadataFieldType.DATE,
                        "value": "2025-02-18",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["metadata"][0]["custom_key"] == "date_1"
        assert response.data["metadata"][0]["field_type"] == MetadataFieldType.DATE
        assert response.data["metadata"][0]["value"] == "2025-02-18"
        assert response.data["metadata"][0]["config"] == {}

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "time_1",
                        "field_type": MetadataFieldType.TIME,
                        "value": "13:37:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["metadata"][0]["custom_key"] == "time_1"
        assert response.data["metadata"][0]["field_type"] == MetadataFieldType.TIME
        assert response.data["metadata"][0]["value"] == "13:37:00"
        assert response.data["metadata"][0]["config"] == {}

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "text_1",
                        "field_type": MetadataFieldType.TEXT,
                        "value": "Text",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["metadata"][0]["custom_key"] == "text_1"
        assert response.data["metadata"][0]["field_type"] == MetadataFieldType.TEXT
        assert response.data["metadata"][0]["value"] == "Text"
        assert response.data["metadata"][0]["config"] == {}

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "wysiwyg_1",
                        "field_type": MetadataFieldType.WYSIWYG,
                        "value": {
                            "text": "1337",
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["metadata"][0]["custom_key"] == "wysiwyg_1"
        assert response.data["metadata"][0]["field_type"] == MetadataFieldType.WYSIWYG
        assert response.data["metadata"][0]["value"] == {
            "text": "1337",
        }
        assert response.data["metadata"][0]["config"] == {}

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "selection_1",
                        "field_type": MetadataFieldType.SELECTION,
                        "value": "Option 1",
                        "config": {
                            "options": [
                                "Option 1",
                                "Option 2",
                                "Option 3",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["metadata"][0]["custom_key"] == "selection_1"
        assert response.data["metadata"][0]["field_type"] == MetadataFieldType.SELECTION
        assert response.data["metadata"][0]["value"] == "Option 1"
        assert response.data["metadata"][0]["config"] == {
            "options": [
                "Option 1",
                "Option 2",
                "Option 3",
            ],
        }

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "tag_1",
                        "field_type": MetadataFieldType.TAG,
                        "value": [
                            "Tag 1",
                            "Tag 3",
                        ],
                        "config": {
                            "options": [
                                "Tag 1",
                                "Tag 2",
                                "Tag 3",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["metadata"][0]["custom_key"] == "tag_1"
        assert response.data["metadata"][0]["field_type"] == MetadataFieldType.TAG
        assert response.data["metadata"][0]["value"] == [
            "Tag 1",
            "Tag 3",
        ]
        assert response.data["metadata"][0]["config"] == {
            "options": [
                "Tag 1",
                "Tag 2",
                "Tag 3",
            ],
        }

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "multi_selection_1",
                        "field_type": MetadataFieldType.MULTI_SELECTION,
                        "value": [
                            "Option 1",
                            "Option 3",
                        ],
                        "config": {
                            "options": [
                                "Option 1",
                                "Option 2",
                                "Option 3",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["metadata"][0]["custom_key"] == "multi_selection_1"
        assert response.data["metadata"][0]["field_type"] == MetadataFieldType.MULTI_SELECTION
        assert response.data["metadata"][0]["value"] == [
            "Option 1",
            "Option 3",
        ]
        assert response.data["metadata"][0]["config"] == {
            "options": [
                "Option 1",
                "Option 2",
                "Option 3",
            ],
        }


@pytest.mark.django_db
class TestMetadataTemplateContainerAPI:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.project_1 = Project.objects.create(
            name="Project 1",
        )

        self.metadata_template_container_1 = MetadataTemplateContainer.objects.create(
            name="Metadata template container 1",
            assigned_to_content_type=self.project_1.get_content_type(),
            assigned_to_object_id=self.project_1.pk,
        )

    def test_read_metadata_template_containers_list(self, client):
        """
        Ensure we can read the metadata template containers list.
        """
        url = reverse("metadata-template-container-list")

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

    def test_read_metadata_template_container_details(self, client):
        """
        Ensure we can read the metadata template container details.
        """
        url = reverse(
            "metadata-template-container-detail",
            kwargs={
                "pk": self.metadata_template_container_1.pk,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["pk"] == str(self.metadata_template_container_1.pk)

    def test_create_metadata_template_container(self, client):
        """
        Ensure we can create a new metadata template container.
        """
        url = reverse("metadata-template-container-list")

        response = client.post(
            url,
            {
                "name": "Metadata template container 2",
                "assigned_to_content_type": get_content_type_for_object(self.project_1.get_content_type()),
                "assigned_to_object_id": self.project_1.pk,
                "metadata_template_fields": [
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
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Metadata template container 2"
        assert response.data["assigned_to_content_type"] == get_content_type_for_object(
            self.project_1.get_content_type(),
        )
        assert response.data["assigned_to_object_id"] == str(self.project_1.pk)
        assert response.data["project"]["pk"] == str(self.project_1.pk)
        assert response.data["project"]["name"] == self.project_1.name
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template__metadata_template_container=response.data["pk"],
            ).count()
            == 2
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 2

    def test_edit_metadata_template_container_as_project_admin(self, client, initial_users):
        """
        Ensure we can edit a metadata template container with project admin permissions only.
        """
        auth_user(client, initial_users["user_2"])

        url = reverse(
            "metadata-template-container-detail",
            kwargs={
                "pk": self.metadata_template_container_1.pk,
            },
        )

        response = client.patch(
            url,
            {
                "name": "Name edited",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

        set_request_for_user(initial_users["user_1"])

        project_membership = ProjectMembership.objects.create(
            project=self.project_1,
            member=initial_users["user_2"],
            is_project_admin=False,
            can_create_folders=False,
        )

        auth_user(client, initial_users["user_2"])

        response = client.patch(
            url,
            {
                "name": "Name edited",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        set_request_for_user(initial_users["user_1"])

        project_membership.is_project_admin = True
        project_membership.save()

        auth_user(client, initial_users["user_2"])

        response = client.patch(
            url,
            {
                "name": "Name edited",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Name edited"

    def test_create_and_edit_metadata_template_containers_with_content_type(self, client, initial_users):
        """
        Ensure we can create a new metadata template container with a specific content type.
        """
        url = reverse("metadata-template-container-list")

        response = client.post(
            url,
            {
                "name": "Metadata template container 2",
                "assigned_to_content_type": get_content_type_for_object(self.project_1.get_content_type()),
                "assigned_to_object_id": self.project_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        metadata_template_container_2 = response.data

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 2

        detail_url = reverse(
            "metadata-template-container-detail",
            kwargs={
                "pk": metadata_template_container_2["pk"],
            },
        )

        response = client.patch(
            detail_url,
            {
                "name": "Metadata template container 2 edited",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Metadata template container 2 edited"

        response = client.post(
            url,
            {
                "name": "Metadata template container 3",
                "assigned_to_content_type": get_content_type_for_object(
                    self.project_1.folders.first().get_content_type(),
                ),
                "assigned_to_object_id": self.project_1.folders.first().pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        metadata_template_container_3 = response.data

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 3

        auth_user(client, initial_users["user_2"])

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0

        detail_url = reverse(
            "metadata-template-container-detail",
            kwargs={
                "pk": self.metadata_template_container_1.pk,
            },
        )

        response = client.patch(
            detail_url,
            {
                "name": "Metadata template container edited",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

        detail_url = reverse(
            "metadata-template-container-detail",
            kwargs={
                "pk": metadata_template_container_2["pk"],
            },
        )

        response = client.patch(
            detail_url,
            {
                "name": "Metadata template container 2 edited",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

        detail_url = reverse(
            "metadata-template-container-detail",
            kwargs={
                "pk": metadata_template_container_3["pk"],
            },
        )

        response = client.patch(
            detail_url,
            {
                "name": "Metadata template container 3 edited",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

        response = client.post(
            url,
            {
                "name": "Metadata template container 4",
                "assigned_to_content_type": get_content_type_for_object(self.project_1.get_content_type()),
                "assigned_to_object_id": self.project_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0

        response = client.post(
            url,
            {
                "name": "Metadata template container 5",
                "assigned_to_content_type": get_content_type_for_object(
                    self.project_1.folders.first().get_content_type(),
                ),
                "assigned_to_object_id": self.project_1.folders.first().pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0

        project_membership = ProjectMembership.objects.create(
            project=self.project_1,
            member=initial_users["user_2"],
            is_project_admin=False,
            can_create_folders=True,
        )
        assert project_membership.is_metadata_template_admin is False

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 2

        folder_permission = FolderPermission.objects.create(
            folder=self.project_1.folders.first(),
            project_membership=project_membership,
        )
        assert folder_permission.is_metadata_template_admin is False

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 3

        project_membership.is_metadata_template_admin = True
        project_membership.save()

        project_membership.refresh_from_db()
        assert project_membership.is_metadata_template_admin is True

        folder_permission.refresh_from_db()
        assert folder_permission.is_metadata_template_admin is True

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 3

        auth_user(client, initial_users["user_1"])

        response = client.post(
            url,
            {
                "name": "Metadata template container 4",
                "assigned_to_content_type": None,
                "assigned_to_object_id": None,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        metadata_template_container_4 = response.data

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 4

        auth_user(client, initial_users["user_2"])

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 4

        response = client.post(
            url,
            {
                "name": "Metadata template container 5",
                "assigned_to_content_type": None,
                "assigned_to_object_id": None,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 4

        initial_users["user_2"].is_global_metadata_template_admin = True
        initial_users["user_2"].save()

        response = client.post(
            url,
            {
                "name": "Metadata template container 5",
                "assigned_to_content_type": None,
                "assigned_to_object_id": None,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        metadata_template_container_5 = response.data

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 5

        detail_url = reverse(
            "metadata-template-container-detail",
            kwargs={
                "pk": self.metadata_template_container_1.pk,
            },
        )

        response = client.patch(
            detail_url,
            {
                "name": "Metadata template container edited",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Metadata template container edited"

        detail_url = reverse(
            "metadata-template-container-detail",
            kwargs={
                "pk": metadata_template_container_2["pk"],
            },
        )

        response = client.patch(
            detail_url,
            {
                "name": "Metadata template container 2 2nd edit",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Metadata template container 2 2nd edit"

        detail_url = reverse(
            "metadata-template-container-detail",
            kwargs={
                "pk": metadata_template_container_3["pk"],
            },
        )

        response = client.patch(
            detail_url,
            {
                "name": "Metadata template container 3 edited",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Metadata template container 3 edited"

        detail_url = reverse(
            "metadata-template-container-detail",
            kwargs={
                "pk": metadata_template_container_4["pk"],
            },
        )

        response = client.patch(
            detail_url,
            {
                "name": "Metadata template container 4 edited",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Metadata template container 4 edited"

        detail_url = reverse(
            "metadata-template-container-detail",
            kwargs={
                "pk": metadata_template_container_5["pk"],
            },
        )

        response = client.patch(
            detail_url,
            {
                "name": "Metadata template container 5 edited",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Metadata template container 5 edited"

    def test_create_metadata_template_container_with_metadata_template_fields(self, client):
        """
        Ensure we can create a new metadata template container and template fields with a single request.
        """
        url = reverse("metadata-template-container-list")

        response = client.post(
            url,
            {
                "name": "Metadata template container 2",
                "metadata_template_fields": [
                    {
                        "custom_key": "custom_key_1",
                        "value": "custom value",
                        "mandatory": True,
                    },
                    {
                        "custom_key": "custom_key_2",
                        "field_type": MetadataFieldType.DECIMAL,
                        "mandatory": False,
                    },
                    {
                        "custom_key": "custom_key_3",
                        "field_type": MetadataFieldType.SELECTION,
                        "value": "Option 2",
                        "config": {
                            "options": [
                                "Option 1",
                                "Option 2",
                                "Option 3",
                            ],
                        },
                        "mandatory": False,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 3
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(custom_key="custom_key_1")
        assert metadata_template_field_1.custom_key == "custom_key_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.TEXT
        assert metadata_template_field_1.get_value() == "custom value"
        assert metadata_template_field_1.mandatory is True

        metadata_template_field_2 = MetadataTemplateField.objects.get(custom_key="custom_key_2")
        assert metadata_template_field_2.custom_key == "custom_key_2"
        assert metadata_template_field_2.field_type == MetadataFieldType.DECIMAL
        assert metadata_template_field_2.get_value() is None
        assert metadata_template_field_2.mandatory is False

        metadata_template_field_3 = MetadataTemplateField.objects.get(custom_key="custom_key_3")
        assert metadata_template_field_3.custom_key == "custom_key_3"
        assert metadata_template_field_3.field_type == MetadataFieldType.SELECTION
        assert metadata_template_field_3.get_value() == "Option 2"
        assert metadata_template_field_3.mandatory is False
        assert metadata_template_field_3.config is not None
        assert "options" in metadata_template_field_3.config
        assert len(metadata_template_field_3.config["options"]) == 3
        assert all(
            option
            in [
                "Option 1",
                "Option 2",
                "Option 3",
            ]
            for option in metadata_template_field_3.config["options"]
        )

    def test_change_metadata_template_container_with_metadata_template_fields(self, client):
        """
        Ensure we can update a metadata template container and not replace template fields with a single request.
        """
        url = reverse("metadata-template-container-list")

        response = client.post(
            url,
            {
                "name": "Metadata template container 2",
                "metadata_template_fields": [
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
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        metadata_template_container = response.data
        latest_metadata_template = metadata_template_container["latest_metadata_template"]

        assert MetadataTemplateField.objects.filter(metadata_template=latest_metadata_template["pk"]).count() == 2

        metadata_template_field_1 = MetadataTemplateField.objects.get(custom_key="custom_key_1")
        assert metadata_template_field_1.custom_key == "custom_key_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.TEXT
        assert metadata_template_field_1.get_value() == "custom value"
        assert metadata_template_field_1.mandatory is True

        metadata_template_field_2 = MetadataTemplateField.objects.get(custom_key="custom_key_2")
        assert metadata_template_field_2.custom_key == "custom_key_2"
        assert metadata_template_field_2.field_type == MetadataFieldType.TEXT
        assert metadata_template_field_2.get_value() is None
        assert metadata_template_field_2.mandatory is False

        url = reverse(
            "metadata-template-container-detail",
            kwargs={
                "pk": metadata_template_container["pk"],
            },
        )

        name = "Metadata template container 2 edit 1"
        response = client.patch(
            url,
            {
                "name": name,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == name
        assert MetadataTemplateField.objects.filter(metadata_template=latest_metadata_template["pk"]).count() == 2

        metadata_template_field_1 = MetadataTemplateField.objects.get(custom_key="custom_key_1")
        assert metadata_template_field_1.custom_key == "custom_key_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.TEXT
        assert metadata_template_field_1.get_value() == "custom value"
        assert metadata_template_field_1.mandatory is True

        metadata_template_field_2 = MetadataTemplateField.objects.get(custom_key="custom_key_2")
        assert metadata_template_field_2.custom_key == "custom_key_2"
        assert metadata_template_field_2.field_type == MetadataFieldType.TEXT
        assert metadata_template_field_2.get_value() is None
        assert metadata_template_field_2.mandatory is False

        name = "Metadata template container 2 edit 2"
        response = client.patch(
            url,
            {
                "name": name,
                "metadata_template_fields": [],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == name
        assert MetadataTemplateField.objects.filter(metadata_template=latest_metadata_template["pk"]).count() == 2

        metadata_template_field_1 = MetadataTemplateField.objects.get(custom_key="custom_key_1")
        assert metadata_template_field_1.custom_key == "custom_key_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.TEXT
        assert metadata_template_field_1.get_value() == "custom value"
        assert metadata_template_field_1.mandatory is True

        metadata_template_field_2 = MetadataTemplateField.objects.get(custom_key="custom_key_2")
        assert metadata_template_field_2.custom_key == "custom_key_2"
        assert metadata_template_field_2.field_type == MetadataFieldType.TEXT
        assert metadata_template_field_2.get_value() is None
        assert metadata_template_field_2.mandatory is False

        name = "Metadata template container 2 edit 3"
        response = client.patch(
            url,
            {
                "name": name,
                "metadata_template_fields": [
                    {
                        "custom_key": "custom_key_1",
                        "value": "custom value",
                        "mandatory": True,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == name
        assert MetadataTemplateField.objects.filter(metadata_template=latest_metadata_template["pk"]).count() == 2

        metadata_template_field_1 = MetadataTemplateField.objects.get(custom_key="custom_key_1")
        assert metadata_template_field_1.custom_key == "custom_key_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.TEXT
        assert metadata_template_field_1.get_value() == "custom value"
        assert metadata_template_field_1.mandatory is True

        metadata_template_field_2 = MetadataTemplateField.objects.get(custom_key="custom_key_2")
        assert metadata_template_field_2.custom_key == "custom_key_2"
        assert metadata_template_field_2.field_type == MetadataFieldType.TEXT
        assert metadata_template_field_2.get_value() is None
        assert metadata_template_field_2.mandatory is False

        response = client.patch(
            url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == name
        assert MetadataTemplateField.objects.filter(metadata_template=latest_metadata_template["pk"]).count() == 2

        metadata_template_field_1 = MetadataTemplateField.objects.get(custom_key="custom_key_1")
        assert metadata_template_field_1.custom_key == "custom_key_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.TEXT
        assert metadata_template_field_1.get_value() == "custom value"
        assert metadata_template_field_1.mandatory is True

        metadata_template_field_2 = MetadataTemplateField.objects.get(custom_key="custom_key_2")
        assert metadata_template_field_2.custom_key == "custom_key_2"
        assert metadata_template_field_2.field_type == MetadataFieldType.TEXT
        assert metadata_template_field_2.get_value() is None
        assert metadata_template_field_2.mandatory is False

    def test_metadata_template_container_assigned_to_content_object_name(self, client, initial_users):
        """
        Ensure we get the correct name of the assigned content object.
        """
        url = reverse("metadata-template-container-list")

        response = client.post(
            url,
            {
                "name": "Metadata template container 2",
                "assigned_to_content_type": get_content_type_for_object(self.project_1.get_content_type()),
                "assigned_to_object_id": self.project_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["assigned_to_content_object_name"] == self.project_1.name

        response = client.post(
            url,
            {
                "name": "Metadata template container 3",
                "assigned_to_content_type": get_content_type_for_object(
                    self.project_1.folders.first().get_content_type(),
                ),
                "assigned_to_object_id": self.project_1.folders.first().pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["assigned_to_content_object_name"] == self.project_1.folders.first().name

        response = client.post(
            url,
            {
                "name": "Metadata template container 4",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["assigned_to_content_object_name"] is None

    def test_metadata_template_container_version_action(self, client, initial_users):
        """
        Ensure we can create a new metadata template version by making a POST request to the respective metadata
        template container action endpoint.
        """
        metadata_template_container_2 = MetadataTemplateContainer.objects.create(
            name="Metadata template container 2",
        )

        metadata_template_2 = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_2,
        )

        metadata_template_field_1 = MetadataTemplateField.objects.create(
            custom_key="custom_key_1",
            metadata_template=metadata_template_2,
        )

        url = reverse(
            "metadata-template-container-detail",
            kwargs={
                "pk": self.metadata_template_container_1.pk,
            },
        )
        action_url = f"{url}version/"

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["metadata_templates"]) == 0

        response = client.post(action_url, format="json")
        assert response.status_code == status.HTTP_403_FORBIDDEN

        MetadataTemplate.objects.create(
            metadata_template_container=self.metadata_template_container_1,
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["metadata_templates"]) == 1

        response = client.post(
            action_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] is None
        assert response.data["metadata_template_container"]["pk"] == str(self.metadata_template_container_1.pk)

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["metadata_templates"]) == 2

        response = client.post(
            action_url,
            {
                "name": "Specific version name",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Specific version name"
        assert response.data["metadata_template_container"]["pk"] == str(self.metadata_template_container_1.pk)

        fields_url = reverse("metadata-template-field-list")
        url_filter = f"{fields_url}?metadata_template={response.data['pk']}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["metadata_templates"]) == 3

        response = client.post(
            action_url,
            {
                "name": "Another specific version name",
                "metadata_template_fields": [
                    {
                        "custom_key": "custom_key_1",
                        "value": "custom value",
                        "mandatory": True,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Another specific version name"
        assert response.data["metadata_template_container"]["pk"] == str(self.metadata_template_container_1.pk)

        fields_url = reverse("metadata-template-field-list")
        url_filter = f"{fields_url}?metadata_template={response.data['pk']}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["metadata_templates"]) == 4

        response = client.post(
            action_url,
            {
                "name": "Named version 3",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Named version 3"
        assert response.data["metadata_template_container"]["pk"] == str(self.metadata_template_container_1.pk)

        fields_url = reverse("metadata-template-field-list")
        url_filter = f"{fields_url}?metadata_template={response.data['pk']}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["metadata_templates"]) == 5

        response = client.post(
            action_url,
            {
                "name": "v4",
                "metadata_template_fields": [
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
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "v4"
        assert response.data["metadata_template_container"]["pk"] == str(self.metadata_template_container_1.pk)

        previous_metadata_template = response.data

        fields_url = reverse("metadata-template-field-list")
        url_filter = f"{fields_url}?metadata_template={response.data['pk']}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 2

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["metadata_templates"]) == 6

        auth_user(client, initial_users["user_2"])

        response = client.post(
            action_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

        auth_user(client, initial_users["user_1"])

        previous_metadata_template_fields = MetadataTemplateField.objects.filter(
            metadata_template=previous_metadata_template["pk"],
        )

        response = client.post(
            action_url,
            {
                "name": "v5",
                "metadata_template_fields": [
                    {
                        "custom_key": "custom_key_1",
                        "value": "custom value",
                        "mandatory": True,
                        "previous_version_field": previous_metadata_template_fields.get(
                            custom_key="custom_key_1",
                        ).pk,
                    },
                    {
                        "custom_key": "custom_key_2",
                        "mandatory": False,
                        "previous_version_field": previous_metadata_template_fields.get(
                            custom_key="custom_key_2",
                        ).pk,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "v5"
        assert response.data["metadata_template_container"]["pk"] == str(self.metadata_template_container_1.pk)

        current_metadata_template = response.data

        current_metadata_template_fields = MetadataTemplateField.objects.filter(
            metadata_template=current_metadata_template["pk"],
        )

        assert current_metadata_template_fields.get(
            custom_key="custom_key_1",
        ).previous_version_field == previous_metadata_template_fields.get(
            custom_key="custom_key_1",
        )
        assert current_metadata_template_fields.get(
            custom_key="custom_key_2",
        ).previous_version_field == previous_metadata_template_fields.get(
            custom_key="custom_key_2",
        )

        response = client.post(
            action_url,
            {
                "name": "v6",
                "metadata_template_fields": [
                    {
                        "custom_key": "custom_key_1",
                        "value": "custom value",
                        "mandatory": True,
                        "previous_version_field": metadata_template_field_1.pk,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        auth_user(client, initial_users["user_2"])

        url = reverse(
            "metadata-template-container-detail",
            kwargs={
                "pk": metadata_template_container_2.pk,
            },
        )
        action_url = f"{url}version/"

        response = client.post(
            action_url,
            {
                "name": "New version from another user",
                "metadata_template_fields": [
                    {
                        "custom_key": "custom_key_1",
                        "value": "custom value",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        url = reverse(
            "metadata-template-container-detail",
            kwargs={
                "pk": self.metadata_template_container_1.pk,
            },
        )
        action_url = f"{url}version/"

        response = client.post(
            action_url,
            {
                "name": "New version from another user",
                "metadata_template_fields": [
                    {
                        "custom_key": "custom_key_1",
                        "value": "custom value",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

        project_membership_user_2 = ProjectMembership.objects.create(
            project=self.project_1,
            member=initial_users["user_2"],
            is_project_admin=False,
            is_metadata_template_admin=False,
            can_create_folders=False,
        )

        response = client.post(
            action_url,
            {
                "name": "New version from another user",
                "metadata_template_fields": [
                    {
                        "custom_key": "custom_key_1",
                        "value": "custom value",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        project_membership_user_2.is_project_admin = True
        project_membership_user_2.is_metadata_template_admin = True
        project_membership_user_2.save()

        response = client.post(
            action_url,
            {
                "name": "New version from another user",
                "metadata_template_fields": [
                    {
                        "custom_key": "custom_key_1",
                        "value": "custom value",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

    def test_metadata_template_container_version_action_with_metadata_ordering(self, client, initial_users):
        """
        Ensure we can create a new metadata template version by making a POST request to the respective metadata
        template container action endpoint and that the metadata ordering is respected.
        """
        url = reverse(
            "metadata-template-container-detail",
            kwargs={
                "pk": self.metadata_template_container_1.pk,
            },
        )
        action_url = f"{url}version/"

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["metadata_templates"]) == 0

        response = client.post(action_url, format="json")
        assert response.status_code == status.HTTP_403_FORBIDDEN

        MetadataTemplate.objects.create(
            metadata_template_container=self.metadata_template_container_1,
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["metadata_templates"]) == 1

        response = client.post(
            action_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["metadata_template_container"]["pk"] == str(self.metadata_template_container_1.pk)

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["metadata_templates"]) == 2

        response = client.post(
            action_url,
            {
                "metadata_template_fields": [
                    {
                        "custom_key": "custom_key_1",
                        "value": "custom value",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        fields_url = reverse("metadata-template-field-list")
        url_filter = f"{fields_url}?metadata_template={response.data['pk']}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1
        assert response.data["results"][0]["order"] == 0
        assert response.data["results"][0]["custom_key"] == "custom_key_1"

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["metadata_templates"]) == 3

        response = client.post(
            action_url,
            {
                "metadata_template_fields": [
                    {
                        "custom_key": "custom_key_1",
                        "value": "custom value 1",
                        "order": 1,
                    },
                    {
                        "custom_key": "custom_key_3",
                        "value": "custom value 3",
                        "order": 3,
                    },
                    {
                        "custom_key": "custom_key_2",
                        "value": "custom value 2",
                        "order": 2,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        fields_url = reverse("metadata-template-field-list")
        url_filter = f"{fields_url}?metadata_template={response.data['pk']}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 3
        assert response.data["results"][0]["order"] == 1
        assert response.data["results"][0]["custom_key"] == "custom_key_1"
        assert response.data["results"][1]["order"] == 2
        assert response.data["results"][1]["custom_key"] == "custom_key_2"
        assert response.data["results"][2]["order"] == 3
        assert response.data["results"][2]["custom_key"] == "custom_key_3"

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["metadata_templates"]) == 4

        response = client.post(
            action_url,
            {
                "metadata_template_fields": [
                    {
                        "custom_key": "custom_key_1",
                        "value": "custom value 1",
                        "order": 5,
                    },
                    {
                        "custom_key": "custom_key_4",
                        "value": "custom value 4",
                    },
                    {
                        "custom_key": "custom_key_3",
                        "value": "custom value 3",
                        "order": 2,
                    },
                    {
                        "custom_key": "custom_key_2",
                        "value": "custom value 2",
                    },
                    {
                        "custom_key": "custom_key_5",
                        "value": "custom value 5",
                        "order": -1,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        fields_url = reverse("metadata-template-field-list")
        url_filter = f"{fields_url}?metadata_template={response.data['pk']}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 5
        assert response.data["results"][0]["order"] == -1
        assert response.data["results"][0]["custom_key"] == "custom_key_5"
        assert response.data["results"][1]["order"] == 0
        assert response.data["results"][1]["custom_key"] == "custom_key_2"
        assert response.data["results"][2]["order"] == 0
        assert response.data["results"][2]["custom_key"] == "custom_key_4"
        assert response.data["results"][3]["order"] == 2
        assert response.data["results"][3]["custom_key"] == "custom_key_3"
        assert response.data["results"][4]["order"] == 5
        assert response.data["results"][4]["custom_key"] == "custom_key_1"

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["metadata_templates"]) == 5

    def test_metadata_template_container_field_validate_integer(self, client):
        """
        Ensure we get a correct validation of an integer value.
        """
        url = reverse("metadata-template-container-list")

        response = client.post(
            url,
            {
                "name": "Metadata template container 2",
                "metadata_template_fields": [
                    {
                        "custom_key": "integer_1",
                        "field_type": MetadataFieldType.INTEGER,
                        "value": 0,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 2
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="integer_1",
        )
        assert metadata_template_field_1.custom_key == "integer_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.INTEGER
        assert metadata_template_field_1.get_value() == "0"

        response = client.post(
            url,
            {
                "name": "Metadata template container 3",
                "metadata_template_fields": [
                    {
                        "custom_key": "integer_1",
                        "field_type": MetadataFieldType.INTEGER,
                        "value": 1337,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 3
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="integer_1",
        )
        assert metadata_template_field_1.custom_key == "integer_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.INTEGER
        assert metadata_template_field_1.get_value() == "1337"

        response = client.post(
            url,
            {
                "name": "Metadata template container 4",
                "metadata_template_fields": [
                    {
                        "custom_key": "integer_1",
                        "field_type": MetadataFieldType.INTEGER,
                        "value": -7331,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 4
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="integer_1",
        )
        assert metadata_template_field_1.custom_key == "integer_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.INTEGER
        assert metadata_template_field_1.get_value() == "-7331"

        response = client.post(
            url,
            {
                "name": "Metadata template container 5",
                "metadata_template_fields": [
                    {
                        "custom_key": "integer_1",
                        "field_type": MetadataFieldType.INTEGER,
                        "value": "0",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 5
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="integer_1",
        )
        assert metadata_template_field_1.custom_key == "integer_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.INTEGER
        assert metadata_template_field_1.get_value() == "0"

        response = client.post(
            url,
            {
                "name": "Metadata template container 6",
                "metadata_template_fields": [
                    {
                        "custom_key": "integer_1",
                        "field_type": MetadataFieldType.INTEGER,
                        "value": "1337",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 6
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="integer_1",
        )
        assert metadata_template_field_1.custom_key == "integer_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.INTEGER
        assert metadata_template_field_1.get_value() == "1337"

        response = client.post(
            url,
            {
                "name": "Metadata template container 7",
                "metadata_template_fields": [
                    {
                        "custom_key": "integer_1",
                        "field_type": MetadataFieldType.INTEGER,
                        "value": "-7331",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 7
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="integer_1",
        )
        assert metadata_template_field_1.custom_key == "integer_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.INTEGER
        assert metadata_template_field_1.get_value() == "-7331"

        response = client.post(
            url,
            {
                "name": "Metadata template container 8",
                "metadata_template_fields": [
                    {
                        "custom_key": "integer_1",
                        "field_type": MetadataFieldType.INTEGER,
                        "value": "Text",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 7

        response = client.post(
            url,
            {
                "name": "Metadata template container 9",
                "metadata_template_fields": [
                    {
                        "custom_key": "integer_1",
                        "field_type": MetadataFieldType.INTEGER,
                        "value": 13.37,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 7

        response = client.post(
            url,
            {
                "name": "Metadata template container 10",
                "metadata_template_fields": [
                    {
                        "custom_key": "integer_1",
                        "field_type": MetadataFieldType.INTEGER,
                        "value": "13.37",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 7

        response = client.post(
            url,
            {
                "name": "Metadata template container 11",
                "metadata_template_fields": [
                    {
                        "custom_key": "integer_1",
                        "field_type": MetadataFieldType.INTEGER,
                        "value": None,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 8
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="integer_1",
        )
        assert metadata_template_field_1.custom_key == "integer_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.INTEGER
        assert metadata_template_field_1.get_value() is None

        response = client.post(
            url,
            {
                "name": "Metadata template container 12",
                "metadata_template_fields": [
                    {
                        "custom_key": "integer_1",
                        "field_type": MetadataFieldType.INTEGER,
                        "value": None,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 9
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="integer_1",
        )
        assert metadata_template_field_1.custom_key == "integer_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.INTEGER
        assert metadata_template_field_1.get_value() is None

    def test_metadata_template_field_validate_decimal(self, client):
        """
        Ensure we get a correct validation of a decimal numeral value.
        """
        url = reverse("metadata-template-container-list")

        response = client.post(
            url,
            {
                "name": "Metadata template container 2",
                "metadata_template_fields": [
                    {
                        "custom_key": "decimal_1",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": 0,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 2
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="decimal_1",
        )
        assert metadata_template_field_1.custom_key == "decimal_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.DECIMAL
        assert metadata_template_field_1.get_value() == "0"

        response = client.post(
            url,
            {
                "name": "Metadata template container 3",
                "metadata_template_fields": [
                    {
                        "custom_key": "decimal_1",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": 1337,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 3
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="decimal_1",
        )
        assert metadata_template_field_1.custom_key == "decimal_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.DECIMAL
        assert metadata_template_field_1.get_value() == "1337"

        response = client.post(
            url,
            {
                "name": "Metadata template container 4",
                "metadata_template_fields": [
                    {
                        "custom_key": "decimal_1",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": -7331,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 4
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="decimal_1",
        )
        assert metadata_template_field_1.custom_key == "decimal_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.DECIMAL
        assert metadata_template_field_1.get_value() == "-7331"

        response = client.post(
            url,
            {
                "name": "Metadata template container 5",
                "metadata_template_fields": [
                    {
                        "custom_key": "decimal_1",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": 0.0,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 5
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="decimal_1",
        )
        assert metadata_template_field_1.custom_key == "decimal_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.DECIMAL
        assert metadata_template_field_1.get_value() == "0.0"

        response = client.post(
            url,
            {
                "name": "Metadata template container 6",
                "metadata_template_fields": [
                    {
                        "custom_key": "decimal_1",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": 13.37,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 6
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="decimal_1",
        )
        assert metadata_template_field_1.custom_key == "decimal_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.DECIMAL
        assert metadata_template_field_1.get_value() == "13.37"

        response = client.post(
            url,
            {
                "name": "Metadata template container 7",
                "metadata_template_fields": [
                    {
                        "custom_key": "decimal_1",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": "0",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 7
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="decimal_1",
        )
        assert metadata_template_field_1.custom_key == "decimal_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.DECIMAL
        assert metadata_template_field_1.get_value() == "0"

        response = client.post(
            url,
            {
                "name": "Metadata template container 8",
                "metadata_template_fields": [
                    {
                        "custom_key": "decimal_1",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": "1337",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 8
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="decimal_1",
        )
        assert metadata_template_field_1.custom_key == "decimal_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.DECIMAL
        assert metadata_template_field_1.get_value() == "1337"

        response = client.post(
            url,
            {
                "name": "Metadata template container 9",
                "metadata_template_fields": [
                    {
                        "custom_key": "decimal_1",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": "-7331",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 9
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="decimal_1",
        )
        assert metadata_template_field_1.custom_key == "decimal_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.DECIMAL
        assert metadata_template_field_1.get_value() == "-7331"

        response = client.post(
            url,
            {
                "name": "Metadata template container 10",
                "metadata_template_fields": [
                    {
                        "custom_key": "decimal_1",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": "0.0",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 10
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="decimal_1",
        )
        assert metadata_template_field_1.custom_key == "decimal_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.DECIMAL
        assert metadata_template_field_1.get_value() == "0.0"

        response = client.post(
            url,
            {
                "name": "Metadata template container 11",
                "metadata_template_fields": [
                    {
                        "custom_key": "decimal_1",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": "13.37",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 11
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="decimal_1",
        )
        assert metadata_template_field_1.custom_key == "decimal_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.DECIMAL
        assert metadata_template_field_1.get_value() == "13.37"

        response = client.post(
            url,
            {
                "name": "Metadata template container 12",
                "metadata_template_fields": [
                    {
                        "custom_key": "decimal_1",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": "1e10",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 12
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="decimal_1",
        )
        assert metadata_template_field_1.custom_key == "decimal_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.DECIMAL
        assert metadata_template_field_1.get_value() == "1e10"

        response = client.post(
            url,
            {
                "name": "Metadata template container 13",
                "metadata_template_fields": [
                    {
                        "custom_key": "decimal_1",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": "Text",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 12

        response = client.post(
            url,
            {
                "name": "Metadata template container 14",
                "metadata_template_fields": [
                    {
                        "custom_key": "decimal_1",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": None,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 13
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="decimal_1",
        )
        assert metadata_template_field_1.custom_key == "decimal_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.DECIMAL
        assert metadata_template_field_1.get_value() is None

        response = client.post(
            url,
            {
                "name": "Metadata template container 15",
                "metadata_template_fields": [
                    {
                        "custom_key": "decimal_1",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": "",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 14
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="decimal_1",
        )
        assert metadata_template_field_1.custom_key == "decimal_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.DECIMAL
        assert metadata_template_field_1.get_value() is None

    def test_metadata_template_field_validate_datetime(self, client):
        """
        Ensure we get a correct validation of a datetime value.
        """
        url = reverse("metadata-template-container-list")

        response = client.post(
            url,
            {
                "name": "Metadata template container 2",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": 1337,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 3",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": 13.37,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 4",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "18-02-2025",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 5",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "02-18-2025",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 6",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "18.02.2025",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 7",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "02.18.2025",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 8",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "18/02/2025",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 9",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "02/18/2025",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 10",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2025-18-02",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 11",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2025-02-18",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 12",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2025.18.02",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 13",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2025.02.18",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 14",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2025/18/02",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 15",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2025/02/18",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 16",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2025 Feb 18",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 17",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "Feb 18 2025",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 18",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2025 February 18",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 19",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "February 18 2025",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 20",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2025-2-18",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 21",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2025-2-2",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 22",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "25-2-2",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 23",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "1900-02-29",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 24",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "1900-02-29 00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 25",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "1900-02-29 00:00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 26",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2000-02-29",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 27",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2000-02-29 00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 28",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2000-02-29 00:00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 2
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="datetime_1",
        )
        assert metadata_template_field_1.custom_key == "datetime_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.DATETIME
        assert metadata_template_field_1.get_value() == "2000-02-29 00:00:00"

        response = client.post(
            url,
            {
                "name": "Metadata template container 29",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2004-02-29",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 2

        response = client.post(
            url,
            {
                "name": "Metadata template container 30",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2004-02-29 00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 2

        response = client.post(
            url,
            {
                "name": "Metadata template container 31",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2004-02-29 00:00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 3
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="datetime_1",
        )
        assert metadata_template_field_1.custom_key == "datetime_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.DATETIME
        assert metadata_template_field_1.get_value() == "2004-02-29 00:00:00"

        response = client.post(
            url,
            {
                "name": "Metadata template container 32",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2025-02-18 20:00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 4
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="datetime_1",
        )
        assert metadata_template_field_1.custom_key == "datetime_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.DATETIME
        assert metadata_template_field_1.get_value() == "2025-02-18 20:00:00"

        response = client.post(
            url,
            {
                "name": "Metadata template container 33",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2025-02-18 24:00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 4

        response = client.post(
            url,
            {
                "name": "Metadata template container 34",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "10:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 4

        response = client.post(
            url,
            {
                "name": "Metadata template container 35",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "10:00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 4

        response = client.post(
            url,
            {
                "name": "Metadata template container 36",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "10:00:00.0000",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 4

        response = client.post(
            url,
            {
                "name": "Metadata template container 37",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2025-02-18 10:00:00.0000",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 4

        response = client.post(
            url,
            {
                "name": "Metadata template container 38",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "Text",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 4

        response = client.post(
            url,
            {
                "name": "Metadata template container 39",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": None,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 5
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="datetime_1",
        )
        assert metadata_template_field_1.custom_key == "datetime_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.DATETIME
        assert metadata_template_field_1.get_value() is None

        response = client.post(
            url,
            {
                "name": "Metadata template container 40",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 6
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="datetime_1",
        )
        assert metadata_template_field_1.custom_key == "datetime_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.DATETIME
        assert metadata_template_field_1.get_value() is None

    def test_metadata_template_field_validate_date(self, client):
        """
        Ensure we get a correct validation of a date value.
        """
        url = reverse("metadata-template-container-list")

        response = client.post(
            url,
            {
                "name": "Metadata template container 2",
                "metadata_template_fields": [
                    {
                        "custom_key": "date_1",
                        "field_type": MetadataFieldType.DATE,
                        "value": 1337,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 3",
                "metadata_template_fields": [
                    {
                        "custom_key": "date_1",
                        "field_type": MetadataFieldType.DATE,
                        "value": 13.37,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 4",
                "metadata_template_fields": [
                    {
                        "custom_key": "date_1",
                        "field_type": MetadataFieldType.DATE,
                        "value": "18-02-2025",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 5",
                "metadata_template_fields": [
                    {
                        "custom_key": "date_1",
                        "field_type": MetadataFieldType.DATE,
                        "value": "02-18-2025",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 6",
                "metadata_template_fields": [
                    {
                        "custom_key": "date_1",
                        "field_type": MetadataFieldType.DATE,
                        "value": "18.02.2025",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 7",
                "metadata_template_fields": [
                    {
                        "custom_key": "date_1",
                        "field_type": MetadataFieldType.DATE,
                        "value": "02.18.2025",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 8",
                "metadata_template_fields": [
                    {
                        "custom_key": "date_1",
                        "field_type": MetadataFieldType.DATE,
                        "value": "18/02/2025",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 9",
                "metadata_template_fields": [
                    {
                        "custom_key": "date_1",
                        "field_type": MetadataFieldType.DATE,
                        "value": "02/18/2025",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 10",
                "metadata_template_fields": [
                    {
                        "custom_key": "date_1",
                        "field_type": MetadataFieldType.DATE,
                        "value": "2025-18-02",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 11",
                "metadata_template_fields": [
                    {
                        "custom_key": "date_1",
                        "field_type": MetadataFieldType.DATE,
                        "value": "2025-02-18",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 2
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="date_1",
        )
        assert metadata_template_field_1.custom_key == "date_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.DATE
        assert metadata_template_field_1.get_value() == "2025-02-18"

        response = client.post(
            url,
            {
                "name": "Metadata template container 12",
                "metadata_template_fields": [
                    {
                        "custom_key": "date_1",
                        "field_type": MetadataFieldType.DATE,
                        "value": "2025.18.02",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 2

        response = client.post(
            url,
            {
                "name": "Metadata template container 13",
                "metadata_template_fields": [
                    {
                        "custom_key": "date_1",
                        "field_type": MetadataFieldType.DATE,
                        "value": "2025.02.18",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 2

        response = client.post(
            url,
            {
                "name": "Metadata template container 14",
                "metadata_template_fields": [
                    {
                        "custom_key": "date_1",
                        "field_type": MetadataFieldType.DATE,
                        "value": "2025/18/02",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 2

        response = client.post(
            url,
            {
                "name": "Metadata template container 15",
                "metadata_template_fields": [
                    {
                        "custom_key": "date_1",
                        "field_type": MetadataFieldType.DATE,
                        "value": "2025/02/18",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 2

        response = client.post(
            url,
            {
                "name": "Metadata template container 16",
                "metadata_template_fields": [
                    {
                        "custom_key": "date_1",
                        "field_type": MetadataFieldType.DATE,
                        "value": "2025 Feb 18",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 2

        response = client.post(
            url,
            {
                "name": "Metadata template container 17",
                "metadata_template_fields": [
                    {
                        "custom_key": "date_1",
                        "field_type": MetadataFieldType.DATE,
                        "value": "Feb 18 2025",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 2

        response = client.post(
            url,
            {
                "name": "Metadata template container 18",
                "metadata_template_fields": [
                    {
                        "custom_key": "date_1",
                        "field_type": MetadataFieldType.DATE,
                        "value": "2025 February 18",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 2

        response = client.post(
            url,
            {
                "name": "Metadata template container 19",
                "metadata_template_fields": [
                    {
                        "custom_key": "date_1",
                        "field_type": MetadataFieldType.DATE,
                        "value": "February 18 2025",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 2

        response = client.post(
            url,
            {
                "name": "Metadata template container 20",
                "metadata_template_fields": [
                    {
                        "custom_key": "date_1",
                        "field_type": MetadataFieldType.DATE,
                        "value": "2025-2-18",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 3
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="date_1",
        )
        assert metadata_template_field_1.custom_key == "date_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.DATE
        assert metadata_template_field_1.get_value() == "2025-2-18"

        response = client.post(
            url,
            {
                "name": "Metadata template container 21",
                "metadata_template_fields": [
                    {
                        "custom_key": "date_1",
                        "field_type": MetadataFieldType.DATE,
                        "value": "2025-2-2",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 4
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="date_1",
        )
        assert metadata_template_field_1.custom_key == "date_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.DATE
        assert metadata_template_field_1.get_value() == "2025-2-2"

        response = client.post(
            url,
            {
                "name": "Metadata template container 22",
                "metadata_template_fields": [
                    {
                        "custom_key": "date_1",
                        "field_type": MetadataFieldType.DATE,
                        "value": "25-2-2",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 4

        response = client.post(
            url,
            {
                "name": "Metadata template container 23",
                "metadata_template_fields": [
                    {
                        "custom_key": "date_1",
                        "field_type": MetadataFieldType.DATE,
                        "value": "1900-02-29",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 4

        response = client.post(
            url,
            {
                "name": "Metadata template container 24",
                "metadata_template_fields": [
                    {
                        "custom_key": "date_1",
                        "field_type": MetadataFieldType.DATE,
                        "value": "2000-02-29",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 5
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="date_1",
        )
        assert metadata_template_field_1.custom_key == "date_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.DATE
        assert metadata_template_field_1.get_value() == "2000-02-29"

        response = client.post(
            url,
            {
                "name": "Metadata template container 25",
                "metadata_template_fields": [
                    {
                        "custom_key": "date_1",
                        "field_type": MetadataFieldType.DATE,
                        "value": "2004-02-29",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 6
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="date_1",
        )
        assert metadata_template_field_1.custom_key == "date_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.DATE
        assert metadata_template_field_1.get_value() == "2004-02-29"

        response = client.post(
            url,
            {
                "name": "Metadata template container 26",
                "metadata_template_fields": [
                    {
                        "custom_key": "date_1",
                        "field_type": MetadataFieldType.DATE,
                        "value": "2004-02-29 00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 6

        response = client.post(
            url,
            {
                "name": "Metadata template container 27",
                "metadata_template_fields": [
                    {
                        "custom_key": "date_1",
                        "field_type": MetadataFieldType.DATE,
                        "value": "2004-02-29 00:00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 6

        response = client.post(
            url,
            {
                "name": "Metadata template container 28",
                "metadata_template_fields": [
                    {
                        "custom_key": "date_1",
                        "field_type": MetadataFieldType.DATE,
                        "value": "Text",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 6

        response = client.post(
            url,
            {
                "name": "Metadata template container 29",
                "metadata_template_fields": [
                    {
                        "custom_key": "date_1",
                        "field_type": MetadataFieldType.DATE,
                        "value": None,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 7
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="date_1",
        )
        assert metadata_template_field_1.custom_key == "date_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.DATE
        assert metadata_template_field_1.get_value() is None

        response = client.post(
            url,
            {
                "name": "Metadata template container 30",
                "metadata_template_fields": [
                    {
                        "custom_key": "date_1",
                        "field_type": MetadataFieldType.DATE,
                        "value": "",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 8
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="date_1",
        )
        assert metadata_template_field_1.custom_key == "date_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.DATE
        assert metadata_template_field_1.get_value() is None

    def test_metadata_template_field_validate_time(self, client):
        """
        Ensure we get a correct validation of a time value.
        """
        url = reverse("metadata-template-container-list")

        response = client.post(
            url,
            {
                "name": "Metadata template container 2",
                "metadata_template_fields": [
                    {
                        "custom_key": "time_1",
                        "field_type": MetadataFieldType.TIME,
                        "value": 1337,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 3",
                "metadata_template_fields": [
                    {
                        "custom_key": "time_1",
                        "field_type": MetadataFieldType.TIME,
                        "value": 13.37,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 4",
                "metadata_template_fields": [
                    {
                        "custom_key": "time_1",
                        "field_type": MetadataFieldType.TIME,
                        "value": "2025-02-18",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 5",
                "metadata_template_fields": [
                    {
                        "custom_key": "time_1",
                        "field_type": MetadataFieldType.TIME,
                        "value": "2025-02-18 00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 6",
                "metadata_template_fields": [
                    {
                        "custom_key": "time_1",
                        "field_type": MetadataFieldType.TIME,
                        "value": "2025-02-18 00:00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 7",
                "metadata_template_fields": [
                    {
                        "custom_key": "time_1",
                        "field_type": MetadataFieldType.TIME,
                        "value": "10:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 8",
                "metadata_template_fields": [
                    {
                        "custom_key": "time_1",
                        "field_type": MetadataFieldType.TIME,
                        "value": "10:00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 2
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="time_1",
        )
        assert metadata_template_field_1.custom_key == "time_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.TIME
        assert metadata_template_field_1.get_value() == "10:00:00"

        response = client.post(
            url,
            {
                "name": "Metadata template container 9",
                "metadata_template_fields": [
                    {
                        "custom_key": "time_1",
                        "field_type": MetadataFieldType.TIME,
                        "value": "20:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 2

        response = client.post(
            url,
            {
                "name": "Metadata template container 10",
                "metadata_template_fields": [
                    {
                        "custom_key": "time_1",
                        "field_type": MetadataFieldType.TIME,
                        "value": "20:00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 3
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="time_1",
        )
        assert metadata_template_field_1.custom_key == "time_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.TIME
        assert metadata_template_field_1.get_value() == "20:00:00"

        response = client.post(
            url,
            {
                "name": "Metadata template container 11",
                "metadata_template_fields": [
                    {
                        "custom_key": "time_1",
                        "field_type": MetadataFieldType.TIME,
                        "value": "20:00:00.0000",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 3

        response = client.post(
            url,
            {
                "name": "Metadata template container 12",
                "metadata_template_fields": [
                    {
                        "custom_key": "time_1",
                        "field_type": MetadataFieldType.TIME,
                        "value": "2004-02-29 00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 3

        response = client.post(
            url,
            {
                "name": "Metadata template container 13",
                "metadata_template_fields": [
                    {
                        "custom_key": "time_1",
                        "field_type": MetadataFieldType.TIME,
                        "value": "2004-02-29 00:00:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 3

        response = client.post(
            url,
            {
                "name": "Metadata template container 14",
                "metadata_template_fields": [
                    {
                        "custom_key": "time_1",
                        "field_type": MetadataFieldType.TIME,
                        "value": "2004-02-29 00:00:00.0000",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 3

        response = client.post(
            url,
            {
                "name": "Metadata template container 15",
                "metadata_template_fields": [
                    {
                        "custom_key": "time_1",
                        "field_type": MetadataFieldType.TIME,
                        "value": "Text",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 3

        response = client.post(
            url,
            {
                "name": "Metadata template container 16",
                "metadata_template_fields": [
                    {
                        "custom_key": "time_1",
                        "field_type": MetadataFieldType.TIME,
                        "value": None,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 4
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="time_1",
        )
        assert metadata_template_field_1.custom_key == "time_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.TIME
        assert metadata_template_field_1.get_value() is None

        response = client.post(
            url,
            {
                "name": "Metadata template container 17",
                "metadata_template_fields": [
                    {
                        "custom_key": "time_1",
                        "field_type": MetadataFieldType.TIME,
                        "value": "",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 5
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="time_1",
        )
        assert metadata_template_field_1.custom_key == "time_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.TIME
        assert metadata_template_field_1.get_value() is None

    def test_metadata_template_field_validate_selection(self, client):
        """
        Ensure we get a correct validation of a selection value.
        """
        url = reverse("metadata-template-container-list")

        response = client.post(
            url,
            {
                "name": "Metadata template container 2",
                "metadata_template_fields": [
                    {
                        "custom_key": "selection_1",
                        "field_type": MetadataFieldType.SELECTION,
                        "value": "Option 1",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 3",
                "metadata_template_fields": [
                    {
                        "custom_key": "selection_1",
                        "field_type": MetadataFieldType.SELECTION,
                        "value": "Option 1",
                        "config": {
                            "key": "value",
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 4",
                "metadata_template_fields": [
                    {
                        "custom_key": "selection_1",
                        "field_type": MetadataFieldType.SELECTION,
                        "value": "Option 1",
                        "config": {
                            "options": "Option 1",
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 5",
                "metadata_template_fields": [
                    {
                        "custom_key": "selection_1",
                        "field_type": MetadataFieldType.SELECTION,
                        "value": "Option 1",
                        "config": {
                            "options": [
                                "Option 1",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 2
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="selection_1",
        )
        assert metadata_template_field_1.custom_key == "selection_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.SELECTION
        assert metadata_template_field_1.get_value() == "Option 1"

        response = client.post(
            url,
            {
                "name": "Metadata template container 6",
                "metadata_template_fields": [
                    {
                        "custom_key": "selection_1",
                        "field_type": MetadataFieldType.SELECTION,
                        "value": "Option 2",
                        "config": {
                            "options": [
                                "Option 1",
                                "Option 2",
                                "Option 3",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 3
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="selection_1",
        )
        assert metadata_template_field_1.custom_key == "selection_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.SELECTION
        assert metadata_template_field_1.get_value() == "Option 2"

        response = client.post(
            url,
            {
                "name": "Metadata template container 7",
                "metadata_template_fields": [
                    {
                        "custom_key": "selection_1",
                        "field_type": MetadataFieldType.SELECTION,
                        "value": "",
                        "config": {
                            "options": [
                                "",
                                "Option 1",
                                "Option 2",
                                "Option 3",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 3

        response = client.post(
            url,
            {
                "name": "Metadata template container 8",
                "metadata_template_fields": [
                    {
                        "custom_key": "selection_1",
                        "field_type": MetadataFieldType.SELECTION,
                        "value": None,
                        "config": {
                            "options": [
                                "",
                                "Option 1",
                                "Option 2",
                                "Option 3",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 3

        response = client.post(
            url,
            {
                "name": "Metadata template container 9",
                "metadata_template_fields": [
                    {
                        "custom_key": "selection_1",
                        "field_type": MetadataFieldType.SELECTION,
                        "value": 1,
                        "config": {
                            "options": [
                                "1",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 4
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="selection_1",
        )
        assert metadata_template_field_1.custom_key == "selection_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.SELECTION
        assert metadata_template_field_1.get_value() == "1"

    def test_metadata_template_field_validate_multi_selection(self, client):
        """
        Ensure we get a correct validation of a multi selection value.
        """
        url = reverse("metadata-template-container-list")

        response = client.post(
            url,
            {
                "name": "Metadata template container 2",
                "metadata_template_fields": [
                    {
                        "custom_key": "multi_selection_1",
                        "field_type": MetadataFieldType.MULTI_SELECTION,
                        "value": [
                            "Option 1",
                        ],
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 3",
                "metadata_template_fields": [
                    {
                        "custom_key": "multi_selection_1",
                        "field_type": MetadataFieldType.MULTI_SELECTION,
                        "value": [
                            "Option 1",
                        ],
                        "config": {
                            "key": "value",
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 4",
                "metadata_template_fields": [
                    {
                        "custom_key": "multi_selection_1",
                        "field_type": MetadataFieldType.MULTI_SELECTION,
                        "value": [
                            "Option 1",
                        ],
                        "config": {
                            "options": "Option 1",
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 5",
                "metadata_template_fields": [
                    {
                        "custom_key": "multi_selection_1",
                        "field_type": MetadataFieldType.MULTI_SELECTION,
                        "value": [
                            "Option 1",
                        ],
                        "config": {
                            "options": [
                                "Option 1",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 2
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="multi_selection_1",
        )
        assert metadata_template_field_1.custom_key == "multi_selection_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.MULTI_SELECTION
        assert metadata_template_field_1.get_value() == [
            "Option 1",
        ]

        response = client.post(
            url,
            {
                "name": "Metadata template container 6",
                "metadata_template_fields": [
                    {
                        "custom_key": "multi_selection_1",
                        "field_type": MetadataFieldType.MULTI_SELECTION,
                        "value": [
                            "Option 1",
                            "Option 3",
                        ],
                        "config": {
                            "options": [
                                "Option 1",
                                "Option 2",
                                "Option 3",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 3
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="multi_selection_1",
        )
        assert metadata_template_field_1.custom_key == "multi_selection_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.MULTI_SELECTION
        assert metadata_template_field_1.get_value() == [
            "Option 1",
            "Option 3",
        ]

        response = client.post(
            url,
            {
                "name": "Metadata template container 7",
                "metadata_template_fields": [
                    {
                        "custom_key": "multi_selection_1",
                        "field_type": MetadataFieldType.MULTI_SELECTION,
                        "value": [
                            "Option 4",
                        ],
                        "config": {
                            "options": [
                                "Option 1",
                                "Option 2",
                                "Option 3",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 3

        response = client.post(
            url,
            {
                "name": "Metadata template container 8",
                "metadata_template_fields": [
                    {
                        "custom_key": "multi_selection_1",
                        "field_type": MetadataFieldType.MULTI_SELECTION,
                        "value": [],
                        "config": {
                            "options": [
                                "Option 1",
                                "Option 2",
                                "Option 3",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 4
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="multi_selection_1",
        )
        assert metadata_template_field_1.custom_key == "multi_selection_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.MULTI_SELECTION
        assert metadata_template_field_1.get_value() == []

        response = client.post(
            url,
            {
                "name": "Metadata template container 9",
                "metadata_template_fields": [
                    {
                        "custom_key": "multi_selection_1",
                        "field_type": MetadataFieldType.MULTI_SELECTION,
                        "value": None,
                        "config": {
                            "options": [
                                "Option 1",
                                "Option 2",
                                "Option 3",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 5
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="multi_selection_1",
        )
        assert metadata_template_field_1.custom_key == "multi_selection_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.MULTI_SELECTION
        assert metadata_template_field_1.get_value() is None

        response = client.post(
            url,
            {
                "name": "Metadata template container 10",
                "metadata_template_fields": [
                    {
                        "custom_key": "multi_selection_1",
                        "field_type": MetadataFieldType.MULTI_SELECTION,
                        "value": "",
                        "config": {
                            "options": [
                                "Option 1",
                                "Option 2",
                                "Option 3",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 5

        response = client.post(
            url,
            {
                "name": "Metadata template container 11",
                "metadata_template_fields": [
                    {
                        "custom_key": "multi_selection_1",
                        "field_type": MetadataFieldType.MULTI_SELECTION,
                        "value": 1,
                        "config": {
                            "options": [
                                "1",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 6
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="multi_selection_1",
        )
        assert metadata_template_field_1.custom_key == "multi_selection_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.MULTI_SELECTION
        assert metadata_template_field_1.get_value() == [
            "1",
        ]

    def test_metadata_template_field_validate_tag(self, client):
        """
        Ensure we get a correct validation of a tag value.
        """
        url = reverse("metadata-template-container-list")

        response = client.post(
            url,
            {
                "name": "Metadata template container 2",
                "metadata_template_fields": [
                    {
                        "custom_key": "tag_1",
                        "field_type": MetadataFieldType.TAG,
                        "value": [
                            "Tag 1",
                        ],
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 3",
                "metadata_template_fields": [
                    {
                        "custom_key": "tag_1",
                        "field_type": MetadataFieldType.TAG,
                        "value": [
                            "Tag 1",
                        ],
                        "config": {
                            "key": "value",
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 4",
                "metadata_template_fields": [
                    {
                        "custom_key": "tag_1",
                        "field_type": MetadataFieldType.TAG,
                        "value": [
                            "Tag 1",
                        ],
                        "config": {
                            "options": "Tag 1",
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 1

        response = client.post(
            url,
            {
                "name": "Metadata template container 5",
                "metadata_template_fields": [
                    {
                        "custom_key": "tag_1",
                        "field_type": MetadataFieldType.TAG,
                        "value": [
                            "Tag 1",
                        ],
                        "config": {
                            "options": [
                                "Tag 1",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 2
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="tag_1",
        )
        assert metadata_template_field_1.custom_key == "tag_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.TAG
        assert metadata_template_field_1.get_value() == [
            "Tag 1",
        ]

        response = client.post(
            url,
            {
                "name": "Metadata template container 6",
                "metadata_template_fields": [
                    {
                        "custom_key": "tag_1",
                        "field_type": MetadataFieldType.TAG,
                        "value": [
                            "Tag 1",
                            "Tag 3",
                        ],
                        "config": {
                            "options": [
                                "Tag 1",
                                "Tag 2",
                                "Tag 3",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 3
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="tag_1",
        )
        assert metadata_template_field_1.custom_key == "tag_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.TAG
        assert metadata_template_field_1.get_value() == [
            "Tag 1",
            "Tag 3",
        ]

        response = client.post(
            url,
            {
                "name": "Metadata template container 7",
                "metadata_template_fields": [
                    {
                        "custom_key": "tag_1",
                        "field_type": MetadataFieldType.TAG,
                        "value": [
                            "Tag 4",
                        ],
                        "config": {
                            "options": [
                                "Tag 1",
                                "Tag 2",
                                "Tag 3",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 3

        response = client.post(
            url,
            {
                "name": "Metadata template container 8",
                "metadata_template_fields": [
                    {
                        "custom_key": "tag_1",
                        "field_type": MetadataFieldType.TAG,
                        "value": [],
                        "config": {
                            "options": [
                                "Tag 1",
                                "Tag 2",
                                "Tag 3",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 4
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="tag_1",
        )
        assert metadata_template_field_1.custom_key == "tag_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.TAG
        assert metadata_template_field_1.get_value() == []

        response = client.post(
            url,
            {
                "name": "Metadata template container 9",
                "metadata_template_fields": [
                    {
                        "custom_key": "tag_1",
                        "field_type": MetadataFieldType.TAG,
                        "value": None,
                        "config": {
                            "options": [
                                "Tag 1",
                                "Tag 2",
                                "Tag 3",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 5
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="tag_1",
        )
        assert metadata_template_field_1.custom_key == "tag_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.TAG
        assert metadata_template_field_1.get_value() is None

        response = client.post(
            url,
            {
                "name": "Metadata template container 10",
                "metadata_template_fields": [
                    {
                        "custom_key": "tag_1",
                        "field_type": MetadataFieldType.TAG,
                        "value": "",
                        "config": {
                            "options": [
                                "Tag 1",
                                "Tag 2",
                                "Tag 3",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 5

        response = client.post(
            url,
            {
                "name": "Metadata template container 11",
                "metadata_template_fields": [
                    {
                        "custom_key": "tag_1",
                        "field_type": MetadataFieldType.TAG,
                        "value": 1,
                        "config": {
                            "options": [
                                "1",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 6
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="tag_1",
        )
        assert metadata_template_field_1.custom_key == "tag_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.TAG
        assert metadata_template_field_1.get_value() == [
            "1",
        ]

    def test_metadata_template_field_validate_wysiwyg(self, client):
        """
        Ensure we get a correct validation of a selection value.
        """
        url = reverse("metadata-template-container-list")

        response = client.post(
            url,
            {
                "name": "Metadata template container 2",
                "metadata_template_fields": [
                    {
                        "custom_key": "wysiwyg_1",
                        "field_type": MetadataFieldType.WYSIWYG,
                        "value": {
                            "paragraph": "Text",
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 2
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="wysiwyg_1",
        )
        assert metadata_template_field_1.custom_key == "wysiwyg_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.WYSIWYG
        assert metadata_template_field_1.get_value() == {
            "paragraph": "Text",
        }

        response = client.post(
            url,
            {
                "name": "Metadata template container 3",
                "metadata_template_fields": [
                    {
                        "custom_key": "wysiwyg_1",
                        "field_type": MetadataFieldType.WYSIWYG,
                        "value": {},
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 3
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="wysiwyg_1",
        )
        assert metadata_template_field_1.custom_key == "wysiwyg_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.WYSIWYG
        assert metadata_template_field_1.get_value() == {}

        response = client.post(
            url,
            {
                "name": "Metadata template container 4",
                "metadata_template_fields": [
                    {
                        "custom_key": "wysiwyg_1",
                        "field_type": MetadataFieldType.WYSIWYG,
                        "value": "Text",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == 3

        response = client.post(
            url,
            {
                "name": "Metadata template container 5",
                "metadata_template_fields": [
                    {
                        "custom_key": "wysiwyg_1",
                        "field_type": MetadataFieldType.WYSIWYG,
                        "value": None,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 4
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="wysiwyg_1",
        )
        assert metadata_template_field_1.custom_key == "wysiwyg_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.WYSIWYG
        assert metadata_template_field_1.get_value() == {}

        response = client.post(
            url,
            {
                "name": "Metadata template container 6",
                "metadata_template_fields": [
                    {
                        "custom_key": "wysiwyg_1",
                        "field_type": MetadataFieldType.WYSIWYG,
                        "value": "",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert MetadataTemplateContainer.objects.count() == 5
        assert (
            MetadataTemplateField.objects.filter(
                metadata_template=response.data["latest_metadata_template"]["pk"],
            ).count()
            == 1
        )

        metadata_template_field_1 = MetadataTemplateField.objects.get(
            metadata_template=response.data["latest_metadata_template"]["pk"],
            custom_key="wysiwyg_1",
        )
        assert metadata_template_field_1.custom_key == "wysiwyg_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.WYSIWYG
        assert metadata_template_field_1.get_value() == {}

    def test_metadata_template_field_validate_field_types(self, client):
        """
        Ensure we get the correct typing of metadata values.
        """
        url = reverse("metadata-template-container-list")

        response = client.post(
            url,
            {
                "name": "Metadata template container 2",
                "metadata_template_fields": [
                    {
                        "custom_key": "integer_1",
                        "field_type": MetadataFieldType.INTEGER,
                        "value": 1337,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        field_url = reverse("metadata-template-field-list")
        url_filter = f"{field_url}?metadata_template={response.data['latest_metadata_template']['pk']}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        assert response.data["results"][0]["custom_key"] == "integer_1"
        assert response.data["results"][0]["field_type"] == MetadataFieldType.INTEGER
        assert response.data["results"][0]["value"] == "1337"
        assert response.data["results"][0]["config"] == {}

        response = client.post(
            url,
            {
                "name": "Metadata template container 3",
                "metadata_template_fields": [
                    {
                        "custom_key": "decimal_1",
                        "field_type": MetadataFieldType.DECIMAL,
                        "value": 13.37,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        field_url = reverse("metadata-template-field-list")
        url_filter = f"{field_url}?metadata_template={response.data['latest_metadata_template']['pk']}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        assert response.data["results"][0]["custom_key"] == "decimal_1"
        assert response.data["results"][0]["field_type"] == MetadataFieldType.DECIMAL
        assert response.data["results"][0]["value"] == "13.37"
        assert response.data["results"][0]["config"] == {}

        response = client.post(
            url,
            {
                "name": "Metadata template container 4",
                "metadata_template_fields": [
                    {
                        "custom_key": "datetime_1",
                        "field_type": MetadataFieldType.DATETIME,
                        "value": "2025-02-18 13:37:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        field_url = reverse("metadata-template-field-list")
        url_filter = f"{field_url}?metadata_template={response.data['latest_metadata_template']['pk']}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        assert response.data["results"][0]["custom_key"] == "datetime_1"
        assert response.data["results"][0]["field_type"] == MetadataFieldType.DATETIME
        assert response.data["results"][0]["value"] == "2025-02-18 13:37:00"
        assert response.data["results"][0]["config"] == {}

        response = client.post(
            url,
            {
                "name": "Metadata template container 5",
                "metadata_template_fields": [
                    {
                        "custom_key": "date_1",
                        "field_type": MetadataFieldType.DATE,
                        "value": "2025-02-18",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        field_url = reverse("metadata-template-field-list")
        url_filter = f"{field_url}?metadata_template={response.data['latest_metadata_template']['pk']}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        assert response.data["results"][0]["custom_key"] == "date_1"
        assert response.data["results"][0]["field_type"] == MetadataFieldType.DATE
        assert response.data["results"][0]["value"] == "2025-02-18"
        assert response.data["results"][0]["config"] == {}

        response = client.post(
            url,
            {
                "name": "Metadata template container 6",
                "metadata_template_fields": [
                    {
                        "custom_key": "time_1",
                        "field_type": MetadataFieldType.TIME,
                        "value": "13:37:00",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        field_url = reverse("metadata-template-field-list")
        url_filter = f"{field_url}?metadata_template={response.data['latest_metadata_template']['pk']}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        assert response.data["results"][0]["custom_key"] == "time_1"
        assert response.data["results"][0]["field_type"] == MetadataFieldType.TIME
        assert response.data["results"][0]["value"] == "13:37:00"
        assert response.data["results"][0]["config"] == {}

        response = client.post(
            url,
            {
                "name": "Metadata template container 7",
                "metadata_template_fields": [
                    {
                        "custom_key": "text_1",
                        "field_type": MetadataFieldType.TEXT,
                        "value": "Text",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        field_url = reverse("metadata-template-field-list")
        url_filter = f"{field_url}?metadata_template={response.data['latest_metadata_template']['pk']}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        assert response.data["results"][0]["custom_key"] == "text_1"
        assert response.data["results"][0]["field_type"] == MetadataFieldType.TEXT
        assert response.data["results"][0]["value"] == "Text"
        assert response.data["results"][0]["config"] == {}

        response = client.post(
            url,
            {
                "name": "Metadata template container 8",
                "metadata_template_fields": [
                    {
                        "custom_key": "wysiwyg_1",
                        "field_type": MetadataFieldType.WYSIWYG,
                        "value": {
                            "text": "1337",
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        field_url = reverse("metadata-template-field-list")
        url_filter = f"{field_url}?metadata_template={response.data['latest_metadata_template']['pk']}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        assert response.data["results"][0]["custom_key"] == "wysiwyg_1"
        assert response.data["results"][0]["field_type"] == MetadataFieldType.WYSIWYG
        assert response.data["results"][0]["value"] == {
            "text": "1337",
        }
        assert response.data["results"][0]["config"] == {}

        response = client.post(
            url,
            {
                "name": "Metadata template container 9",
                "metadata_template_fields": [
                    {
                        "custom_key": "selection_1",
                        "field_type": MetadataFieldType.SELECTION,
                        "value": "Option 1",
                        "config": {
                            "options": [
                                "Option 1",
                                "Option 2",
                                "Option 3",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        field_url = reverse("metadata-template-field-list")
        url_filter = f"{field_url}?metadata_template={response.data['latest_metadata_template']['pk']}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        assert response.data["results"][0]["custom_key"] == "selection_1"
        assert response.data["results"][0]["field_type"] == MetadataFieldType.SELECTION
        assert response.data["results"][0]["value"] == "Option 1"
        assert response.data["results"][0]["config"] == {
            "options": [
                "Option 1",
                "Option 2",
                "Option 3",
            ],
        }

        response = client.post(
            url,
            {
                "name": "Metadata template container 11",
                "metadata_template_fields": [
                    {
                        "custom_key": "tag_1",
                        "field_type": MetadataFieldType.TAG,
                        "value": [
                            "Tag 1",
                            "Tag 3",
                        ],
                        "config": {
                            "options": [
                                "Tag 1",
                                "Tag 2",
                                "Tag 3",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        field_url = reverse("metadata-template-field-list")
        url_filter = f"{field_url}?metadata_template={response.data['latest_metadata_template']['pk']}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        assert response.data["results"][0]["custom_key"] == "tag_1"
        assert response.data["results"][0]["field_type"] == MetadataFieldType.TAG
        assert response.data["results"][0]["value"] == [
            "Tag 1",
            "Tag 3",
        ]
        assert response.data["results"][0]["config"] == {
            "options": [
                "Tag 1",
                "Tag 2",
                "Tag 3",
            ],
        }

        response = client.post(
            url,
            {
                "name": "Metadata template container 10",
                "metadata_template_fields": [
                    {
                        "custom_key": "multi_selection_1",
                        "field_type": MetadataFieldType.MULTI_SELECTION,
                        "value": [
                            "Option 1",
                            "Option 3",
                        ],
                        "config": {
                            "options": [
                                "Option 1",
                                "Option 2",
                                "Option 3",
                            ],
                        },
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        field_url = reverse("metadata-template-field-list")
        url_filter = f"{field_url}?metadata_template={response.data['latest_metadata_template']['pk']}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        assert response.data["results"][0]["custom_key"] == "multi_selection_1"
        assert response.data["results"][0]["field_type"] == MetadataFieldType.MULTI_SELECTION
        assert response.data["results"][0]["value"] == [
            "Option 1",
            "Option 3",
        ]
        assert response.data["results"][0]["config"] == {
            "options": [
                "Option 1",
                "Option 2",
                "Option 3",
            ],
        }

    def test_metadata_template_restore_action(self, client, initial_users):
        """
        Ensure we can restore a metadata template by making a POST request to the respective metadata template container
        action endpoint.
        """
        url = reverse("metadata-template-container-list")

        response = client.post(
            url,
            {
                "name": "Test metadata template",
                "metadata_template_fields": [],
                "assigned_to_content_type": get_content_type_for_model(Project),
                "assigned_to_object_id": self.project_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        metadata_template_container_1 = response.data
        assert metadata_template_container_1["name"] == "Test metadata template"

        metadata_template_1 = MetadataTemplate.objects.get(
            metadata_template_container__pk=metadata_template_container_1["pk"],
        )

        url = reverse(
            "metadata-template-detail",
            kwargs={
                "pk": metadata_template_1.pk,
            },
        )
        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK

        metadata_template_1 = response.data
        assert metadata_template_1["name"] is None
        assert MetadataTemplateField.objects.filter(metadata_template=metadata_template_1["pk"]).count() == 0

        url = reverse(
            "metadata-template-container-detail",
            kwargs={
                "pk": metadata_template_container_1["pk"],
            },
        )
        version_action_url = f"{url}version/"
        restore_action_url = f"{url}restore/"

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["metadata_templates"]) == 1
        assert response.data["metadata_templates"][0]["pk"] == metadata_template_1["pk"]

        response = client.post(
            restore_action_url,
            {
                "metadata_template": metadata_template_1["pk"],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        response = client.post(
            version_action_url,
            {
                "metadata_template_fields": [
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
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        metadata_template_2 = response.data
        assert metadata_template_2["name"] is None
        assert MetadataTemplateField.objects.filter(metadata_template=metadata_template_2["pk"]).count() == 2

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["metadata_templates"]) == 2
        assert response.data["metadata_templates"][0]["pk"] == metadata_template_2["pk"]

        response = client.post(
            restore_action_url,
            {
                "metadata_template": metadata_template_1["pk"],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        metadata_template_3 = response.data
        assert metadata_template_3["name"] is None
        assert MetadataTemplateField.objects.filter(metadata_template=metadata_template_3["pk"]).count() == 0

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["metadata_templates"]) == 3
        assert metadata_template_1["pk"] != metadata_template_3["pk"]
        assert response.data["metadata_templates"][0]["pk"] == metadata_template_3["pk"]

        response = client.post(
            restore_action_url,
            {
                "metadata_template": metadata_template_2["pk"],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        metadata_template_4 = response.data
        assert metadata_template_4["name"] is None
        assert MetadataTemplateField.objects.filter(metadata_template=metadata_template_4["pk"]).count() == 2

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["metadata_templates"]) == 4
        assert metadata_template_2["pk"] != metadata_template_4["pk"]
        assert response.data["metadata_templates"][0]["pk"] == metadata_template_4["pk"]

        response = client.post(
            restore_action_url,
            {
                "metadata_template": metadata_template_4["pk"],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        metadata_template_container_2 = MetadataTemplateContainer.objects.create(
            name="Metadata template container 2",
        )

        metadata_template_5 = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_2,
        )

        response = client.post(
            restore_action_url,
            {
                "metadata_template": metadata_template_5.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_metadata_template_clone_action(self, client, initial_users):
        """
        Ensure we can clone a metadata template by making a POST request to the respective metadata template container
        action endpoint.
        """
        url = reverse("metadata-template-container-list")

        response = client.post(
            url,
            {
                "name": "Test metadata template",
                "metadata_template_fields": [],
                "assigned_to_content_type": get_content_type_for_model(Project),
                "assigned_to_object_id": self.project_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        metadata_template_container_1 = response.data
        assert metadata_template_container_1["name"] == "Test metadata template"

        metadata_template_1 = MetadataTemplate.objects.get(
            metadata_template_container__pk=metadata_template_container_1["pk"],
        )

        url = reverse(
            "metadata-template-detail",
            kwargs={
                "pk": metadata_template_1.pk,
            },
        )
        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK

        metadata_template_1 = response.data
        assert metadata_template_1["name"] is None
        assert MetadataTemplateField.objects.filter(metadata_template=metadata_template_1["pk"]).count() == 0

        url = reverse(
            "metadata-template-container-detail",
            kwargs={
                "pk": metadata_template_container_1["pk"],
            },
        )
        version_action_url = f"{url}version/"
        clone_action_url = f"{url}clone/"

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["metadata_templates"]) == 1
        assert response.data["metadata_templates"][0]["pk"] == metadata_template_1["pk"]

        response = client.post(
            version_action_url,
            {
                "metadata_template_fields": [
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
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        metadata_template_2 = response.data
        assert metadata_template_2["name"] is None
        assert MetadataTemplateField.objects.filter(metadata_template=metadata_template_2["pk"]).count() == 2

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["metadata_templates"]) == 2
        assert response.data["metadata_templates"][0]["pk"] == metadata_template_2["pk"]

        response = client.post(
            clone_action_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        metadata_template_container_2 = response.data

        url = reverse(
            "metadata-template-container-detail",
            kwargs={
                "pk": metadata_template_container_2["pk"],
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == f"Copy of {metadata_template_container_1['name']}"
        assert len(response.data["metadata_templates"]) == 1
        assert response.data["metadata_templates"][0]["pk"] not in [
            metadata_template_1["pk"],
            metadata_template_2["pk"],
        ]
        assert (
            MetadataTemplateField.objects.filter(metadata_template=response.data["metadata_templates"][0]["pk"]).count()
            == 2
        )

    def _create_clone_source(self):
        metadata_template_container = MetadataTemplateContainer.objects.create(
            name="Source metadata template",
        )
        old_metadata_template = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container,
        )
        MetadataTemplateField.objects.create(
            metadata_template=old_metadata_template,
            custom_key="old_custom_key",
        )

        latest_metadata_template = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container,
        )
        latest_field = MetadataTemplateField.objects.create(
            metadata_template=latest_metadata_template,
            custom_key="latest_custom_key",
            config={
                "placeholder": "Clone me",
            },
            mandatory=True,
            order=7,
        )
        latest_field.set_value("latest value")

        return metadata_template_container, latest_metadata_template, latest_field

    @staticmethod
    def _clone_url(metadata_template_container):
        return (
            reverse(
                "metadata-template-container-detail",
                kwargs={
                    "pk": metadata_template_container.pk,
                },
            )
            + "clone/"
        )

    def _assert_latest_version_was_cloned(self, response, latest_field):
        assert len(response.data["metadata_templates"]) == 1
        cloned_template = MetadataTemplate.objects.get(pk=response.data["latest_metadata_template"]["pk"])
        cloned_fields = list(cloned_template.metadata_template_fields.all())
        assert len(cloned_fields) == 1
        assert cloned_fields[0].custom_key == latest_field.custom_key
        assert cloned_fields[0].config == latest_field.config
        assert cloned_fields[0].mandatory is True
        assert cloned_fields[0].order == latest_field.order
        assert cloned_fields[0].get_value() == latest_field.get_value()

    def test_metadata_template_clone_action_into_target_scopes(self, client, initial_users):
        """
        Ensure clone can target project, folder, dataset, and global scopes while copying only the latest version.
        """
        source_container, _, latest_field = self._create_clone_source()
        target_project = Project.objects.create(name="Target project")
        target_folder = target_project.folders.first()
        target_dataset = UploadsDataset.objects.create(
            name="Target dataset",
            folder=target_folder,
        )

        user_2_membership = ProjectMembership.objects.create(
            project=target_project,
            member=initial_users["user_2"],
            is_metadata_template_admin=True,
        )
        FolderPermission.objects.create(
            project_membership=user_2_membership,
            folder=target_folder,
            is_metadata_template_admin=True,
            can_edit=True,
        )

        auth_user(client, initial_users["user_2"])
        clone_url = self._clone_url(source_container)

        response = client.post(
            clone_url,
            {
                "assigned_to_content_type": get_content_type_for_model(Project),
                "assigned_to_object_id": target_project.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["assigned_to_content_type"] == get_content_type_for_model(Project)
        assert response.data["assigned_to_object_id"] == str(target_project.pk)
        self._assert_latest_version_was_cloned(response, latest_field)

        response = client.post(
            clone_url,
            {
                "assigned_to_content_type": get_content_type_for_model(Folder),
                "assigned_to_object_id": target_folder.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["assigned_to_content_type"] == get_content_type_for_model(Folder)
        assert response.data["assigned_to_object_id"] == str(target_folder.pk)

        response = client.post(
            clone_url,
            {
                "assigned_to_content_type": get_content_type_for_model(UploadsDataset),
                "assigned_to_object_id": target_dataset.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["assigned_to_content_type"] == get_content_type_for_model(UploadsDataset)
        assert response.data["assigned_to_object_id"] == str(target_dataset.pk)

        dataset_container_url = reverse(
            "metadata-template-container-detail",
            kwargs={
                "pk": response.data["pk"],
            },
        )
        response = client.get(dataset_container_url, format="json")
        assert response.status_code == status.HTTP_200_OK

        response = client.post(
            f"{dataset_container_url}version/",
            {
                "metadata_template_fields": [],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        initial_users["user_2"].is_global_metadata_template_admin = True
        initial_users["user_2"].save()
        response = client.post(
            clone_url,
            {
                "assigned_to_content_type": None,
                "assigned_to_object_id": None,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["assigned_to_content_type"] is None
        assert response.data["assigned_to_object_id"] is None

    def test_metadata_template_clone_action_target_permission_errors(self, client, initial_users):
        """
        Ensure clone target permissions are enforced for project, folder, dataset, and global targets.
        """
        source_container, _, _ = self._create_clone_source()
        target_project = Project.objects.create(name="Target project")
        target_folder = target_project.folders.first()
        target_dataset = UploadsDataset.objects.create(
            name="Target dataset",
            folder=target_folder,
        )
        user_2_membership = ProjectMembership.objects.create(
            project=target_project,
            member=initial_users["user_2"],
        )
        folder_permission = FolderPermission.objects.create(
            project_membership=user_2_membership,
            folder=target_folder,
            can_edit=True,
            is_metadata_template_admin=False,
        )

        auth_user(client, initial_users["user_2"])
        clone_url = self._clone_url(source_container)

        response = client.post(
            clone_url,
            {
                "assigned_to_content_type": get_content_type_for_model(Project),
                "assigned_to_object_id": target_project.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        response = client.post(
            clone_url,
            {
                "assigned_to_content_type": get_content_type_for_model(Folder),
                "assigned_to_object_id": target_folder.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        response = client.post(
            clone_url,
            {
                "assigned_to_content_type": get_content_type_for_model(UploadsDataset),
                "assigned_to_object_id": target_dataset.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        folder_permission.is_metadata_template_admin = True
        folder_permission.can_edit = False
        folder_permission.save()
        response = client.post(
            clone_url,
            {
                "assigned_to_content_type": get_content_type_for_model(UploadsDataset),
                "assigned_to_object_id": target_dataset.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        response = client.post(
            clone_url,
            {
                "assigned_to_content_type": None,
                "assigned_to_object_id": None,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_metadata_template_clone_action_target_validation_errors(self, client):
        """
        Ensure invalid clone target payloads are rejected before creating a container.
        """
        source_container, _, _ = self._create_clone_source()
        clone_url = self._clone_url(source_container)
        containers_before = MetadataTemplateContainer.objects.count()

        response = client.post(
            clone_url,
            {
                "assigned_to_content_type": get_content_type_for_model(Project),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        response = client.post(
            clone_url,
            {
                "assigned_to_content_type": None,
                "assigned_to_object_id": self.project_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        response = client.post(
            clone_url,
            {
                "assigned_to_content_type": get_content_type_for_model(MetadataTemplate),
                "assigned_to_object_id": self.project_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        response = client.post(
            clone_url,
            {
                "assigned_to_content_type": get_content_type_for_model(Project),
                "assigned_to_object_id": uuid.uuid4(),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert MetadataTemplateContainer.objects.count() == containers_before


@pytest.mark.django_db
class TestMetadataTemplateContainerPoolAutoAdd:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.project_1 = Project.objects.create(name="Project 1")
        self.folder_1 = self.project_1.folders.first()

    def _create_url(self):
        return reverse("metadata-template-container-list")

    def _project_pool_url(self, project):
        return reverse("project-detail", kwargs={"pk": project.pk}) + "metadata-template-container-pool/"

    def _folder_pool_url(self, folder):
        return reverse("folder-detail", kwargs={"pk": folder.pk}) + "metadata-template-container-pool/"

    def test_project_scoped_create_adds_container_to_project_pool(self, client):
        """
        Creating a project-scoped template as the project's admin should land the new
        container in the project's pool without any further action.
        """
        response = client.post(
            self._create_url(),
            {
                "name": "Auto-pooled project template",
                "assigned_to_content_type": get_content_type_for_object(self.project_1.get_content_type()),
                "assigned_to_object_id": self.project_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        new_container_pk = response.data["pk"]

        pool_response = client.get(self._project_pool_url(self.project_1), format="json")
        assert pool_response.status_code == status.HTTP_200_OK
        assert any(container["pk"] == new_container_pk for container in pool_response.data)

    def test_global_create_does_not_touch_any_pool(self, client):
        """
        Creating a global (unscoped) template must not add membership to any
        project or folder pool.
        """
        response = client.post(
            self._create_url(),
            {
                "name": "Global template",
                "assigned_to_content_type": None,
                "assigned_to_object_id": None,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        new_container_pk = response.data["pk"]

        project_pool = client.get(self._project_pool_url(self.project_1), format="json")
        folder_pool = client.get(self._folder_pool_url(self.folder_1), format="json")
        assert not any(c["pk"] == new_container_pk for c in project_pool.data)
        assert not any(c["pk"] == new_container_pk for c in folder_pool.data)

    def test_dataset_scoped_create_does_not_touch_any_pool(self, client):
        """
        Creating a template scoped to an UploadsDataset must not attempt to touch
        any pool (datasets have no pool concept).
        """
        dataset = UploadsDataset.objects.create(name="Dataset 1")

        response = client.post(
            self._create_url(),
            {
                "name": "Dataset-scoped template",
                "assigned_to_content_type": get_content_type_for_object(dataset.get_content_type()),
                "assigned_to_object_id": dataset.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        new_container_pk = response.data["pk"]

        project_pool = client.get(self._project_pool_url(self.project_1), format="json")
        folder_pool = client.get(self._folder_pool_url(self.folder_1), format="json")
        assert not any(c["pk"] == new_container_pk for c in project_pool.data)
        assert not any(c["pk"] == new_container_pk for c in folder_pool.data)

    def test_scoped_create_skips_pool_when_caller_is_not_scope_admin(self, client, initial_users):
        """
        A caller who is a metadata-template admin on the scope but NOT a project
        (or folder) admin can create a scoped template; the template is created,
        but the auto-add to the pool is silently skipped because only scope
        admins may edit pool membership.
        """
        membership = ProjectMembership.objects.create(
            project=self.project_1,
            member=initial_users["user_2"],
            is_project_admin=False,
            is_metadata_template_admin=True,
        )
        assert membership.is_project_admin is False
        assert membership.is_metadata_template_admin is True

        auth_user(client, initial_users["user_2"])

        response = client.post(
            self._create_url(),
            {
                "name": "Template-admin-only scoped",
                "assigned_to_content_type": get_content_type_for_object(self.project_1.get_content_type()),
                "assigned_to_object_id": self.project_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        new_container_pk = response.data["pk"]

        pool_response = client.get(self._project_pool_url(self.project_1), format="json")
        assert pool_response.status_code == status.HTTP_200_OK
        assert not any(c["pk"] == new_container_pk for c in pool_response.data)

    def test_scoped_create_rolls_back_when_pool_add_fails(self, client, mocker):
        """
        If the auto-add step fails, the whole create is rolled back: no orphan
        template row survives. This preserves the invariant that a scoped
        template is either fully present (record + pool membership) or absent.
        """
        mocker.patch(
            "fdm.metadata.rest.views.add_scoped_container_to_pool",
            side_effect=RuntimeError("simulated pool add failure"),
        )

        containers_before = MetadataTemplateContainer.objects.count()

        with pytest.raises(RuntimeError):
            client.post(
                self._create_url(),
                {
                    "name": "Rollback scoped template",
                    "assigned_to_content_type": get_content_type_for_object(self.project_1.get_content_type()),
                    "assigned_to_object_id": self.project_1.pk,
                },
                format="json",
            )

        assert MetadataTemplateContainer.objects.count() == containers_before

    def test_skip_pool_add_query_param_suppresses_auto_add(self, client):
        """
        Passing ?skip_pool_add=true suppresses the auto-add for admin callers,
        giving automation/provisioning scripts an explicit opt-out.
        """
        response = client.post(
            f"{self._create_url()}?skip_pool_add=true",
            {
                "name": "Opt-out template",
                "assigned_to_content_type": get_content_type_for_object(self.project_1.get_content_type()),
                "assigned_to_object_id": self.project_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        new_container_pk = response.data["pk"]

        pool_response = client.get(self._project_pool_url(self.project_1), format="json")
        assert pool_response.status_code == status.HTTP_200_OK
        assert not any(c["pk"] == new_container_pk for c in pool_response.data)

    def test_legacy_frontend_replay_keeps_new_container_exactly_once(self, client):
        """
        Backward-compat: today's frontend POSTs a scoped template and then PUTs
        the pool with the full list including the new id. After auto-add, that
        sequence must still leave the new id in the pool exactly once (the PUT
        becomes an idempotent no-op, not a duplicate).
        """
        response = client.post(
            self._create_url(),
            {
                "name": "Replay template",
                "assigned_to_content_type": get_content_type_for_object(self.project_1.get_content_type()),
                "assigned_to_object_id": self.project_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        new_container_pk = response.data["pk"]

        put_response = client.put(
            self._project_pool_url(self.project_1),
            {"metadata_template_container": [new_container_pk]},
            format="json",
        )
        assert put_response.status_code == status.HTTP_200_OK

        pool_response = client.get(self._project_pool_url(self.project_1), format="json")
        assert pool_response.status_code == status.HTTP_200_OK
        matching = [c for c in pool_response.data if c["pk"] == new_container_pk]
        assert len(matching) == 1

    def test_folder_scoped_create_adds_container_to_folder_pool(self, client):
        """
        Creating a folder-scoped template as the folder's admin should land the new
        container in the folder's pool without any further action.
        """
        response = client.post(
            self._create_url(),
            {
                "name": "Auto-pooled folder template",
                "assigned_to_content_type": get_content_type_for_object(self.folder_1.get_content_type()),
                "assigned_to_object_id": self.folder_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        new_container_pk = response.data["pk"]

        pool_response = client.get(self._folder_pool_url(self.folder_1), format="json")
        assert pool_response.status_code == status.HTTP_200_OK
        assert any(container["pk"] == new_container_pk for container in pool_response.data)


@pytest.mark.django_db
class TestMetadataTemplateAPI:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.project_1 = Project.objects.create(
            name="Project 1",
        )

        self.metadata_template_container_1 = MetadataTemplateContainer.objects.create(
            name="Project metadata template container 1",
            assigned_to_content_type=self.project_1.get_content_type(),
            assigned_to_object_id=self.project_1.pk,
        )

        self.metadata_template_1 = MetadataTemplate.objects.create(
            metadata_template_container=self.metadata_template_container_1,
        )

    def test_read_metadata_templates_list(self, client):
        """
        Ensure we can read the metadata templates list.
        """
        url = reverse("metadata-template-list")

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

    def test_search_metadata_template_versions_by_container_name(self, client):
        """
        Ensure unnamed metadata template versions can be found by their display fallback name.
        """
        url = reverse("metadata-template-list")

        response = client.get(
            f"{url}?search={self.metadata_template_container_1.name}",
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert [result["pk"] for result in response.data["results"]] == [str(self.metadata_template_1.pk)]
        assert response.data["results"][0]["name"] == self.metadata_template_container_1.name

    def test_filter_by_metadata_template_container(self, client, initial_users):
        """
        Ensure we can filter the metadata template list by metadata template container.
        """
        url = reverse("metadata-template-list")

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        response = client.get(
            f"{url}?metadata_template_container={self.metadata_template_container_1.pk}",
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        response = client.get(f"{url}?metadata_template_container=invalid_value", format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        metadata_template_container_2 = MetadataTemplateContainer.objects.create(
            name="Folder metadata template container 1",
            assigned_to_content_type=self.project_1.folders.first().get_content_type(),
            assigned_to_object_id=self.project_1.folders.first().pk,
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_2,
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 2

        response = client.get(f"{url}?metadata_template_container={metadata_template_container_2.pk}", format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        metadata_template_container_3 = MetadataTemplateContainer.objects.create(
            name="Global metadata template container 2",
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_3,
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 3

        response = client.get(f"{url}?metadata_template_container={metadata_template_container_3.pk}", format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_3,
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 4

        response = client.get(f"{url}?metadata_template_container={metadata_template_container_3.pk}", format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 2

        auth_user(client, initial_users["user_2"])

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 2

        response = client.get(
            f"{url}?metadata_template_container={self.metadata_template_container_1.pk}",
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0

        response = client.get(f"{url}?metadata_template_container={metadata_template_container_2.pk}", format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0

        response = client.get(f"{url}?metadata_template_container={metadata_template_container_3.pk}", format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 2

    def test_read_metadata_template_details(self, client):
        """
        Ensure we can read the metadata template details.
        """
        url = reverse(
            "metadata-template-detail",
            kwargs={
                "pk": self.metadata_template_1.pk,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["pk"] == str(self.metadata_template_1.pk)

    def test_create_metadata_template(self, client):
        """
        Ensure we can't create a new metadata template on its own.
        """
        url = reverse("metadata-template-list")

        response = client.post(
            url,
            {
                "name": "Metadata template 2",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

    def test_edit_metadata_template_as_project_admin(self, client, initial_users):
        """
        Ensure we can edit a metadata template with project admin permissions only.
        """
        auth_user(client, initial_users["user_2"])

        url = reverse(
            "metadata-template-detail",
            kwargs={
                "pk": self.metadata_template_1.pk,
            },
        )

        response = client.patch(
            url,
            {
                "name": "Name edited",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

        set_request_for_user(initial_users["user_1"])

        project_membership = ProjectMembership.objects.create(
            project=self.project_1,
            member=initial_users["user_2"],
            is_project_admin=False,
            can_create_folders=False,
        )

        auth_user(client, initial_users["user_2"])

        response = client.patch(
            url,
            {
                "name": "Name edited",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        set_request_for_user(initial_users["user_1"])

        project_membership.is_project_admin = True
        project_membership.save()

        auth_user(client, initial_users["user_2"])

        response = client.patch(
            url,
            {
                "name": "Name edited",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Name edited"

    def test_change_metadata_template_with_metadata_template_fields(self, client):
        """
        Ensure we can't update a metadata template and replace template fields with a single request.
        """
        assert MetadataTemplate.objects.count() == 1
        assert MetadataTemplateField.objects.count() == 0

        metadata_template_field_1 = MetadataTemplateField.objects.create(
            metadata_template=self.metadata_template_1,
            custom_key="custom_key_1",
            field_type=MetadataFieldType.TEXT,
            mandatory=True,
        )
        metadata_template_field_1.set_value("custom value")

        MetadataTemplateField.objects.create(
            metadata_template=self.metadata_template_1,
            custom_key="custom_key_2",
            field_type=MetadataFieldType.TEXT,
            mandatory=False,
        )

        assert MetadataTemplateField.objects.count() == 2

        metadata_template_field_1 = MetadataTemplateField.objects.get(custom_key="custom_key_1")
        assert metadata_template_field_1.custom_key == "custom_key_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.TEXT
        assert metadata_template_field_1.get_value() == "custom value"
        assert metadata_template_field_1.mandatory is True

        metadata_template_field_2 = MetadataTemplateField.objects.get(custom_key="custom_key_2")
        assert metadata_template_field_2.custom_key == "custom_key_2"
        assert metadata_template_field_2.field_type == MetadataFieldType.TEXT
        assert metadata_template_field_2.get_value() is None
        assert metadata_template_field_2.mandatory is False

        url = reverse(
            "metadata-template-detail",
            kwargs={
                "pk": self.metadata_template_1.pk,
            },
        )

        name = "Metadata template 1 edit 1"
        response = client.patch(
            url,
            {
                "name": name,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == name
        assert MetadataTemplate.objects.count() == 1
        assert MetadataTemplateField.objects.count() == 2

        metadata_template_field_1 = MetadataTemplateField.objects.get(custom_key="custom_key_1")
        assert metadata_template_field_1.custom_key == "custom_key_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.TEXT
        assert metadata_template_field_1.get_value() == "custom value"
        assert metadata_template_field_1.mandatory is True

        metadata_template_field_2 = MetadataTemplateField.objects.get(custom_key="custom_key_2")
        assert metadata_template_field_2.custom_key == "custom_key_2"
        assert metadata_template_field_2.field_type == MetadataFieldType.TEXT
        assert metadata_template_field_2.get_value() is None
        assert metadata_template_field_2.mandatory is False

        name = "Metadata template 1 edit 2"
        response = client.patch(
            url,
            {
                "name": name,
                "metadata_template_fields": [],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == name
        assert MetadataTemplate.objects.count() == 1
        assert MetadataTemplateField.objects.count() == 2

        metadata_template_field_1 = MetadataTemplateField.objects.get(custom_key="custom_key_1")
        assert metadata_template_field_1.custom_key == "custom_key_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.TEXT
        assert metadata_template_field_1.get_value() == "custom value"
        assert metadata_template_field_1.mandatory is True

        metadata_template_field_2 = MetadataTemplateField.objects.get(custom_key="custom_key_2")
        assert metadata_template_field_2.custom_key == "custom_key_2"
        assert metadata_template_field_2.field_type == MetadataFieldType.TEXT
        assert metadata_template_field_2.get_value() is None
        assert metadata_template_field_2.mandatory is False

        name = "Metadata template 1 edit 3"
        response = client.patch(
            url,
            {
                "name": name,
                "metadata_template_fields": [
                    {
                        "custom_key": "custom_key_1",
                        "value": "custom value",
                        "mandatory": True,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == name
        assert MetadataTemplate.objects.count() == 1
        assert MetadataTemplateField.objects.count() == 2

        metadata_template_field_1 = MetadataTemplateField.objects.get(custom_key="custom_key_1")
        assert metadata_template_field_1.custom_key == "custom_key_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.TEXT
        assert metadata_template_field_1.get_value() == "custom value"
        assert metadata_template_field_1.mandatory is True

        metadata_template_field_2 = MetadataTemplateField.objects.get(custom_key="custom_key_2")
        assert metadata_template_field_2.custom_key == "custom_key_2"
        assert metadata_template_field_2.field_type == MetadataFieldType.TEXT
        assert metadata_template_field_2.get_value() is None
        assert metadata_template_field_2.mandatory is False

        response = client.patch(
            url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == name
        assert MetadataTemplate.objects.count() == 1
        assert MetadataTemplateField.objects.count() == 2

        metadata_template_field_1 = MetadataTemplateField.objects.get(custom_key="custom_key_1")
        assert metadata_template_field_1.custom_key == "custom_key_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.TEXT
        assert metadata_template_field_1.get_value() == "custom value"
        assert metadata_template_field_1.mandatory is True

        metadata_template_field_2 = MetadataTemplateField.objects.get(custom_key="custom_key_2")
        assert metadata_template_field_2.custom_key == "custom_key_2"
        assert metadata_template_field_2.field_type == MetadataFieldType.TEXT
        assert metadata_template_field_2.get_value() is None
        assert metadata_template_field_2.mandatory is False

    def test_metadata_template_assigned_to_content_object_name(self, client, initial_users):
        """
        Ensure we get the correct name of the assigned content object.
        """
        url = reverse("metadata-template-container-list")

        response = client.post(
            url,
            {
                "name": "Metadata template container 2",
                "assigned_to_content_type": get_content_type_for_object(self.project_1.get_content_type()),
                "assigned_to_object_id": self.project_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        url_details = reverse(
            "metadata-template-detail",
            kwargs={
                "pk": response.data["latest_metadata_template"]["pk"],
            },
        )
        response = client.get(url_details, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template_container"]["assigned_to_content_object_name"] == self.project_1.name

        response = client.post(
            url,
            {
                "name": "Metadata template container 3",
                "assigned_to_content_type": get_content_type_for_object(
                    self.project_1.folders.first().get_content_type(),
                ),
                "assigned_to_object_id": self.project_1.folders.first().pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        url_details = reverse(
            "metadata-template-detail",
            kwargs={
                "pk": response.data["latest_metadata_template"]["pk"],
            },
        )
        response = client.get(url_details, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert (
            response.data["metadata_template_container"]["assigned_to_content_object_name"]
            == self.project_1.folders.first().name
        )

        response = client.post(
            url,
            {
                "name": "Metadata template container 4",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        url_details = reverse(
            "metadata-template-detail",
            kwargs={
                "pk": response.data["latest_metadata_template"]["pk"],
            },
        )
        response = client.get(url_details, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template_container"]["assigned_to_content_object_name"] is None

    def test_metadata_template_version_diff(self, client, initial_users):
        """
        Ensure we have a correct diff between two versions of a metadata template.
        """
        url = reverse("metadata-template-container-list")

        response = client.post(
            url,
            {
                "name": "Metadata template container 2",
                "assigned_to_content_type": get_content_type_for_object(self.project_1.get_content_type()),
                "assigned_to_object_id": self.project_1.pk,
                "metadata_template_fields": [
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
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        metadata_template_1 = response.data["latest_metadata_template"]

        metadata_template_1_field_1 = MetadataTemplateField.objects.get(
            metadata_template=metadata_template_1["pk"],
            custom_key="custom_key_1",
        )

        metadata_template_1_field_2 = MetadataTemplateField.objects.get(
            metadata_template=metadata_template_1["pk"],
            custom_key="custom_key_2",
        )

        url_container = reverse(
            "metadata-template-container-detail",
            kwargs={
                "pk": response.data["pk"],
            },
        )
        url_version = f"{url_container}version/"

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK

        url_template_1 = reverse(
            "metadata-template-detail",
            kwargs={
                "pk": metadata_template_1["pk"],
            },
        )

        response = client.get(url_template_1, format="json")
        assert response.status_code == status.HTTP_200_OK

        response = client.get(f"{url_template_1}diff/", format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        response = client.get(f"{url_template_1}diff/?compare=test", format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        response = client.get(f"{url_template_1}diff/?compare=85bc7d2f-e712-40c7-b39e-fad2839fa758", format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND

        response = client.get(f"{url_template_1}diff/?compare={metadata_template_1['pk']}", format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        response = client.post(
            url_version,
            {
                "metadata_template_fields": [
                    {
                        "custom_key": "custom_key_1",
                        "value": "custom value",
                        "mandatory": True,
                        "previous_version_field": metadata_template_1_field_1.pk,
                    },
                    {
                        "custom_key": "custom_key_2",
                        "value": "custom value 2",
                        "mandatory": False,
                        "previous_version_field": metadata_template_1_field_2.pk,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        metadata_template_2 = response.data

        metadata_template_2_field_1 = MetadataTemplateField.objects.get(
            metadata_template=metadata_template_2["pk"],
            custom_key="custom_key_1",
        )

        metadata_template_2_field_2 = MetadataTemplateField.objects.get(
            metadata_template=metadata_template_2["pk"],
            custom_key="custom_key_2",
        )

        url_template_2 = reverse(
            "metadata-template-detail",
            kwargs={
                "pk": metadata_template_2["pk"],
            },
        )

        response = client.get(url_template_2, format="json")
        assert response.status_code == status.HTTP_200_OK

        response = client.get(f"{url_template_2}diff/?compare={metadata_template_1['pk']}", format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["version"]["old"]["pk"] == metadata_template_1["pk"]
        assert response.data["version"]["new"]["pk"] == metadata_template_2["pk"]
        assert len(response.data["fields"]) == 1
        assert response.data["fields"][0]["old"]["pk"] == str(metadata_template_1_field_2.pk)
        assert response.data["fields"][0]["old"]["value"] == metadata_template_1_field_2.get_value()
        assert response.data["fields"][0]["new"]["pk"] == str(metadata_template_2_field_2.pk)
        assert response.data["fields"][0]["new"]["value"] == metadata_template_2_field_2.get_value()
        assert response.data["fields"][0]["old"]["value"] != response.data["fields"][0]["new"]["value"]

        response = client.get(f"{url_template_1}diff/?compare={metadata_template_2['pk']}", format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["version"]["old"]["pk"] == metadata_template_1["pk"]
        assert response.data["version"]["new"]["pk"] == metadata_template_2["pk"]
        assert len(response.data["fields"]) == 1
        assert response.data["fields"][0]["old"]["pk"] == str(metadata_template_1_field_2.pk)
        assert response.data["fields"][0]["old"]["value"] == metadata_template_1_field_2.get_value()
        assert response.data["fields"][0]["new"]["pk"] == str(metadata_template_2_field_2.pk)
        assert response.data["fields"][0]["new"]["value"] == metadata_template_2_field_2.get_value()
        assert response.data["fields"][0]["old"]["value"] != response.data["fields"][0]["new"]["value"]

        response = client.post(
            url_version,
            {
                "metadata_template_fields": [
                    {
                        "custom_key": "custom_key_3",
                        "value": "custom value",
                        "mandatory": True,
                    },
                    {
                        "custom_key": "custom_key_4",
                        "mandatory": False,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        metadata_template_3 = response.data

        metadata_template_3_field_1 = MetadataTemplateField.objects.get(
            metadata_template=metadata_template_3["pk"],
            custom_key="custom_key_3",
        )

        metadata_template_3_field_2 = MetadataTemplateField.objects.get(
            metadata_template=metadata_template_3["pk"],
            custom_key="custom_key_4",
        )

        url_template_3 = reverse(
            "metadata-template-detail",
            kwargs={
                "pk": metadata_template_3["pk"],
            },
        )

        response = client.get(url_template_3, format="json")
        assert response.status_code == status.HTTP_200_OK

        response = client.get(f"{url_template_3}diff/?compare={metadata_template_2['pk']}", format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["version"]["old"]["pk"] == metadata_template_2["pk"]
        assert response.data["version"]["new"]["pk"] == metadata_template_3["pk"]
        assert len(response.data["fields"]) == 4
        assert response.data["fields"][0]["old"] is None
        assert response.data["fields"][0]["new"]["pk"] == str(metadata_template_3_field_1.pk)
        assert response.data["fields"][0]["new"]["value"] == metadata_template_3_field_1.get_value()
        assert response.data["fields"][1]["old"] is None
        assert response.data["fields"][1]["new"]["pk"] == str(metadata_template_3_field_2.pk)
        assert response.data["fields"][1]["new"]["value"] == metadata_template_3_field_2.get_value()
        assert response.data["fields"][2]["old"]["pk"] == str(metadata_template_2_field_1.pk)
        assert response.data["fields"][2]["old"]["value"] == metadata_template_2_field_1.get_value()
        assert response.data["fields"][2]["new"] is None
        assert response.data["fields"][3]["old"]["pk"] == str(metadata_template_2_field_2.pk)
        assert response.data["fields"][3]["old"]["value"] == metadata_template_2_field_2.get_value()
        assert response.data["fields"][3]["new"] is None

        response = client.get(f"{url_template_1}diff/?compare={metadata_template_3['pk']}", format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["version"]["old"]["pk"] == metadata_template_1["pk"]
        assert response.data["version"]["new"]["pk"] == metadata_template_3["pk"]
        assert len(response.data["fields"]) == 4
        assert response.data["fields"][0]["old"] is None
        assert response.data["fields"][0]["new"]["pk"] == str(metadata_template_3_field_1.pk)
        assert response.data["fields"][0]["new"]["value"] == metadata_template_3_field_1.get_value()
        assert response.data["fields"][1]["old"] is None
        assert response.data["fields"][1]["new"]["pk"] == str(metadata_template_3_field_2.pk)
        assert response.data["fields"][1]["new"]["value"] == metadata_template_3_field_2.get_value()
        assert response.data["fields"][2]["old"]["pk"] == str(metadata_template_1_field_1.pk)
        assert response.data["fields"][2]["old"]["value"] == metadata_template_1_field_1.get_value()
        assert response.data["fields"][2]["new"] is None
        assert response.data["fields"][3]["old"]["pk"] == str(metadata_template_1_field_2.pk)
        assert response.data["fields"][3]["old"]["value"] == metadata_template_1_field_2.get_value()
        assert response.data["fields"][3]["new"] is None

        response = client.post(
            url_version,
            {
                "metadata_template_fields": [
                    {
                        "custom_key": "custom_key_3",
                        "value": "custom value",
                        "mandatory": False,
                        "previous_version_field": metadata_template_3_field_1.pk,
                        "order": 0,
                    },
                    {
                        "custom_key": "custom_key_5",
                        "mandatory": False,
                        "previous_version_field": metadata_template_3_field_2.pk,
                        "order": 1,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        metadata_template_4 = response.data

        metadata_template_4_field_1 = MetadataTemplateField.objects.get(
            metadata_template=metadata_template_4["pk"],
            custom_key="custom_key_3",
        )

        metadata_template_4_field_2 = MetadataTemplateField.objects.get(
            metadata_template=metadata_template_4["pk"],
            custom_key="custom_key_5",
        )

        url_template_4 = reverse(
            "metadata-template-detail",
            kwargs={
                "pk": metadata_template_4["pk"],
            },
        )

        response = client.get(url_template_4, format="json")
        assert response.status_code == status.HTTP_200_OK

        response = client.get(f"{url_template_4}diff/?compare={metadata_template_3['pk']}", format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["version"]["old"]["pk"] == metadata_template_3["pk"]
        assert response.data["version"]["new"]["pk"] == metadata_template_4["pk"]
        assert len(response.data["fields"]) == 2
        assert response.data["fields"][0]["old"]["pk"] == str(metadata_template_3_field_1.pk)
        assert response.data["fields"][0]["old"]["value"] == metadata_template_3_field_1.get_value()
        assert response.data["fields"][0]["old"]["custom_key"] == metadata_template_3_field_1.custom_key
        assert response.data["fields"][0]["old"]["mandatory"] is metadata_template_3_field_1.mandatory
        assert response.data["fields"][0]["old"]["order"] == metadata_template_3_field_1.order
        assert response.data["fields"][0]["new"]["pk"] == str(metadata_template_4_field_1.pk)
        assert response.data["fields"][0]["new"]["value"] == metadata_template_4_field_1.get_value()
        assert response.data["fields"][0]["new"]["custom_key"] == metadata_template_4_field_1.custom_key
        assert response.data["fields"][0]["new"]["mandatory"] is metadata_template_4_field_1.mandatory
        assert response.data["fields"][0]["old"]["mandatory"] != response.data["fields"][0]["new"]["mandatory"]
        assert response.data["fields"][0]["new"]["order"] == metadata_template_4_field_1.order
        assert response.data["fields"][1]["old"]["pk"] == str(metadata_template_3_field_2.pk)
        assert response.data["fields"][1]["old"]["value"] == metadata_template_3_field_2.get_value()
        assert response.data["fields"][1]["old"]["custom_key"] == metadata_template_3_field_2.custom_key
        assert response.data["fields"][1]["old"]["mandatory"] is metadata_template_3_field_2.mandatory
        assert response.data["fields"][1]["old"]["order"] == metadata_template_3_field_2.order
        assert response.data["fields"][1]["new"]["pk"] == str(metadata_template_4_field_2.pk)
        assert response.data["fields"][1]["new"]["value"] == metadata_template_4_field_2.get_value()
        assert response.data["fields"][1]["new"]["custom_key"] == metadata_template_4_field_2.custom_key
        assert response.data["fields"][1]["new"]["mandatory"] is metadata_template_4_field_2.mandatory
        assert response.data["fields"][1]["old"]["custom_key"] != response.data["fields"][1]["new"]["custom_key"]
        assert response.data["fields"][1]["new"]["order"] == metadata_template_4_field_2.order

        response = client.post(
            url_version,
            {
                "metadata_template_fields": [
                    {
                        "custom_key": "custom_key_3",
                        "value": "custom value",
                        "mandatory": False,
                        "previous_version_field": metadata_template_4_field_1.pk,
                        "order": 2,
                    },
                    {
                        "custom_key": "custom_key_5",
                        "mandatory": False,
                        "previous_version_field": metadata_template_4_field_2.pk,
                        "order": 1,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        metadata_template_5 = response.data

        metadata_template_5_field_1 = MetadataTemplateField.objects.get(
            metadata_template=metadata_template_5["pk"],
            custom_key="custom_key_3",
        )

        url_template_5 = reverse(
            "metadata-template-detail",
            kwargs={
                "pk": metadata_template_5["pk"],
            },
        )

        response = client.get(url_template_5, format="json")
        assert response.status_code == status.HTTP_200_OK

        response = client.get(f"{url_template_5}diff/?compare={metadata_template_4['pk']}", format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["version"]["old"]["pk"] == metadata_template_4["pk"]
        assert response.data["version"]["new"]["pk"] == metadata_template_5["pk"]
        assert len(response.data["fields"]) == 1
        assert response.data["fields"][0]["old"]["pk"] == str(metadata_template_4_field_1.pk)
        assert response.data["fields"][0]["old"]["value"] == metadata_template_4_field_1.get_value()
        assert response.data["fields"][0]["old"]["custom_key"] == metadata_template_4_field_1.custom_key
        assert response.data["fields"][0]["old"]["mandatory"] is metadata_template_4_field_1.mandatory
        assert response.data["fields"][0]["old"]["order"] == metadata_template_4_field_1.order
        assert response.data["fields"][0]["new"]["pk"] == str(metadata_template_5_field_1.pk)
        assert response.data["fields"][0]["new"]["value"] == metadata_template_5_field_1.get_value()
        assert response.data["fields"][0]["new"]["custom_key"] == metadata_template_5_field_1.custom_key
        assert response.data["fields"][0]["new"]["mandatory"] is metadata_template_5_field_1.mandatory
        assert response.data["fields"][0]["old"]["order"] != response.data["fields"][0]["new"]["order"]
        assert response.data["fields"][0]["new"]["order"] == metadata_template_5_field_1.order

    def test_active_metadata_template_duplicates(self, client, initial_users):
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
        url = reverse(
            "project-detail",
            kwargs={
                "pk": project_1.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(metadata_template_1.pk)

        url = reverse(
            "folder-detail",
            kwargs={
                "pk": project_1_folder_1.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        url = reverse(
            "folder-detail",
            kwargs={
                "pk": project_1_folder_2.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": project_1_folder_1_dataset_1.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": project_1_folder_1_dataset_2.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": project_1_folder_2_dataset_1.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": project_1_folder_2_dataset_2.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        url = reverse(
            "project-detail",
            kwargs={
                "pk": project_2.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(metadata_template_1.pk)

        url = reverse(
            "folder-detail",
            kwargs={
                "pk": project_2_folder_1.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        url = reverse(
            "folder-detail",
            kwargs={
                "pk": project_2_folder_2.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": project_2_folder_1_dataset_1.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": project_2_folder_1_dataset_2.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": project_2_folder_2_dataset_1.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": project_2_folder_2_dataset_2.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

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

        # # Test case 2: folders have the advantage
        url = reverse(
            "folder-detail",
            kwargs={
                "pk": project_1_folder_1.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(metadata_template_1.pk)

        url = reverse(
            "folder-detail",
            kwargs={
                "pk": project_1_folder_2.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(metadata_template_1.pk)

        url = reverse(
            "project-detail",
            kwargs={
                "pk": project_1.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": project_1_folder_1_dataset_1.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": project_1_folder_1_dataset_2.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": project_1_folder_2_dataset_1.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": project_1_folder_2_dataset_2.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        url = reverse(
            "folder-detail",
            kwargs={
                "pk": project_2_folder_1.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(metadata_template_1.pk)

        url = reverse(
            "folder-detail",
            kwargs={
                "pk": project_2_folder_2.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(metadata_template_1.pk)

        url = reverse(
            "project-detail",
            kwargs={
                "pk": project_2.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": project_2_folder_1_dataset_1.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": project_2_folder_1_dataset_2.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": project_2_folder_2_dataset_1.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": project_2_folder_2_dataset_2.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

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
        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": project_1_folder_1_dataset_1.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(metadata_template_1.pk)

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": project_1_folder_1_dataset_2.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(metadata_template_1.pk)

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": project_1_folder_2_dataset_1.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(metadata_template_1.pk)

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": project_1_folder_2_dataset_2.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(metadata_template_1.pk)

        url = reverse(
            "project-detail",
            kwargs={
                "pk": project_1.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        url = reverse(
            "folder-detail",
            kwargs={
                "pk": project_1_folder_1.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        url = reverse(
            "folder-detail",
            kwargs={
                "pk": project_1_folder_2.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": project_2_folder_1_dataset_1.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(metadata_template_1.pk)

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": project_2_folder_1_dataset_2.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(metadata_template_1.pk)

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": project_2_folder_2_dataset_1.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(metadata_template_1.pk)

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": project_2_folder_2_dataset_2.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(metadata_template_1.pk)

        url = reverse(
            "project-detail",
            kwargs={
                "pk": project_2.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        url = reverse(
            "folder-detail",
            kwargs={
                "pk": project_2_folder_1.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        url = reverse(
            "folder-detail",
            kwargs={
                "pk": project_2_folder_2.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

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
        url = reverse(
            "project-detail",
            kwargs={
                "pk": project_1.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(metadata_template_1.pk)

        url = reverse(
            "folder-detail",
            kwargs={
                "pk": project_1_folder_1.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_2.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(metadata_template_2.pk)

        url = reverse(
            "folder-detail",
            kwargs={
                "pk": project_1_folder_2.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_2.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(metadata_template_2.pk)

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": project_1_folder_1_dataset_1.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_3.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(metadata_template_3.pk)

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": project_1_folder_1_dataset_2.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_3.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(metadata_template_3.pk)

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": project_1_folder_2_dataset_1.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_3.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(metadata_template_3.pk)

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": project_1_folder_2_dataset_2.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_3.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(metadata_template_3.pk)

        url = reverse(
            "project-detail",
            kwargs={
                "pk": project_2.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(metadata_template_1.pk)

        url = reverse(
            "folder-detail",
            kwargs={
                "pk": project_2_folder_1.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_2.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(metadata_template_2.pk)

        url = reverse(
            "folder-detail",
            kwargs={
                "pk": project_2_folder_2.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_2.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(metadata_template_2.pk)

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": project_2_folder_1_dataset_1.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_3.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(metadata_template_3.pk)

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": project_2_folder_1_dataset_2.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_3.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(metadata_template_3.pk)

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": project_2_folder_2_dataset_1.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_3.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(metadata_template_3.pk)

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": project_2_folder_2_dataset_2.pk,
            },
        )

        response = client.patch(
            url,
            {
                "metadata_template": metadata_template_3.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(metadata_template_3.pk)


@pytest.mark.django_db
class TestMetadataTemplateContainerLockStatusMixin:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.metadata_template_container_1 = MetadataTemplateContainer.objects.create(
            name="Metadata template container 1",
        )

    def test_lock(self, client, initial_users):
        """
        Ensure we have an expected lock behavior.
        """
        set_request_for_user(initial_users["user_1"])

        url = reverse(
            "metadata-template-container-detail",
            kwargs={
                "pk": self.metadata_template_container_1.pk,
            },
        )

        lock_url = f"{url}lock/"
        unlock_url = f"{url}unlock/"
        status_url = f"{url}status/"

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None

        response = client.patch(
            url,
            {
                "name": "Metadata template container 1",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Metadata template container 1"

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None

        response = client.post(
            lock_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is True
        assert response.data["locked_by"]["pk"] == initial_users["user_1"].pk
        assert response.data["locked_at"] is not None

        last_lock_time = response.data["locked_at"]

        response = client.post(
            lock_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is True
        assert response.data["locked_by"]["pk"] == initial_users["user_1"].pk
        assert response.data["locked_at"] is not None
        assert response.data["locked_at"] > last_lock_time

        response = client.patch(
            url,
            {
                "name": "Metadata template container 1 - Edit #1",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Metadata template container 1 - Edit #1"

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None

        response = client.post(
            lock_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is True
        assert response.data["locked_by"]["pk"] == initial_users["user_1"].pk
        assert response.data["locked_at"] is not None

        response = client.post(
            unlock_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None

        auth_user(client, initial_users["user_2"])

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK

        response = client.patch(
            url,
            {
                "name": "Metadata template container 1 - Edit #3",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        initial_users["user_2"].is_global_metadata_template_admin = True
        initial_users["user_2"].save()

        response = client.patch(
            url,
            {
                "name": "Metadata template container 1 - Edit #3",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Metadata template container 1 - Edit #3"

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None

        response = client.post(
            lock_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is True
        assert response.data["locked_by"]["pk"] == initial_users["user_2"].pk
        assert response.data["locked_at"] is not None

        auth_user(client, initial_users["user_1"])

        response = client.post(
            unlock_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        response = client.post(
            lock_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        auth_user(client, initial_users["user_2"])

        response = client.post(
            unlock_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None

        auth_user(client, initial_users["user_1"])

        response = client.post(
            lock_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is True
        assert response.data["locked_by"]["pk"] == initial_users["user_1"].pk
        assert response.data["locked_at"] is not None

        response = client.post(
            unlock_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None


@pytest.mark.django_db
class TestMetadataTemplateLockStatusMixin:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.metadata_template_container_1 = MetadataTemplateContainer.objects.create(
            name="Global metadata template container 1",
        )

        self.metadata_template_1 = MetadataTemplate.objects.create(
            metadata_template_container=self.metadata_template_container_1,
        )

    def test_lock(self, client, initial_users):
        """
        Ensure we have an expected lock behavior.
        """
        set_request_for_user(initial_users["user_1"])

        url = reverse(
            "metadata-template-container-detail",
            kwargs={
                "pk": self.metadata_template_container_1.pk,
            },
        )

        lock_url = f"{url}lock/"
        unlock_url = f"{url}unlock/"
        status_url = f"{url}status/"

        metadata_template_url = reverse(
            "metadata-template-detail",
            kwargs={
                "pk": self.metadata_template_1.pk,
            },
        )

        metadata_template_status_url = f"{metadata_template_url}status/"

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None

        response = client.get(metadata_template_status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None

        response = client.patch(
            url,
            {
                "name": "Metadata template container 1",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Metadata template container 1"

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None

        response = client.get(metadata_template_status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None

        response = client.post(
            lock_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is True
        assert response.data["locked_by"]["pk"] == initial_users["user_1"].pk
        assert response.data["locked_at"] is not None

        response = client.get(metadata_template_status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is True
        assert response.data["locked_by"]["pk"] == initial_users["user_1"].pk
        assert response.data["locked_at"] is not None

        last_lock_time = response.data["locked_at"]

        response = client.post(
            lock_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is True
        assert response.data["locked_by"]["pk"] == initial_users["user_1"].pk
        assert response.data["locked_at"] is not None
        assert response.data["locked_at"] > last_lock_time

        response = client.get(metadata_template_status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is True
        assert response.data["locked_by"]["pk"] == initial_users["user_1"].pk
        assert response.data["locked_at"] is not None
        assert response.data["locked_at"] > last_lock_time

        response = client.patch(
            url,
            {
                "name": "Metadata template container 1 - Edit #1",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Metadata template container 1 - Edit #1"

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None

        response = client.get(metadata_template_status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None

        response = client.post(
            lock_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is True
        assert response.data["locked_by"]["pk"] == initial_users["user_1"].pk
        assert response.data["locked_at"] is not None

        response = client.get(metadata_template_status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is True
        assert response.data["locked_by"]["pk"] == initial_users["user_1"].pk
        assert response.data["locked_at"] is not None

        response = client.post(
            unlock_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None

        response = client.get(metadata_template_status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None

        auth_user(client, initial_users["user_2"])

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK

        response = client.get(metadata_template_url, format="json")
        assert response.status_code == status.HTTP_200_OK

        response = client.patch(
            url,
            {
                "name": "Metadata template container 1 - Edit #3",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        initial_users["user_2"].is_global_metadata_template_admin = True
        initial_users["user_2"].save()

        response = client.patch(
            url,
            {
                "name": "Metadata template container 1 - Edit #3",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Metadata template container 1 - Edit #3"

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None

        response = client.get(metadata_template_status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None

        response = client.post(
            lock_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is True
        assert response.data["locked_by"]["pk"] == initial_users["user_2"].pk
        assert response.data["locked_at"] is not None

        response = client.get(metadata_template_status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is True
        assert response.data["locked_by"]["pk"] == initial_users["user_2"].pk
        assert response.data["locked_at"] is not None

        auth_user(client, initial_users["user_1"])

        response = client.post(
            unlock_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        response = client.post(
            lock_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        auth_user(client, initial_users["user_2"])

        response = client.post(
            unlock_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None

        response = client.get(metadata_template_status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None

        auth_user(client, initial_users["user_1"])

        response = client.post(
            lock_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is True
        assert response.data["locked_by"]["pk"] == initial_users["user_1"].pk
        assert response.data["locked_at"] is not None

        response = client.get(metadata_template_status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is True
        assert response.data["locked_by"]["pk"] == initial_users["user_1"].pk
        assert response.data["locked_at"] is not None

        response = client.post(
            unlock_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None

        response = client.get(metadata_template_status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None
