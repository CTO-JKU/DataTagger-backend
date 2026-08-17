import datetime
import os
import uuid
from datetime import timedelta

import pytest
from conftest import auth_user
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from fdm.core.helpers import get_content_type_for_object, set_request_for_user
from fdm.dbsettings.functions import get_dbsettings_value
from fdm.file_parser.models import FileParser
from fdm.folders.models import Folder, FolderPermission
from fdm.metadata.enums import MetadataFieldType
from fdm.metadata.helpers import set_metadata
from fdm.metadata.models import Metadata, MetadataTemplate, MetadataTemplateContainer, MetadataTemplateField
from fdm.projects.models import Project, ProjectMembership
from fdm.storages.models import DynamicStorage
from fdm.uploads.helpers import create_uploads_version_with_new_file_for_dataset
from fdm.uploads.models import UploadsDataset, UploadsVersion, UploadsVersionFile
from fdm.uploads.tasks import remove_expired_dataset_drafts

sample_file = SimpleUploadedFile(
    "file.jpg",
    b"",
    content_type="image/jpg",
)

sample_file2 = SimpleUploadedFile(
    "file2.jpg",
    b"",
    content_type="image/jpg",
)


@pytest.mark.django_db
class TestUploadsDatasetAPI:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        storage_1 = DynamicStorage.objects.get(
            default=True,
        )

        self.project_1 = Project.objects.create(
            name="Project 1",
        )

        self.project_2 = Project.objects.create(
            name="Project 2",
        )

        self.folder_1 = self.project_1.folders.first()
        self.folder_1.storage = storage_1
        self.folder_1.save()

        self.folder_2 = Folder.objects.create(
            name="Folder 2",
            project=self.project_1,
        )

        self.folder_3 = Folder.objects.create(
            name="Folder 3",
            project=self.project_1,
        )

        self.uploads_dataset_1 = UploadsDataset.objects.create(
            name="Dataset 1",
            folder=self.folder_1,
            publication_date=timezone.now(),
            deletable_until_date=timezone.now()
            + timezone.timedelta(minutes=int(get_dbsettings_value("DATASET_DELETABLE_WITHIN_THRESHOLD"))),
        )

        self.uploads_dataset_2 = UploadsDataset.objects.create(
            name="Dataset 2",
            folder=self.folder_1,
            publication_date=timezone.now(),
            deletable_until_date=timezone.now()
            + timezone.timedelta(minutes=int(get_dbsettings_value("DATASET_DELETABLE_WITHIN_THRESHOLD"))),
        )

        self.uploads_dataset_3 = UploadsDataset.objects.create(
            name="Dataset 3",
            folder=self.folder_2,
            publication_date=timezone.now(),
            deletable_until_date=timezone.now()
            + timezone.timedelta(minutes=int(get_dbsettings_value("DATASET_DELETABLE_WITHIN_THRESHOLD"))),
        )

        self.uploads_dataset_4 = UploadsDataset.objects.create(
            name="Dataset 4",
            folder=self.folder_3,
            publication_date=timezone.now(),
            deletable_until_date=timezone.now()
            + timezone.timedelta(minutes=int(get_dbsettings_value("DATASET_DELETABLE_WITHIN_THRESHOLD"))),
        )

        self.uploads_dataset_5 = UploadsDataset.objects.create(
            name="Dataset 5",
        )

        self.metadata_template_container_1 = MetadataTemplateContainer.objects.create(
            name="Project metadata template container 1",
            assigned_to_content_type=self.project_1.get_content_type(),
            assigned_to_object_id=self.project_1.pk,
        )

        self.metadata_template_1 = MetadataTemplate.objects.create(
            metadata_template_container=self.metadata_template_container_1,
        )

        self.metadata_template_field_1 = MetadataTemplateField.objects.create(
            metadata_template=self.metadata_template_1,
            custom_key="metadata_template_field_1",
            field_type=MetadataFieldType.TEXT,
            mandatory=False,
        )
        self.metadata_template_field_1.set_value("Text")

    def _create_assigned_metadata_template(self, assigned_to_object, name, field_specs):
        metadata_template_container = MetadataTemplateContainer.objects.create(
            name=name,
            assigned_to_content_type=assigned_to_object.get_content_type(),
            assigned_to_object_id=assigned_to_object.pk,
        )
        metadata_template = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container,
        )

        metadata_template_fields = {}
        for index, field_spec in enumerate(field_specs):
            metadata_template_field = MetadataTemplateField.objects.create(
                metadata_template=metadata_template,
                custom_key=field_spec["custom_key"],
                field_type=field_spec["field_type"],
                config=field_spec.get("config", {}),
                mandatory=field_spec.get("mandatory", False),
                previous_version_field=field_spec.get("previous_version_field"),
                order=index,
            )
            metadata_template_field.set_value(field_spec.get("value"))
            metadata_template_fields[field_spec["custom_key"]] = metadata_template_field

        return metadata_template, metadata_template_fields

    def _create_bulk_fill_dataset(
        self,
        name,
        folder,
        *,
        published=True,
        linked_templates=None,
        metadata_values_by_field=None,
        dataset_metadata_template=None,
        extra_metadata=None,
    ):
        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )
        dataset = UploadsDataset.objects.create(
            name=name,
            folder=folder,
            publication_date=timezone.now() if published else None,
            metadata_template=dataset_metadata_template,
        )
        uploads_version = UploadsVersion.objects.create(
            dataset=dataset,
            version_file=uploads_version_file,
        )

        for order, (metadata_template_field, value) in enumerate((metadata_values_by_field or {}).items()):
            set_metadata(
                assigned_to_content_type=uploads_version.get_content_type(),
                assigned_to_object_id=uploads_version.pk,
                custom_key=metadata_template_field.custom_key,
                field_type=metadata_template_field.field_type,
                value=value,
                config=metadata_template_field.config,
                metadata_template_field=metadata_template_field,
                order=order,
            )

        order_offset = len(metadata_values_by_field or {})
        for index, metadata in enumerate(extra_metadata or []):
            set_metadata(
                assigned_to_content_type=uploads_version.get_content_type(),
                assigned_to_object_id=uploads_version.pk,
                custom_key=metadata["custom_key"],
                field_type=metadata.get("field_type", MetadataFieldType.TEXT),
                value=metadata.get("value"),
                config=metadata.get("config", {}),
                order=order_offset + index,
            )

        uploads_version.metadata_templates.set(list(linked_templates or []))
        return dataset

    def test_read_uploads_dataset_list(self, client, initial_users):
        """
        Ensure we can read the uploads dataset list.
        """
        url = reverse("uploads-dataset-list")

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1
        assert response.data["results"][0]["metadata_upgrade_status"] == UploadsDataset.Status.FINISHED

        url_filter = f"{url}?folder={self.folder_1.pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 2

        url_filter = f"{url}?folder={self.folder_1.pk}&search={self.uploads_dataset_1.name}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1
        assert response.data["results"][0]["pk"] == str(self.uploads_dataset_1.pk)

        url_filter = f"{url}?folder={self.folder_2.pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        url_filter = f"{url}?folder={self.folder_3.pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        uploads_version_1 = create_uploads_version_with_new_file_for_dataset(
            dataset=self.uploads_dataset_1,
            uploaded_file=sample_file,
        )
        assert uploads_version_1.metadata_is_complete is True

        uploads_version_2 = create_uploads_version_with_new_file_for_dataset(
            dataset=self.uploads_dataset_2,
            uploaded_file=sample_file,
        )
        assert uploads_version_2.metadata_is_complete is True

        url_filter = f"{url}?folder={self.folder_1.pk}&metadata_is_complete=true"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 2

        url_filter = f"{url}?folder={self.folder_1.pk}&metadata_is_complete=false"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0

        uploads_version_2.metadata_is_complete = False
        uploads_version_2.save()
        assert uploads_version_2.metadata_is_complete is False

        url_filter = f"{url}?folder={self.folder_1.pk}&metadata_is_complete=true"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        url_filter = f"{url}?folder={self.folder_1.pk}&metadata_is_complete=false"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

    def test_bulk_fill_metadata_template_fields_returns_inherited_fields_and_skip_reasons(
        self,
        client,
        initial_users,
    ):
        set_request_for_user(initial_users["user_1"])

        project_template, project_fields = self._create_assigned_metadata_template(
            self.project_1,
            "Bulk fill project template",
            [
                {
                    "custom_key": "project_shared_text",
                    "field_type": MetadataFieldType.TEXT,
                },
            ],
        )
        folder_template, folder_fields = self._create_assigned_metadata_template(
            self.folder_1,
            "Bulk fill folder template",
            [
                {
                    "custom_key": "folder_choice",
                    "field_type": MetadataFieldType.SELECTION,
                    "config": {
                        "options": [
                            "Option A",
                            "Option B",
                        ],
                    },
                },
            ],
        )
        dataset_template, dataset_fields = self._create_assigned_metadata_template(
            self.uploads_dataset_1,
            "Bulk fill dataset template",
            [
                {
                    "custom_key": "dataset_only_field",
                    "field_type": MetadataFieldType.TEXT,
                },
            ],
        )

        self.project_1.metadata_template = project_template
        self.project_1.save()
        self.folder_1.metadata_template = folder_template
        self.folder_1.save()

        dataset_1 = self._create_bulk_fill_dataset(
            "Bulk fill eligible 1",
            self.folder_1,
            linked_templates=[
                project_template,
                folder_template,
                dataset_template,
            ],
            metadata_values_by_field={
                project_fields["project_shared_text"]: "Shared",
                folder_fields["folder_choice"]: "Option A",
                dataset_fields["dataset_only_field"]: "Dataset specific",
            },
            dataset_metadata_template=dataset_template,
        )
        dataset_2 = self._create_bulk_fill_dataset(
            "Bulk fill eligible 2",
            self.folder_1,
            linked_templates=[
                project_template,
                folder_template,
            ],
            metadata_values_by_field={
                project_fields["project_shared_text"]: "Shared",
                folder_fields["folder_choice"]: "Option B",
            },
        )
        dataset_other_folder = self._create_bulk_fill_dataset(
            "Bulk fill other folder",
            self.folder_2,
            linked_templates=[
                project_template,
            ],
            metadata_values_by_field={
                project_fields["project_shared_text"]: "Shared",
            },
        )
        dataset_draft = self._create_bulk_fill_dataset(
            "Bulk fill draft",
            self.folder_1,
            published=False,
            linked_templates=[
                project_template,
                folder_template,
            ],
            metadata_values_by_field={
                project_fields["project_shared_text"]: "Shared",
                folder_fields["folder_choice"]: "Option A",
            },
        )

        url = reverse("uploads-dataset-bulk-fill-metadata-template-fields")
        response = client.post(
            url,
            {
                "uploads_datasets": [
                    dataset_1.pk,
                    dataset_2.pk,
                    dataset_other_folder.pk,
                    dataset_draft.pk,
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["skipped_uploads_datasets_count"] == 2
        assert response.data["has_skipped_due_to_pending_template_upgrade"] is False
        assert response.data["eligible_uploads_datasets"] == [
            {
                "pk": str(dataset_1.pk),
                "display_name": dataset_1.display_name,
                "base_version_pk": str(dataset_1.latest_version.pk),
            },
            {
                "pk": str(dataset_2.pk),
                "display_name": dataset_2.display_name,
                "base_version_pk": str(dataset_2.latest_version.pk),
            },
        ]
        assert response.data["skip_reasons"] == [
            {
                "code": "NOT_IN_FOLDER_SCOPE",
                "count": 1,
            },
            {
                "code": "NO_INHERITED_TEMPLATE_FIELDS",
                "count": 1,
            },
        ]

        assert len(response.data["fields"]) == 2
        fields_by_key = {item["custom_key"]: item for item in response.data["fields"]}
        assert "dataset_only_field" not in fields_by_key

        shared_field = fields_by_key["project_shared_text"]
        assert shared_field["pk"] == str(project_fields["project_shared_text"].pk)
        assert shared_field["various_inputs"] is False
        assert shared_field["value"] == "Shared"

        folder_field = fields_by_key["folder_choice"]
        assert folder_field["pk"] == str(folder_fields["folder_choice"].pk)
        assert folder_field["various_inputs"] is True
        assert folder_field["value"] is None

    def test_bulk_fill_metadata_template_fields_returns_and_updates_shared_dataset_template_fields(
        self,
        client,
        initial_users,
    ):
        set_request_for_user(initial_users["user_1"])

        project_template, project_fields = self._create_assigned_metadata_template(
            self.project_1,
            "Bulk fill shared project template",
            [
                {
                    "custom_key": "shared_project_text",
                    "field_type": MetadataFieldType.TEXT,
                },
            ],
        )
        folder_template, folder_fields = self._create_assigned_metadata_template(
            self.folder_1,
            "Bulk fill shared folder template",
            [
                {
                    "custom_key": "shared_folder_text",
                    "field_type": MetadataFieldType.TEXT,
                },
            ],
        )
        dataset_template, dataset_fields = self._create_assigned_metadata_template(
            self.uploads_dataset_1,
            "Bulk fill shared file-level template",
            [
                {
                    "custom_key": "shared_file_level_text",
                    "field_type": MetadataFieldType.TEXT,
                },
            ],
        )

        self.project_1.metadata_template = project_template
        self.project_1.save()
        self.folder_1.metadata_template = folder_template
        self.folder_1.save()

        dataset_1 = self._create_bulk_fill_dataset(
            "Bulk fill shared file-level eligible 1",
            self.folder_1,
            linked_templates=[
                project_template,
                folder_template,
                dataset_template,
            ],
            metadata_values_by_field={
                project_fields["shared_project_text"]: "Project",
                folder_fields["shared_folder_text"]: "Folder",
                dataset_fields["shared_file_level_text"]: "Old file value",
            },
            dataset_metadata_template=dataset_template,
        )
        dataset_2 = self._create_bulk_fill_dataset(
            "Bulk fill shared file-level eligible 2",
            self.folder_1,
            linked_templates=[
                project_template,
                folder_template,
                dataset_template,
            ],
            metadata_values_by_field={
                project_fields["shared_project_text"]: "Project",
                folder_fields["shared_folder_text"]: "Folder",
                dataset_fields["shared_file_level_text"]: "Old file value",
            },
            dataset_metadata_template=dataset_template,
        )

        preflight_url = reverse("uploads-dataset-bulk-fill-metadata-template-fields")
        submit_url = reverse("uploads-dataset-bulk-fill-metadata-template-fields-submit")

        preflight_response = client.post(
            preflight_url,
            {
                "uploads_datasets": [
                    dataset_1.pk,
                    dataset_2.pk,
                ],
            },
            format="json",
        )

        assert preflight_response.status_code == status.HTTP_200_OK
        assert preflight_response.data["warnings"] == []
        fields_by_key = {item["custom_key"]: item for item in preflight_response.data["fields"]}
        assert fields_by_key["shared_project_text"]["pk"] == str(project_fields["shared_project_text"].pk)
        assert fields_by_key["shared_folder_text"]["pk"] == str(folder_fields["shared_folder_text"].pk)
        assert fields_by_key["shared_file_level_text"]["pk"] == str(dataset_fields["shared_file_level_text"].pk)
        assert fields_by_key["shared_file_level_text"]["various_inputs"] is False
        assert fields_by_key["shared_file_level_text"]["value"] == "Old file value"

        response = client.post(
            submit_url,
            {
                "targets": [
                    {
                        "uploads_dataset_pk": item["pk"],
                        "base_version_pk": item["base_version_pk"],
                    }
                    for item in preflight_response.data["eligible_uploads_datasets"]
                ],
                "fields": [
                    {
                        "metadata_template_field_pk": dataset_fields["shared_file_level_text"].pk,
                        "value": "New file value",
                    },
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["updated_uploads_datasets_count"] == 2
        assert response.data["skipped_uploads_datasets_count"] == 0
        assert response.data["skip_reasons"] == []

        for dataset in [dataset_1, dataset_2]:
            dataset.refresh_from_db()
            assert dataset.uploads_versions.count() == 2
            latest_version = dataset.latest_version
            assert (
                latest_version.metadata.get(
                    metadata_template_field=dataset_fields["shared_file_level_text"],
                ).get_value()
                == "New file value"
            )
            assert latest_version.metadata_templates.filter(pk=dataset_template.pk).exists()

    def test_bulk_fill_metadata_template_fields_returns_shared_dataset_template_fields_without_inherited_fields(
        self,
        client,
        initial_users,
    ):
        set_request_for_user(initial_users["user_1"])

        dataset_template, dataset_fields = self._create_assigned_metadata_template(
            self.uploads_dataset_1,
            "Bulk fill shared file-level only template",
            [
                {
                    "custom_key": "file_level_only_text",
                    "field_type": MetadataFieldType.TEXT,
                },
            ],
        )

        dataset_1 = self._create_bulk_fill_dataset(
            "Bulk fill file-level only eligible 1",
            self.folder_1,
            linked_templates=[
                dataset_template,
            ],
            metadata_values_by_field={
                dataset_fields["file_level_only_text"]: "Old file value",
            },
            dataset_metadata_template=dataset_template,
        )
        dataset_2 = self._create_bulk_fill_dataset(
            "Bulk fill file-level only eligible 2",
            self.folder_1,
            linked_templates=[
                dataset_template,
            ],
            metadata_values_by_field={
                dataset_fields["file_level_only_text"]: "Old file value",
            },
            dataset_metadata_template=dataset_template,
        )

        url = reverse("uploads-dataset-bulk-fill-metadata-template-fields")
        response = client.post(
            url,
            {
                "uploads_datasets": [
                    dataset_1.pk,
                    dataset_2.pk,
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["warnings"] == []
        assert response.data["skipped_uploads_datasets_count"] == 0
        assert response.data["skip_reasons"] == []
        assert response.data["eligible_uploads_datasets"] == [
            {
                "pk": str(dataset_1.pk),
                "display_name": dataset_1.display_name,
                "base_version_pk": str(dataset_1.latest_version.pk),
            },
            {
                "pk": str(dataset_2.pk),
                "display_name": dataset_2.display_name,
                "base_version_pk": str(dataset_2.latest_version.pk),
            },
        ]
        assert [field["custom_key"] for field in response.data["fields"]] == ["file_level_only_text"]
        assert response.data["fields"][0]["pk"] == str(dataset_fields["file_level_only_text"].pk)

    def test_bulk_fill_metadata_template_fields_warns_and_rejects_file_level_fields_for_different_dataset_templates(
        self,
        client,
        initial_users,
    ):
        set_request_for_user(initial_users["user_1"])

        project_template, project_fields = self._create_assigned_metadata_template(
            self.project_1,
            "Bulk fill mixed project template",
            [
                {
                    "custom_key": "mixed_project_text",
                    "field_type": MetadataFieldType.TEXT,
                },
            ],
        )
        dataset_template_1, dataset_fields_1 = self._create_assigned_metadata_template(
            self.uploads_dataset_1,
            "Bulk fill mixed file-level template 1",
            [
                {
                    "custom_key": "mixed_file_level_text_1",
                    "field_type": MetadataFieldType.TEXT,
                },
            ],
        )
        dataset_template_2, dataset_fields_2 = self._create_assigned_metadata_template(
            self.uploads_dataset_2,
            "Bulk fill mixed file-level template 2",
            [
                {
                    "custom_key": "mixed_file_level_text_2",
                    "field_type": MetadataFieldType.TEXT,
                },
            ],
        )

        self.project_1.metadata_template = project_template
        self.project_1.save()

        dataset_1 = self._create_bulk_fill_dataset(
            "Bulk fill mixed file-level eligible 1",
            self.folder_1,
            linked_templates=[
                project_template,
                dataset_template_1,
            ],
            metadata_values_by_field={
                project_fields["mixed_project_text"]: "Project",
                dataset_fields_1["mixed_file_level_text_1"]: "File 1",
            },
            dataset_metadata_template=dataset_template_1,
        )
        dataset_2 = self._create_bulk_fill_dataset(
            "Bulk fill mixed file-level eligible 2",
            self.folder_1,
            linked_templates=[
                project_template,
                dataset_template_2,
            ],
            metadata_values_by_field={
                project_fields["mixed_project_text"]: "Project",
                dataset_fields_2["mixed_file_level_text_2"]: "File 2",
            },
            dataset_metadata_template=dataset_template_2,
        )

        preflight_url = reverse("uploads-dataset-bulk-fill-metadata-template-fields")
        submit_url = reverse("uploads-dataset-bulk-fill-metadata-template-fields-submit")

        preflight_response = client.post(
            preflight_url,
            {
                "uploads_datasets": [
                    dataset_1.pk,
                    dataset_2.pk,
                ],
            },
            format="json",
        )

        assert preflight_response.status_code == status.HTTP_200_OK
        assert preflight_response.data["warnings"] == [
            {
                "code": "DIFFERENT_DATASET_METADATA_TEMPLATES",
            },
        ]
        assert preflight_response.data["skipped_uploads_datasets_count"] == 0
        assert preflight_response.data["skip_reasons"] == []
        assert [field["custom_key"] for field in preflight_response.data["fields"]] == ["mixed_project_text"]

        response = client.post(
            submit_url,
            {
                "targets": [
                    {
                        "uploads_dataset_pk": item["pk"],
                        "base_version_pk": item["base_version_pk"],
                    }
                    for item in preflight_response.data["eligible_uploads_datasets"]
                ],
                "fields": [
                    {
                        "metadata_template_field_pk": dataset_fields_1["mixed_file_level_text_1"].pk,
                        "value": "Should be rejected",
                    },
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "error" in response.data
        dataset_1.refresh_from_db()
        dataset_2.refresh_from_db()
        assert dataset_1.uploads_versions.count() == 1
        assert dataset_2.uploads_versions.count() == 1

    def test_bulk_fill_metadata_template_fields_keeps_skip_reasons_separate_from_file_level_warnings(
        self,
        client,
        initial_users,
    ):
        set_request_for_user(initial_users["user_1"])

        project_template_v1, project_fields_v1 = self._create_assigned_metadata_template(
            self.project_1,
            "Bulk fill combined project template",
            [
                {
                    "custom_key": "combined_project_text",
                    "field_type": MetadataFieldType.TEXT,
                },
            ],
        )
        project_template_v2 = MetadataTemplate.objects.create(
            metadata_template_container=project_template_v1.metadata_template_container,
        )
        project_field_v2 = MetadataTemplateField.objects.create(
            metadata_template=project_template_v2,
            custom_key="combined_project_text",
            field_type=MetadataFieldType.TEXT,
            previous_version_field=project_fields_v1["combined_project_text"],
        )
        project_fields_v2 = {
            "combined_project_text": project_field_v2,
        }
        dataset_template_1, dataset_fields_1 = self._create_assigned_metadata_template(
            self.uploads_dataset_1,
            "Bulk fill combined file-level template 1",
            [
                {
                    "custom_key": "combined_file_level_text_1",
                    "field_type": MetadataFieldType.TEXT,
                },
            ],
        )
        dataset_template_2, dataset_fields_2 = self._create_assigned_metadata_template(
            self.uploads_dataset_2,
            "Bulk fill combined file-level template 2",
            [
                {
                    "custom_key": "combined_file_level_text_2",
                    "field_type": MetadataFieldType.TEXT,
                },
            ],
        )

        self.project_1.metadata_template = project_template_v2
        self.project_1.save()

        dataset_1 = self._create_bulk_fill_dataset(
            "Bulk fill combined eligible 1",
            self.folder_1,
            linked_templates=[
                project_template_v2,
                dataset_template_1,
            ],
            metadata_values_by_field={
                project_fields_v2["combined_project_text"]: "Project",
                dataset_fields_1["combined_file_level_text_1"]: "File 1",
            },
            dataset_metadata_template=dataset_template_1,
        )
        dataset_2 = self._create_bulk_fill_dataset(
            "Bulk fill combined eligible 2",
            self.folder_1,
            linked_templates=[
                project_template_v2,
                dataset_template_2,
            ],
            metadata_values_by_field={
                project_fields_v2["combined_project_text"]: "Project",
                dataset_fields_2["combined_file_level_text_2"]: "File 2",
            },
            dataset_metadata_template=dataset_template_2,
        )
        pending_upgrade_dataset = self._create_bulk_fill_dataset(
            "Bulk fill combined pending upgrade",
            self.folder_1,
            linked_templates=[
                project_template_v1,
            ],
            metadata_values_by_field={
                project_fields_v1["combined_project_text"]: "Project",
            },
        )

        url = reverse("uploads-dataset-bulk-fill-metadata-template-fields")
        response = client.post(
            url,
            {
                "uploads_datasets": [
                    dataset_1.pk,
                    dataset_2.pk,
                    pending_upgrade_dataset.pk,
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["warnings"] == [
            {
                "code": "DIFFERENT_DATASET_METADATA_TEMPLATES",
            },
        ]
        assert response.data["skipped_uploads_datasets_count"] == 1
        assert response.data["skip_reasons"] == [
            {
                "code": "PENDING_TEMPLATE_UPGRADE",
                "count": 1,
            },
        ]
        assert [field["custom_key"] for field in response.data["fields"]] == ["combined_project_text"]
        assert [item["pk"] for item in response.data["eligible_uploads_datasets"]] == [
            str(dataset_1.pk),
            str(dataset_2.pk),
        ]

    def test_bulk_fill_metadata_template_fields_skips_only_inherited_template_upgrades(
        self,
        client,
        initial_users,
    ):
        set_request_for_user(initial_users["user_1"])

        project_template_v1, project_fields_v1 = self._create_assigned_metadata_template(
            self.project_1,
            "Inherited upgrade project template",
            [
                {
                    "custom_key": "project_upgrade_text",
                    "field_type": MetadataFieldType.TEXT,
                },
            ],
        )
        project_template_v2 = MetadataTemplate.objects.create(
            metadata_template_container=project_template_v1.metadata_template_container,
        )
        project_field_v2 = MetadataTemplateField.objects.create(
            metadata_template=project_template_v2,
            custom_key="project_upgrade_text",
            field_type=MetadataFieldType.TEXT,
            previous_version_field=project_fields_v1["project_upgrade_text"],
            order=0,
        )
        project_field_v2.set_value(None)

        dataset_template_v1, dataset_fields_v1 = self._create_assigned_metadata_template(
            self.uploads_dataset_1,
            "Dataset-only upgrade template",
            [
                {
                    "custom_key": "dataset_upgrade_only",
                    "field_type": MetadataFieldType.TEXT,
                },
            ],
        )
        dataset_template_v2 = MetadataTemplate.objects.create(
            metadata_template_container=dataset_template_v1.metadata_template_container,
        )
        dataset_field_v2 = MetadataTemplateField.objects.create(
            metadata_template=dataset_template_v2,
            custom_key="dataset_upgrade_only",
            field_type=MetadataFieldType.TEXT,
            previous_version_field=dataset_fields_v1["dataset_upgrade_only"],
            order=0,
        )
        dataset_field_v2.set_value(None)

        self.project_1.metadata_template = project_template_v2
        self.project_1.save()

        pending_inherited_dataset = self._create_bulk_fill_dataset(
            "Pending inherited template dataset",
            self.folder_1,
            linked_templates=[
                project_template_v1,
            ],
            metadata_values_by_field={
                project_fields_v1["project_upgrade_text"]: "Old inherited value",
            },
        )
        dataset_only_upgrade_dataset = self._create_bulk_fill_dataset(
            "Dataset-only upgrade dataset",
            self.folder_1,
            linked_templates=[
                project_template_v2,
                dataset_template_v1,
            ],
            metadata_values_by_field={
                project_field_v2: "Current inherited value",
                dataset_fields_v1["dataset_upgrade_only"]: "Old dataset-only value",
            },
            dataset_metadata_template=dataset_template_v2,
        )

        url = reverse("uploads-dataset-bulk-fill-metadata-template-fields")
        response = client.post(
            url,
            {
                "uploads_datasets": [
                    pending_inherited_dataset.pk,
                    dataset_only_upgrade_dataset.pk,
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["has_skipped_due_to_pending_template_upgrade"] is True
        assert response.data["skipped_uploads_datasets_count"] == 1
        assert response.data["skip_reasons"] == [
            {
                "code": "PENDING_TEMPLATE_UPGRADE",
                "count": 1,
            },
        ]
        assert response.data["eligible_uploads_datasets"] == [
            {
                "pk": str(dataset_only_upgrade_dataset.pk),
                "display_name": dataset_only_upgrade_dataset.display_name,
                "base_version_pk": str(dataset_only_upgrade_dataset.latest_version.pk),
            },
        ]
        assert len(response.data["fields"]) == 1
        assert response.data["fields"][0]["custom_key"] == "project_upgrade_text"

    def test_bulk_fill_metadata_template_fields_submit_updates_changed_datasets_and_skips_stale_and_unchanged(
        self,
        client,
        initial_users,
    ):
        set_request_for_user(initial_users["user_1"])

        project_template, project_fields = self._create_assigned_metadata_template(
            self.project_1,
            "Bulk fill submit project template",
            [
                {
                    "custom_key": "submit_text",
                    "field_type": MetadataFieldType.TEXT,
                },
            ],
        )
        self.project_1.metadata_template = project_template
        self.project_1.save()

        dataset_changed = self._create_bulk_fill_dataset(
            "Bulk fill changed dataset",
            self.folder_1,
            linked_templates=[
                project_template,
            ],
            metadata_values_by_field={
                project_fields["submit_text"]: "Old value",
            },
            extra_metadata=[
                {
                    "custom_key": "unrelated_key",
                    "value": "Keep me",
                },
            ],
        )
        dataset_stale = self._create_bulk_fill_dataset(
            "Bulk fill stale dataset",
            self.folder_1,
            linked_templates=[
                project_template,
            ],
            metadata_values_by_field={
                project_fields["submit_text"]: "Old value",
            },
        )
        dataset_unchanged = self._create_bulk_fill_dataset(
            "Bulk fill unchanged dataset",
            self.folder_1,
            linked_templates=[
                project_template,
            ],
            metadata_values_by_field={
                project_fields["submit_text"]: "New value",
            },
        )

        preflight_url = reverse("uploads-dataset-bulk-fill-metadata-template-fields")
        submit_url = reverse("uploads-dataset-bulk-fill-metadata-template-fields-submit")

        preflight_response = client.post(
            preflight_url,
            {
                "uploads_datasets": [
                    dataset_changed.pk,
                    dataset_stale.pk,
                    dataset_unchanged.pk,
                ],
            },
            format="json",
        )
        assert preflight_response.status_code == status.HTTP_200_OK

        UploadsVersion.objects.create(
            dataset=dataset_stale,
            version_file=dataset_stale.latest_version.version_file,
        )

        response = client.post(
            submit_url,
            {
                "targets": [
                    {
                        "uploads_dataset_pk": item["pk"],
                        "base_version_pk": item["base_version_pk"],
                    }
                    for item in preflight_response.data["eligible_uploads_datasets"]
                ],
                "fields": [
                    {
                        "metadata_template_field_pk": project_fields["submit_text"].pk,
                        "value": "New value",
                    },
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["updated_uploads_datasets_count"] == 1
        assert response.data["skipped_uploads_datasets_count"] == 2
        assert response.data["skip_reasons"] == [
            {
                "code": "STALE_BASE_VERSION",
                "count": 1,
            },
            {
                "code": "NO_CHANGES",
                "count": 1,
            },
        ]

        dataset_changed.refresh_from_db()
        assert dataset_changed.uploads_versions.count() == 2
        assert (
            dataset_changed.latest_version.metadata.get(
                metadata_template_field=project_fields["submit_text"],
            ).get_value()
            == "New value"
        )
        assert (
            dataset_changed.latest_version.metadata.get(
                custom_key="unrelated_key",
            ).get_value()
            == "Keep me"
        )
        assert dataset_changed.latest_version.metadata_templates.filter(pk=project_template.pk).exists()

        dataset_stale.refresh_from_db()
        assert dataset_stale.uploads_versions.count() == 2

        dataset_unchanged.refresh_from_db()
        assert dataset_unchanged.uploads_versions.count() == 1

    def test_bulk_fill_metadata_template_fields_submit_rejects_invalid_values(self, client, initial_users):
        set_request_for_user(initial_users["user_1"])

        folder_template, folder_fields = self._create_assigned_metadata_template(
            self.folder_1,
            "Bulk fill invalid selection template",
            [
                {
                    "custom_key": "invalid_selection",
                    "field_type": MetadataFieldType.SELECTION,
                    "config": {
                        "options": [
                            "Allowed",
                        ],
                    },
                },
            ],
        )
        self.folder_1.metadata_template = folder_template
        self.folder_1.save()

        dataset = self._create_bulk_fill_dataset(
            "Bulk fill invalid selection dataset",
            self.folder_1,
            linked_templates=[
                folder_template,
            ],
            metadata_values_by_field={
                folder_fields["invalid_selection"]: None,
            },
        )

        preflight_url = reverse("uploads-dataset-bulk-fill-metadata-template-fields")
        submit_url = reverse("uploads-dataset-bulk-fill-metadata-template-fields-submit")

        preflight_response = client.post(
            preflight_url,
            {
                "uploads_datasets": [
                    dataset.pk,
                ],
            },
            format="json",
        )
        assert preflight_response.status_code == status.HTTP_200_OK

        response = client.post(
            submit_url,
            {
                "targets": [
                    {
                        "uploads_dataset_pk": item["pk"],
                        "base_version_pk": item["base_version_pk"],
                    }
                    for item in preflight_response.data["eligible_uploads_datasets"]
                ],
                "fields": [
                    {
                        "metadata_template_field_pk": folder_fields["invalid_selection"].pk,
                        "value": "Forbidden",
                    },
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "error" in response.data
        dataset.refresh_from_db()
        assert dataset.uploads_versions.count() == 1

        response = client.post(
            submit_url,
            {
                "targets": [
                    {
                        "uploads_dataset_pk": item["pk"],
                        "base_version_pk": item["base_version_pk"],
                    }
                    for item in preflight_response.data["eligible_uploads_datasets"]
                ],
                "fields": [
                    {
                        "metadata_template_field_pk": folder_fields["invalid_selection"].pk,
                        "value": "  Allowed  ",
                    },
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["updated_uploads_datasets_count"] == 1
        dataset.refresh_from_db()
        assert (
            dataset.latest_version.metadata.get(
                metadata_template_field=folder_fields["invalid_selection"],
            ).get_value()
            == "Allowed"
        )

    def test_read_uploads_dataset_list_with_respect_for_empty_datasets(self, client, initial_users):
        """
        Ensure we can read the uploads dataset list with respect for empty datasets.
        """

        url = reverse("uploads-dataset-list")

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        set_request_for_user(initial_users["user_1"])
        self.uploads_dataset_5.lock()

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0

        self.uploads_dataset_5.unlock()

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        UploadsVersion.objects.create(
            dataset=self.uploads_dataset_5,
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        set_request_for_user(initial_users["user_1"])
        self.uploads_dataset_5.lock()

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        self.uploads_dataset_5.unlock()

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

    def test_read_uploads_dataset_details(self, client):
        """
        Ensure we can read the uploads dataset details.
        """
        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": self.uploads_dataset_1.pk,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == self.uploads_dataset_1.name
        assert "has_upgradeable_metadata_templates" not in response.data
        assert "metadata_upgrade_status" not in response.data

    def test_change_uploads_dataset_details(self, client, initial_users):
        """
        Ensure we can change the uploads dataset details.
        """
        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": self.uploads_dataset_1.pk,
            },
        )

        self.uploads_dataset_1.refresh_from_db()
        assert self.uploads_dataset_1.uploads_versions.count() == 0

        # We can't use reverse for the action as this would collide with other routers
        file_action_url = f"{url}file/"
        publish_action_url = f"{url}publish/"

        response = client.post(
            file_action_url,
            {
                "file": sample_file,
            },
            format="multipart",
        )
        assert response.status_code == status.HTTP_201_CREATED

        self.uploads_dataset_1.refresh_from_db()
        assert self.uploads_dataset_1.uploads_versions.count() == 1

        self.uploads_dataset_1.latest_version.refresh_from_db()
        assert self.uploads_dataset_1.latest_version.metadata_templates.count() == 0

        dataset_name = "Altered dataset name"

        response = client.patch(
            url,
            {
                "name": dataset_name,
                "metadata_template": self.metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == dataset_name
        assert response.data["metadata_template"]["pk"] == str(self.metadata_template_1.pk)

        self.uploads_dataset_1.refresh_from_db()
        assert self.uploads_dataset_1.uploads_versions.count() == 2

        self.uploads_dataset_1.latest_version.refresh_from_db()
        assert self.uploads_dataset_1.latest_version.metadata_templates.count() == 1

        response = client.patch(
            url,
            {
                "name": dataset_name,
                "metadata_template": self.metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(self.metadata_template_1.pk)

        self.uploads_dataset_1.refresh_from_db()
        assert self.uploads_dataset_1.uploads_versions.count() == 2

        self.uploads_dataset_1.latest_version.refresh_from_db()
        assert self.uploads_dataset_1.latest_version.metadata_templates.count() == 1

        response = client.patch(
            url,
            {
                "metadata_template": None,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"] is None

        self.uploads_dataset_1.refresh_from_db()
        assert self.uploads_dataset_1.uploads_versions.count() == 2

        self.uploads_dataset_1.latest_version.refresh_from_db()
        assert self.uploads_dataset_1.latest_version.metadata_templates.count() == 1

        response = client.patch(
            url,
            {
                "metadata_template": self.metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(self.metadata_template_1.pk)

        self.uploads_dataset_1.refresh_from_db()
        assert self.uploads_dataset_1.uploads_versions.count() == 2

        self.uploads_dataset_1.latest_version.refresh_from_db()
        assert self.uploads_dataset_1.latest_version.metadata_templates.count() == 1

        response = client.patch(
            url,
            {
                "metadata_template": {
                    "name": "Metadata template 2",
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
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] != str(self.metadata_template_1.pk)

        self.uploads_dataset_1.refresh_from_db()
        assert self.uploads_dataset_1.uploads_versions.count() == 3

        self.uploads_dataset_1.latest_version.refresh_from_db()
        assert self.uploads_dataset_1.latest_version.metadata_templates.count() == 2

        self.uploads_dataset_1.publication_date = None
        self.uploads_dataset_1.save()

        response = client.post(
            publish_action_url,
            {
                "folder": self.folder_1.pk,
            },
            format="multipart",
        )
        assert response.status_code == status.HTTP_201_CREATED

        # There will be no new version as active metadata templates on datasets get applied immediately
        self.uploads_dataset_1.refresh_from_db()
        assert self.uploads_dataset_1.uploads_versions.count() == 3

        self.uploads_dataset_1.latest_version.refresh_from_db()
        assert self.uploads_dataset_1.latest_version.metadata_templates.count() == 2

        url = reverse(
            "uploads-version-detail",
            kwargs={
                "pk": self.uploads_dataset_1.latest_version.pk,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["metadata_templates"]) == 2

    def test_change_uploads_dataset_details_with_folder_template(self, client, initial_users):
        """
        Ensure we can change the uploads dataset details.
        """
        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": self.uploads_dataset_1.pk,
            },
        )

        self.uploads_dataset_1.refresh_from_db()
        assert self.uploads_dataset_1.uploads_versions.count() == 0

        # We can't use reverse for the action as this would collide with other routers
        file_action_url = f"{url}file/"
        publish_action_url = f"{url}publish/"

        response = client.post(
            file_action_url,
            {
                "file": sample_file,
            },
            format="multipart",
        )
        assert response.status_code == status.HTTP_201_CREATED

        self.uploads_dataset_1.refresh_from_db()
        assert self.uploads_dataset_1.uploads_versions.count() == 1

        self.uploads_dataset_1.latest_version.refresh_from_db()
        assert self.uploads_dataset_1.latest_version.metadata_templates.count() == 0

        dataset_name = "Altered dataset name"

        response = client.patch(
            url,
            {
                "name": dataset_name,
                "metadata_template": self.metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == dataset_name
        assert response.data["metadata_template"]["pk"] == str(self.metadata_template_1.pk)

        self.uploads_dataset_1.refresh_from_db()
        assert self.uploads_dataset_1.uploads_versions.count() == 2

        self.uploads_dataset_1.latest_version.refresh_from_db()
        assert self.uploads_dataset_1.latest_version.metadata_templates.count() == 1
        assert self.uploads_dataset_1.latest_version.metadata_templates.filter(
            pk__in=[
                self.metadata_template_1.pk,
            ],
        ).exists()

        response = client.patch(
            url,
            {
                "name": dataset_name,
                "metadata_template": self.metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(self.metadata_template_1.pk)

        self.uploads_dataset_1.refresh_from_db()
        assert self.uploads_dataset_1.uploads_versions.count() == 2

        self.uploads_dataset_1.latest_version.refresh_from_db()
        assert self.uploads_dataset_1.latest_version.metadata_templates.count() == 1
        assert self.uploads_dataset_1.latest_version.metadata_templates.filter(
            pk__in=[
                self.metadata_template_1.pk,
            ],
        ).exists()

        response = client.patch(
            url,
            {
                "metadata_template": None,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"] is None

        self.uploads_dataset_1.refresh_from_db()
        assert self.uploads_dataset_1.uploads_versions.count() == 2

        self.uploads_dataset_1.latest_version.refresh_from_db()
        assert self.uploads_dataset_1.latest_version.metadata_templates.count() == 1
        assert self.uploads_dataset_1.latest_version.metadata_templates.filter(
            pk__in=[
                self.metadata_template_1.pk,
            ],
        ).exists()

        response = client.patch(
            url,
            {
                "metadata_template": self.metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(self.metadata_template_1.pk)

        self.uploads_dataset_1.refresh_from_db()
        assert self.uploads_dataset_1.uploads_versions.count() == 2

        self.uploads_dataset_1.latest_version.refresh_from_db()
        assert self.uploads_dataset_1.latest_version.metadata_templates.count() == 1
        assert self.uploads_dataset_1.latest_version.metadata_templates.filter(
            pk__in=[
                self.metadata_template_1.pk,
            ],
        ).exists()

        response = client.patch(
            url,
            {
                "metadata_template": {
                    "name": "Metadata template 2",
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
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] != str(self.metadata_template_1.pk)

        metadata_template_2 = MetadataTemplate.objects.get(pk=response.data["metadata_template"]["pk"])

        self.uploads_dataset_1.refresh_from_db()
        assert self.uploads_dataset_1.uploads_versions.count() == 3

        self.uploads_dataset_1.latest_version.refresh_from_db()
        assert self.uploads_dataset_1.latest_version.metadata_templates.count() == 2
        assert self.uploads_dataset_1.latest_version.metadata_templates.filter(
            pk__in=[
                self.metadata_template_1.pk,
                metadata_template_2.pk,
            ],
        ).exists()

        self.uploads_dataset_1.publication_date = None
        self.uploads_dataset_1.save()

        self.folder_1.metadata_template = self.metadata_template_1
        self.folder_1.save()

        response = client.post(
            publish_action_url,
            {
                "folder": self.folder_1.pk,
            },
            format="multipart",
        )
        assert response.status_code == status.HTTP_201_CREATED

        self.uploads_dataset_1.refresh_from_db()
        assert self.uploads_dataset_1.uploads_versions.count() == 3

        self.uploads_dataset_1.latest_version.refresh_from_db()
        assert self.uploads_dataset_1.latest_version.metadata_templates.count() == 2
        assert self.uploads_dataset_1.latest_version.metadata_templates.filter(
            pk__in=[
                self.metadata_template_1.pk,
                metadata_template_2.pk,
            ],
        ).exists()

        url = reverse(
            "uploads-version-detail",
            kwargs={
                "pk": self.uploads_dataset_1.latest_version.pk,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["metadata_templates"]) == 2

    def test_change_uploads_dataset_details_and_create_a_new_metadata_template(self, client, initial_users):
        """
        Ensure we can change the uploads dataset and create a new metadata template.
        """
        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": self.uploads_dataset_1.pk,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"] is None

        response = client.patch(
            url,
            {
                "metadata_template": self.metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(self.metadata_template_1.pk)

        response = client.patch(
            url,
            {
                "metadata_template": {
                    "name": "Metadata template 2",
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
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"] is not None
        assert response.data["metadata_template"]["pk"] != str(self.metadata_template_1.pk)

        url = reverse(
            "metadata-template-detail",
            kwargs={
                "pk": response.data["metadata_template"]["pk"],
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] is None
        assert response.data["metadata_template_container"]["name"] == "Metadata template 2"

    def test_create_uploads_dataset(self, client):
        """
        Ensure we can create a new uploads dataset.
        """
        url = reverse("uploads-dataset-list")

        response = client.post(
            url,
            {
                "name": "Dataset 6",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Dataset 6"
        assert response.data["folder"] is None
        assert response.data["metadata_template"] is None
        assert UploadsDataset.objects.all().count() == 6

        response = client.post(
            url,
            {
                "name": "Dataset 7",
                "folder": self.folder_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Dataset 7"
        assert response.data["folder"]["pk"] == str(self.folder_1.pk)
        assert response.data["metadata_template"] is None
        assert UploadsDataset.objects.all().count() == 7

    # TODO: Creating metadata templates for datasets can only be possible in the future,
    #  when we allow datasets without files
    # def test_create_uploads_dataset_with_metadata_template(self, client, initial_users):
    #     """
    #     Ensure we can create a new uploads dataset with a metadata template.
    #     """
    #     url = reverse("metadata-template-list")
    #
    #     response = client.get(url, format="json")
    #     assert response.status_code == status.HTTP_200_OK
    #     assert len(response.data["results"]) == 1
    #
    #     url = reverse("uploads-dataset-list")
    #
    #     response = client.post(
    #         url,
    #         {
    #             "name": "Dataset 6",
    #             "metadata_template": {
    #                 "name": "Metadata template 2",
    #                 "metadata_template_fields": [
    #                     {
    #                         "custom_key": "custom_key_1",
    #                         "value": "custom value",
    #                         "mandatory": True,
    #                     },
    #                     {
    #                         "custom_key": "custom_key_2",
    #                         "mandatory": False,
    #                     },
    #                 ],
    #             },
    #         },
    #         format="json",
    #     )
    #     assert response.status_code == status.HTTP_201_CREATED
    #
    #     uploads_dataset = response.data
    #
    #     url = reverse("metadata-template-list")
    #
    #     response = client.get(url, format="json")
    #     assert response.status_code == status.HTTP_200_OK
    #     assert len(response.data["results"]) == 2
    #
    #     url = reverse(
    #         "metadata-template-detail",
    #         kwargs={
    #             "pk": uploads_dataset["metadata_template"]["pk"],
    #         },
    #     )
    #
    #     response = client.get(url, format="json")
    #     assert response.status_code == status.HTTP_200_OK
    #     assert response.data["name"] is None
    #     assert response.data["metadata_template_container"]["name"] == "Metadata template 2"
    #
    #     content_type = ContentType.objects.get_for_model(UploadsDataset)
    #     assert response.data["metadata_template_container"]["assigned_to_content_type"] == get_content_type_for_object(
    #         content_type,
    #     )
    #     assert response.data["metadata_template_container"]["assigned_to_object_id"] == uploads_dataset["pk"]
    #
    #     assert response.data["metadata_template_container"]["project"] is None
    #
    #     url = reverse("metadata-template-field-list")
    #     url_filter = f"{url}?metadata_template={uploads_dataset['metadata_template']['pk']}"
    #     response = client.get(url_filter, format="json")
    #     assert response.status_code == status.HTTP_200_OK
    #     assert len(response.data["results"]) == 2
    #
    #     assert (
    #         MetadataTemplateField.objects.filter(metadata_template=uploads_dataset["metadata_template"]["pk"]).count()
    #         == 2
    #     )
    #
    #     metadata_template_field_1 = MetadataTemplateField.objects.get(custom_key="custom_key_1")
    #     assert metadata_template_field_1.custom_key == "custom_key_1"
    #     assert metadata_template_field_1.field_type == MetadataFieldType.TEXT
    #     assert metadata_template_field_1.get_value() == "custom value"
    #     assert metadata_template_field_1.mandatory is True
    #
    #     metadata_template_field_2 = MetadataTemplateField.objects.get(custom_key="custom_key_2")
    #     assert metadata_template_field_2.custom_key == "custom_key_2"
    #     assert metadata_template_field_2.field_type == MetadataFieldType.TEXT
    #     assert metadata_template_field_2.get_value() is None
    #     assert metadata_template_field_2.mandatory is False
    #
    #     uploads_dataset = UploadsDataset.objects.get(pk=uploads_dataset["pk"])
    #     uploads_dataset.folder = self.folder_1
    #     uploads_dataset.save()
    #
    #     url = reverse(
    #         "metadata-template-detail",
    #         kwargs={
    #             "pk": uploads_dataset.metadata_template.pk,
    #         },
    #     )
    #
    #     response = client.get(url, format="json")
    #     assert response.status_code == status.HTTP_200_OK
    #     assert response.data["metadata_template_container"]["project"] is not None
    #     assert response.data["metadata_template_container"]["project"]["pk"] == str(uploads_dataset.folder.project.pk)
    #     assert response.data["metadata_template_container"]["project"]["name"] == uploads_dataset.folder.project.name

    def test_change_folder_protection(self, client):
        """
        Ensure we can't change the folder when updating an uploads dataset.
        """
        url = reverse("uploads-dataset-list")

        response = client.post(
            url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["folder"] is None

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": response.data["pk"],
            },
        )

        response = client.patch(
            url,
            {
                "folder": self.folder_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["folder"] is None

        url = reverse("uploads-dataset-list")

        response = client.post(
            url,
            {
                "folder": self.folder_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["folder"]["pk"] == str(self.folder_1.pk)

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": response.data["pk"],
            },
        )

        response = client.patch(
            url,
            {
                "folder": self.folder_2.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["folder"]["pk"] == str(self.folder_1.pk)

        response = client.patch(
            url,
            {
                "folder": None,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["folder"]["pk"] == str(self.folder_1.pk)

    def test_read_uploads_dataset_list_with_folder_permission(self, client, initial_users):
        """
        Ensure we can read the uploads dataset list.
        """
        auth_user(client, initial_users["user_2"])

        url = reverse("uploads-dataset-list")

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0

        url_filter = f"{url}?folder={self.folder_1.pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0

        url_filter = f"{url}?folder={self.folder_2.pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0

        url_filter = f"{url}?folder={self.folder_3.pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0

        membership = ProjectMembership.objects.create(
            project=self.project_1,
            member=initial_users["user_2"],
        )

        FolderPermission.objects.create(
            folder=self.folder_2,
            project_membership=membership,
        )

        FolderPermission.objects.create(
            folder=self.folder_3,
            project_membership=membership,
        )

        url_filter = f"{url}?folder={self.folder_1.pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0

        url_filter = f"{url}?folder={self.folder_2.pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        url_filter = f"{url}?folder={self.folder_3.pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

    def test_uploads_dataset_file_action(self, client):
        """
        Ensure we can upload a file for an uploads dataset.
        """
        dataset = UploadsDataset.objects.create()

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": dataset.pk,
            },
        )

        # We can't use reverse for the action as this would collide with other routers
        file_action_url = f"{url}file/"
        version_action_url = f"{url}version/"

        assert dataset.publication_date is None
        assert dataset.is_published() is False
        assert dataset.uploads_versions.count() == 0
        assert dataset.latest_version is None

        response = client.post(
            file_action_url,
            {
                "file": sample_file,
            },
            format="multipart",
        )
        assert response.status_code == status.HTTP_201_CREATED
        version_file = UploadsVersionFile.objects.get(pk=response.data["version_file"]["pk"])
        assert version_file.file_parser.filter(
            parser_type=FileParser.Type.MIME_TYPE,
            status=FileParser.Status.SCHEDULED,
        ).exists()
        assert not version_file.metadata.filter(custom_key="MIME_TYPE").exists()

        assert dataset.publication_date is None
        assert dataset.is_published() is False
        assert dataset.uploads_versions.count() == 1
        assert dataset.latest_version is not None
        assert str(dataset.latest_version.pk) == response.data["pk"]
        assert dataset.latest_version.publication_date is None
        assert dataset.latest_version.is_published() is False

        url = reverse(
            "uploads-version-detail",
            kwargs={
                "pk": response.data["pk"],
            },
        )

        response = client.patch(
            url,
            {
                "name": "v1",
                "metadata": [
                    {
                        "custom_key": "custom_key_1",
                        "value": "custom value 1",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        assert dataset.publication_date is None
        assert dataset.is_published() is False
        assert dataset.latest_version is not None
        assert dataset.latest_version.name == "v1"
        assert dataset.latest_version.metadata.count() == 0
        assert dataset.latest_version.publication_date is None
        assert dataset.latest_version.is_published() is False

        response = client.post(
            version_action_url,
            {
                "metadata": [
                    {
                        "custom_key": "custom_key_1",
                        "value": "custom value 1",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        assert dataset.publication_date is None
        assert dataset.is_published() is False
        assert dataset.latest_version is not None
        assert dataset.latest_version.name is None
        assert dataset.latest_version.metadata.count() == 1
        assert dataset.latest_version.metadata.first().custom_key == "custom_key_1"
        assert dataset.latest_version.metadata.first().get_value() == "custom value 1"
        assert dataset.latest_version.publication_date is None
        assert dataset.latest_version.is_published() is False

        response = client.post(
            file_action_url,
            {
                "file": sample_file,
            },
            format="multipart",
        )
        assert response.status_code == status.HTTP_201_CREATED

        assert dataset.publication_date is None
        assert dataset.is_published() is False
        assert dataset.uploads_versions.count() == 3
        assert dataset.latest_version is not None
        assert str(dataset.latest_version.pk) == response.data["pk"]
        assert dataset.latest_version.name == f"File upload of {sample_file.name}"
        assert dataset.latest_version.metadata.count() == 1
        assert dataset.latest_version.metadata.first().custom_key == "custom_key_1"
        assert dataset.latest_version.metadata.first().get_value() == "custom value 1"
        assert dataset.latest_version.publication_date is None
        assert dataset.latest_version.is_published() is False

        url = reverse(
            "uploads-version-detail",
            kwargs={
                "pk": response.data["pk"],
            },
        )

        response = client.patch(
            url,
            {
                "name": "v2",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        assert dataset.publication_date is None
        assert dataset.is_published() is False
        assert dataset.latest_version is not None
        assert dataset.latest_version.name == "v2"
        assert dataset.latest_version.metadata.count() == 1
        assert dataset.latest_version.metadata.first().custom_key == "custom_key_1"
        assert dataset.latest_version.metadata.first().get_value() == "custom value 1"
        assert dataset.latest_version.publication_date is None
        assert dataset.latest_version.is_published() is False

        response = client.post(
            file_action_url,
            {
                "file": sample_file,
            },
            format="multipart",
        )
        assert response.status_code == status.HTTP_201_CREATED

        assert dataset.publication_date is None
        assert dataset.is_published() is False
        assert dataset.uploads_versions.count() == 4
        assert dataset.latest_version is not None
        assert str(dataset.latest_version.pk) == response.data["pk"]
        assert dataset.latest_version.name == f"File upload of {sample_file.name}"
        assert dataset.latest_version.metadata.count() == 1
        assert dataset.latest_version.metadata.first().custom_key == "custom_key_1"
        assert dataset.latest_version.metadata.first().get_value() == "custom value 1"
        assert dataset.latest_version.publication_date is None
        assert dataset.latest_version.is_published() is False

        dataset.publish(folder=self.folder_1.pk)

        assert dataset.publication_date is not None
        assert dataset.is_published() is True
        assert all([uploads_version.publication_date is not None for uploads_version in dataset.uploads_versions.all()])
        assert all([uploads_version.is_published() for uploads_version in dataset.uploads_versions.all()])

        response = client.post(
            file_action_url,
            {
                "file": sample_file,
            },
            format="multipart",
        )
        assert response.status_code == status.HTTP_201_CREATED

        assert dataset.publication_date is not None
        assert dataset.is_published() is True
        assert dataset.uploads_versions.count() == 5
        assert dataset.latest_version is not None
        assert str(dataset.latest_version.pk) == response.data["pk"]
        assert dataset.latest_version.name == f"File upload of {sample_file.name}"
        assert dataset.latest_version.metadata.count() == 1
        assert dataset.latest_version.metadata.first().custom_key == "custom_key_1"
        assert dataset.latest_version.metadata.first().get_value() == "custom value 1"
        assert dataset.latest_version.publication_date is not None
        assert dataset.latest_version.is_published() is True

    def test_uploads_dataset_file_upload_with_metadata_from_templates(self, client, initial_users):
        """
        Ensure we can upload a file for an uploads dataset, and they automatically receive metadata from templates.
        """
        set_request_for_user(initial_users["user_1"])

        dataset_1 = UploadsDataset.objects.create()

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": dataset_1.pk,
            },
        )

        # We can't use reverse for the action as this would collide with other routers
        file_action_url = f"{url}file/"

        assert dataset_1.uploads_versions.count() == 0
        assert dataset_1.latest_version is None

        response = client.post(
            file_action_url,
            {
                "file": sample_file,
            },
            format="multipart",
        )
        assert response.status_code == status.HTTP_201_CREATED

        assert dataset_1.uploads_versions.count() == 1
        assert dataset_1.latest_version is not None
        assert (
            Metadata.objects.filter(
                assigned_to_content_type=dataset_1.latest_version.get_content_type(),
            ).count()
            == 0
        )
        assert dataset_1.latest_version.metadata.count() == 0
        assert str(dataset_1.latest_version.pk) == response.data["pk"]

        set_request_for_user(initial_users["user_1"])

        project_1 = Project.objects.create(
            name="Project 1",
        )
        assert project_1.metadata_template is None

        folder_1 = project_1.folders.first()
        assert folder_1.metadata_template is None

        dataset_2 = UploadsDataset.objects.create()

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": dataset_2.pk,
            },
        )

        # We can't use reverse for the action as this would collide with other routers
        file_action_url = f"{url}file/"
        publish_action_url = f"{url}publish/"

        assert dataset_2.uploads_versions.count() == 0
        assert dataset_2.latest_version is None

        response = client.post(
            file_action_url,
            {
                "file": sample_file,
            },
            format="multipart",
        )
        assert response.status_code == status.HTTP_201_CREATED

        assert dataset_2.uploads_versions.count() == 1
        assert dataset_2.latest_version is not None
        assert (
            Metadata.objects.filter(
                assigned_to_content_type=dataset_2.latest_version.get_content_type(),
            ).count()
            == 0
        )
        assert dataset_2.latest_version.metadata.count() == 0

        response = client.post(
            publish_action_url,
            {
                "folder": folder_1.pk,
            },
            format="multipart",
        )
        assert response.status_code == status.HTTP_201_CREATED

        assert dataset_2.uploads_versions.count() == 1
        assert dataset_2.latest_version is not None
        assert (
            Metadata.objects.filter(
                assigned_to_content_type=dataset_2.latest_version.get_content_type(),
            ).count()
            == 0
        )
        assert dataset_2.latest_version.metadata.count() == 0
        assert str(dataset_2.latest_version.pk) == response.data["latest_version"]["pk"]

        set_request_for_user(initial_users["user_1"])

        metadata_template_container_1 = MetadataTemplateContainer.objects.create(
            name="Global metadata template container 1",
        )

        metadata_template_1 = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_1,
        )

        metadata_template_field_1 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_1,
            custom_key="integer_1",
            field_type=MetadataFieldType.INTEGER,
        )
        metadata_template_field_1.set_value("1337")

        folder_1.metadata_template = metadata_template_1
        folder_1.save()

        dataset_3 = UploadsDataset.objects.create()

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": dataset_3.pk,
            },
        )

        # We can't use reverse for the action as this would collide with other routers
        file_action_url = f"{url}file/"
        publish_action_url = f"{url}publish/"

        assert dataset_3.uploads_versions.count() == 0
        assert dataset_3.latest_version is None

        response = client.post(
            file_action_url,
            {
                "file": sample_file,
            },
            format="multipart",
        )
        assert response.status_code == status.HTTP_201_CREATED

        assert dataset_3.uploads_versions.count() == 1
        assert dataset_3.latest_version is not None
        assert (
            Metadata.objects.filter(
                assigned_to_content_type=dataset_3.latest_version.get_content_type(),
            ).count()
            == 0
        )
        assert dataset_3.latest_version.metadata.count() == 0

        response = client.post(
            publish_action_url,
            {
                "folder": folder_1.pk,
            },
            format="multipart",
        )
        assert response.status_code == status.HTTP_201_CREATED

        assert dataset_3.uploads_versions.count() == 2
        assert dataset_3.latest_version is not None
        assert (
            Metadata.objects.filter(
                assigned_to_content_type=dataset_3.latest_version.get_content_type(),
            ).count()
            == 1
        )
        assert dataset_3.latest_version.metadata.count() == 1
        integer_1 = dataset_3.latest_version.metadata.filter(custom_key="integer_1")
        assert integer_1.exists()
        assert integer_1.first().get_value() == "1337"
        assert integer_1.first().metadata_template_field == metadata_template_field_1
        assert str(dataset_3.latest_version.pk) == response.data["latest_version"]["pk"]

        set_request_for_user(initial_users["user_1"])

        metadata_template_container_2 = MetadataTemplateContainer.objects.create(
            name="Global metadata template container 2",
        )

        metadata_template_2 = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_2,
        )

        metadata_template_field_2 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_2,
            custom_key="text_1",
            field_type=MetadataFieldType.TEXT,
        )
        metadata_template_field_2.set_value("Text")

        project_1.metadata_template = metadata_template_2
        project_1.save()

        dataset_4 = UploadsDataset.objects.create()

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": dataset_4.pk,
            },
        )

        # We can't use reverse for the action as this would collide with other routers
        file_action_url = f"{url}file/"
        publish_action_url = f"{url}publish/"

        assert dataset_4.uploads_versions.count() == 0
        assert dataset_4.latest_version is None

        response = client.post(
            file_action_url,
            {
                "file": sample_file,
            },
            format="multipart",
        )
        assert response.status_code == status.HTTP_201_CREATED

        assert dataset_4.uploads_versions.count() == 1
        assert dataset_4.latest_version is not None
        assert (
            Metadata.objects.filter(
                assigned_to_content_type=dataset_4.latest_version.get_content_type(),
            ).count()
            == 1
        )
        assert dataset_4.latest_version.metadata.count() == 0

        response = client.post(
            publish_action_url,
            {
                "folder": folder_1.pk,
            },
            format="multipart",
        )
        assert response.status_code == status.HTTP_201_CREATED

        assert dataset_4.uploads_versions.count() == 2
        assert dataset_4.latest_version is not None
        assert (
            Metadata.objects.filter(
                assigned_to_content_type=dataset_4.latest_version.get_content_type(),
            ).count()
            == 3
        )
        assert dataset_4.latest_version.metadata.count() == 2
        integer_1 = dataset_4.latest_version.metadata.filter(custom_key="integer_1")
        assert integer_1.exists()
        assert integer_1.first().get_value() == "1337"
        assert integer_1.first().metadata_template_field == metadata_template_field_1
        text_1 = dataset_4.latest_version.metadata.filter(custom_key="text_1")
        assert text_1.exists()
        assert text_1.first().get_value() == "Text"
        assert text_1.first().metadata_template_field == metadata_template_field_2
        assert str(dataset_4.latest_version.pk) == response.data["latest_version"]["pk"]

        set_request_for_user(initial_users["user_1"])

        metadata_template_field_2.set_value("Altered text")

        metadata_template_field_3 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_2,
            custom_key="text_2",
            field_type=MetadataFieldType.TEXT,
        )
        metadata_template_field_3.set_value("Text 2")

        response = client.post(
            file_action_url,
            {
                "file": sample_file,
            },
            format="multipart",
        )
        assert response.status_code == status.HTTP_201_CREATED

        assert dataset_4.uploads_versions.count() == 3
        assert dataset_4.latest_version is not None
        assert (
            Metadata.objects.filter(
                assigned_to_content_type=dataset_4.latest_version.get_content_type(),
            ).count()
            == 5
        )
        assert dataset_4.latest_version.metadata.count() == 2
        integer_1 = dataset_4.latest_version.metadata.filter(custom_key="integer_1")
        assert integer_1.exists()
        assert integer_1.first().get_value() == "1337"
        assert integer_1.first().metadata_template_field == metadata_template_field_1
        text_1 = dataset_4.latest_version.metadata.filter(custom_key="text_1")
        assert text_1.exists()
        assert text_1.first().get_value() == "Text"
        assert text_1.first().metadata_template_field == metadata_template_field_2
        assert str(dataset_4.latest_version.pk) == response.data["pk"]

    def test_uploads_dataset_restore_action(self, client, initial_users):
        """
        Ensure we can restore an uploads version by making a POST request to the respective dataset action endpoint.
        """
        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": self.uploads_dataset_1.pk,
            },
        )
        version_action_url = f"{url}version/"
        file_action_url = f"{url}file/"
        restore_action_url = f"{url}restore/"

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["uploads_versions"]) == 0

        response = client.post(
            file_action_url,
            {
                "file": sample_file,
            },
            format="multipart",
        )
        assert response.status_code == status.HTTP_201_CREATED

        uploads_version_1_url = reverse(
            "uploads-version-detail",
            kwargs={
                "pk": response.data["pk"],
            },
        )

        response = client.patch(
            uploads_version_1_url,
            {
                "name": "v1",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        uploads_version_1 = response.data
        assert uploads_version_1["name"] == "v1"
        assert len(uploads_version_1["metadata"]) == 0

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["uploads_versions"]) == 1
        assert response.data["uploads_versions"][0]["pk"] == uploads_version_1["pk"]

        response = client.post(
            restore_action_url,
            {
                "uploads_version": uploads_version_1["pk"],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        response = client.post(
            version_action_url,
            {
                "metadata": [
                    {
                        "custom_key": "custom_key_1",
                        "field_type": MetadataFieldType.TEXT,
                        "value": "custom value 1",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        uploads_version_2 = response.data
        assert uploads_version_2["name"] is None
        assert len(uploads_version_2["metadata"]) == 1

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["uploads_versions"]) == 2
        assert response.data["uploads_versions"][0]["pk"] == uploads_version_2["pk"]

        response = client.post(
            restore_action_url,
            {
                "uploads_version": uploads_version_1["pk"],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        uploads_version_3 = response.data
        assert uploads_version_3["name"] is None
        assert len(uploads_version_3["metadata"]) == 0
        assert uploads_version_3["restored_uploads_version"]["pk"] == uploads_version_1["pk"]

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["uploads_versions"]) == 3
        assert uploads_version_1["pk"] != uploads_version_3["pk"]
        assert response.data["uploads_versions"][0]["pk"] == uploads_version_3["pk"]

        response = client.post(
            restore_action_url,
            {
                "uploads_version": uploads_version_2["pk"],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        uploads_version_4 = response.data
        assert uploads_version_4["name"] is None
        assert len(uploads_version_4["metadata"]) == 1
        assert uploads_version_4["restored_uploads_version"]["pk"] == uploads_version_2["pk"]

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["uploads_versions"]) == 4
        assert uploads_version_2["pk"] != uploads_version_4["pk"]
        assert response.data["uploads_versions"][0]["pk"] == uploads_version_4["pk"]

        response = client.post(
            restore_action_url,
            {
                "uploads_version": uploads_version_4["pk"],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        uploads_dataset_2 = UploadsDataset.objects.create(
            name="Dataset 2",
            folder=self.folder_1,
            publication_date=timezone.now(),
        )

        uploads_version_5 = UploadsVersion.objects.create(
            dataset=uploads_dataset_2,
            publication_date=timezone.now(),
        )

        response = client.post(
            restore_action_url,
            {
                "uploads_version": uploads_version_5.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_uploads_dataset_version_action(self, client, initial_users):
        """
        Ensure we can create a new uploads version by making a POST request to the respective dataset action endpoint.
        """
        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": self.uploads_dataset_1.pk,
            },
        )
        action_url = f"{url}version/"

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["uploads_versions"]) == 0
        assert response.data["uploads_versions_count"] == 0

        response = client.post(action_url, format="json")
        assert response.status_code == status.HTTP_403_FORBIDDEN

        uploads_version_file_1 = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        UploadsVersion.objects.create(
            dataset=self.uploads_dataset_1,
            version_file=uploads_version_file_1,
            publication_date=timezone.now(),
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["uploads_versions"]) == 1
        assert response.data["uploads_versions_count"] == 1

        response = client.post(
            action_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] is None
        assert response.data["status"] == UploadsVersion.Status.SCHEDULED
        assert response.data["version_file"]["pk"] == str(uploads_version_file_1.pk)
        assert len(response.data["metadata"]) == 0

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["uploads_versions"]) == 2
        assert response.data["uploads_versions_count"] == 2

        response = client.post(
            action_url,
            {
                "name": "Specific version name",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Specific version name"
        assert response.data["status"] == UploadsVersion.Status.SCHEDULED
        assert response.data["version_file"]["pk"] == str(uploads_version_file_1.pk)
        assert len(response.data["metadata"]) == 0

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["uploads_versions"]) == 3
        assert response.data["uploads_versions_count"] == 3

        response = client.post(
            action_url,
            {
                "name": "Another specific version name",
                "metadata": [
                    {
                        "custom_key": "custom_key_1",
                        "value": "custom value 1",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Another specific version name"
        assert response.data["status"] == UploadsVersion.Status.SCHEDULED
        assert response.data["version_file"]["pk"] == str(uploads_version_file_1.pk)
        assert len(response.data["metadata"]) == 1

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["uploads_versions"]) == 4
        assert response.data["uploads_versions_count"] == 4

        response = client.post(
            action_url,
            {
                "name": "Named version 3",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Named version 3"
        assert response.data["status"] == UploadsVersion.Status.SCHEDULED
        assert response.data["version_file"]["pk"] == str(uploads_version_file_1.pk)
        assert len(response.data["metadata"]) == 0

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["uploads_versions"]) == 5
        assert response.data["uploads_versions_count"] == 5

        response = client.post(
            action_url,
            {
                "name": "v4",
                "metadata": [
                    {
                        "custom_key": "custom_key_1",
                        "value": "custom value 1",
                    },
                    {
                        "custom_key": "custom_key_2",
                        "value": "custom value 2",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "v4"
        assert response.data["status"] == UploadsVersion.Status.SCHEDULED
        assert response.data["version_file"]["pk"] == str(uploads_version_file_1.pk)
        assert len(response.data["metadata"]) == 2

        uploads_version_file_2 = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        uploads_version = UploadsVersion.objects.get(pk=response.data["pk"])
        uploads_version.version_file = uploads_version_file_2
        uploads_version.save()

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["uploads_versions"]) == 6
        assert response.data["uploads_versions_count"] == 6

        response = client.post(
            action_url,
            {
                "name": "v5",
                "metadata": [],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "v5"
        assert response.data["status"] == UploadsVersion.Status.SCHEDULED
        assert response.data["version_file"]["pk"] == str(uploads_version_file_2.pk)
        assert len(response.data["metadata"]) == 0

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["uploads_versions"]) == 7
        assert response.data["uploads_versions_count"] == 7

        auth_user(client, initial_users["user_2"])

        response = client.post(
            action_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_uploads_dataset_version_action_with_metadata_ordering(self, client, initial_users):
        """
        Ensure we can create a new uploads version by making a POST request to the respective dataset action endpoint
        and that the metadata ordering is respected.
        """
        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": self.uploads_dataset_1.pk,
            },
        )
        action_url = f"{url}version/"

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["uploads_versions_count"] == 0

        response = client.post(action_url, format="json")
        assert response.status_code == status.HTTP_403_FORBIDDEN

        uploads_version_file_1 = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        UploadsVersion.objects.create(
            dataset=self.uploads_dataset_1,
            version_file=uploads_version_file_1,
            publication_date=timezone.now(),
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["uploads_versions_count"] == 1

        response = client.post(
            action_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["version_file"]["pk"] == str(uploads_version_file_1.pk)
        assert len(response.data["metadata"]) == 0

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["uploads_versions_count"] == 2

        response = client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "custom_key_1",
                        "value": "custom value 1",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["version_file"]["pk"] == str(uploads_version_file_1.pk)
        assert len(response.data["metadata"]) == 1
        assert response.data["metadata"][0]["order"] == 0
        assert response.data["metadata"][0]["custom_key"] == "custom_key_1"

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["uploads_versions_count"] == 3

        response = client.post(
            action_url,
            {
                "metadata": [
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
        assert response.data["version_file"]["pk"] == str(uploads_version_file_1.pk)
        assert len(response.data["metadata"]) == 3
        assert response.data["metadata"][0]["order"] == 1
        assert response.data["metadata"][0]["custom_key"] == "custom_key_1"
        assert response.data["metadata"][1]["order"] == 2
        assert response.data["metadata"][1]["custom_key"] == "custom_key_2"
        assert response.data["metadata"][2]["order"] == 3
        assert response.data["metadata"][2]["custom_key"] == "custom_key_3"

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["uploads_versions_count"] == 4

        response = client.post(
            action_url,
            {
                "metadata": [
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
        assert response.data["version_file"]["pk"] == str(uploads_version_file_1.pk)
        assert len(response.data["metadata"]) == 5
        assert response.data["metadata"][0]["order"] == -1
        assert response.data["metadata"][0]["custom_key"] == "custom_key_5"
        assert response.data["metadata"][1]["order"] == 0
        assert response.data["metadata"][1]["custom_key"] == "custom_key_2"
        assert response.data["metadata"][2]["order"] == 0
        assert response.data["metadata"][2]["custom_key"] == "custom_key_4"
        assert response.data["metadata"][3]["order"] == 2
        assert response.data["metadata"][3]["custom_key"] == "custom_key_3"
        assert response.data["metadata"][4]["order"] == 5
        assert response.data["metadata"][4]["custom_key"] == "custom_key_1"

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["uploads_versions_count"] == 5

    def test_uploads_dataset_metadata_templates_action(self, client):
        """
        Ensure we can read the available metadata templates list for an uploads dataset.
        """
        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": self.uploads_dataset_1.pk,
            },
        )

        # We can't use reverse for the action as this would collide with other routers
        action_url = f"{url}metadata-templates/"

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        assert Folder.objects.filter(project=self.project_1).count() == 3

        url = reverse("folder-list")

        response = client.post(
            url,
            {
                "name": "Folder 2",
                "project": str(self.project_1.pk),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Folder.objects.filter(project=self.project_1).count() == 4

        folder_2 = response.data

        metadata_template_container_2 = MetadataTemplateContainer.objects.create(
            name="Folder metadata template container 2",
            assigned_to_content_type=ContentType.objects.get_for_model(Folder),
            assigned_to_object_id=folder_2["pk"],
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_2,
        )

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        metadata_template_container_3 = MetadataTemplateContainer.objects.create(
            name="Project metadata template container 1",
            assigned_to_content_type=self.project_1.get_content_type(),
            assigned_to_object_id=self.project_1.pk,
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_3,
        )

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

        metadata_template_container_4 = MetadataTemplateContainer.objects.create(
            name="Project metadata template container 2",
            assigned_to_content_type=self.project_2.get_content_type(),
            assigned_to_object_id=self.project_2.pk,
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_4,
        )

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

        metadata_template_container_5 = MetadataTemplateContainer.objects.create(
            name="Global metadata template container 1",
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_5,
        )

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 3

        metadata_template_container_6 = MetadataTemplateContainer.objects.create(
            name="Uploads dataset metadata template container 1",
            assigned_to_content_type=self.uploads_dataset_1.get_content_type(),
            assigned_to_object_id=self.uploads_dataset_1.pk,
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_6,
        )

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 4

    def test_read_uploads_dataset_details_with_folder_permission(self, client, initial_users):
        """
        Ensure we can read the uploads dataset details with folder permissions.
        """
        auth_user(client, initial_users["user_2"])

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": self.uploads_dataset_1.pk,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": self.uploads_dataset_2.pk,
            },
        )
        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": self.uploads_dataset_3.pk,
            },
        )
        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": self.uploads_dataset_4.pk,
            },
        )
        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND

        membership = ProjectMembership.objects.create(
            project=self.project_1,
            member=initial_users["user_2"],
        )

        FolderPermission.objects.create(
            folder=self.folder_1,
            project_membership=membership,
        )

        FolderPermission.objects.create(
            folder=self.folder_2,
            project_membership=membership,
        )

        FolderPermission.objects.create(
            folder=self.folder_3,
            project_membership=membership,
        )

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": self.uploads_dataset_2.pk,
            },
        )
        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == self.uploads_dataset_2.name

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": self.uploads_dataset_3.pk,
            },
        )
        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == self.uploads_dataset_3.name

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": self.uploads_dataset_4.pk,
            },
        )
        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == self.uploads_dataset_4.name

    def test_delete(self, client, initial_users):
        """
        Ensure we can delete a dataset as long as it has not been published or locked.
        """
        set_request_for_user(initial_users["user_1"])

        uploads_dataset_1 = UploadsDataset.objects.create(
            name="Dataset 1",
        )
        uploads_dataset_1_pk = uploads_dataset_1.pk

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

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": uploads_dataset_1.pk,
            },
        )

        auth_user(client, initial_users["user_2"])

        response = client.delete(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert UploadsDataset.objects.filter(pk=uploads_dataset_1_pk).exists()

        auth_user(client, initial_users["user_1"])

        response = client.delete(url)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not UploadsDataset.objects.filter(pk=uploads_dataset_1_pk).exists()

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": uploads_dataset_2.pk,
            },
        )
        lock_url = f"{url}lock/"

        response = client.post(
            lock_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        with TestCase.captureOnCommitCallbacks(execute=True):
            response = client.delete(url)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not UploadsDataset.objects.filter(pk=uploads_dataset_2_pk).exists()
        assert not UploadsVersion.objects.filter(pk=uploads_version_pk).exists()
        assert not UploadsVersionFile.objects.filter(pk=version_file_pk).exists()
        assert not os.path.exists(version_file_path)

    def test_delete_protection(self, client, initial_users):
        """
        Ensure we can't delete a dataset if it has been published or locked.
        """
        set_request_for_user(initial_users["user_1"])

        project_membership_user_2, _ = ProjectMembership.objects.update_or_create(
            project=self.project_1,
            member=initial_users["user_2"],
            defaults={
                "is_project_admin": True,
                "can_create_folders": True,
                "is_metadata_template_admin": True,
            },
        )

        FolderPermission.objects.update_or_create(
            folder=self.folder_1,
            project_membership=project_membership_user_2,
            defaults={
                "is_folder_admin": True,
                "is_metadata_template_admin": True,
                "can_edit": True,
            },
        )

        self.uploads_dataset_1.deletable_until_date = None
        self.uploads_dataset_1.save()

        uploads_dataset_1_pk = self.uploads_dataset_1.pk

        self.uploads_dataset_1.lock()

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": self.uploads_dataset_1.pk,
            },
        )

        auth_user(client, initial_users["user_2"])

        response = client.delete(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert UploadsDataset.objects.filter(pk=uploads_dataset_1_pk).exists()
        assert self.uploads_dataset_1.locked is True
        assert self.uploads_dataset_1.publication_date is not None
        assert self.uploads_dataset_1.deletable_until_date is None

        auth_user(client, initial_users["user_1"])

        self.uploads_dataset_1.unlock()
        self.uploads_dataset_1.deletable_until_date = timezone.now() - timezone.timedelta(hours=1)
        self.uploads_dataset_1.save()

        response = client.delete(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert UploadsDataset.objects.filter(pk=uploads_dataset_1_pk).exists()
        assert self.uploads_dataset_1.locked is False
        assert self.uploads_dataset_1.publication_date is not None
        assert self.uploads_dataset_1.deletable_until_date is not None

        self.uploads_dataset_1.deletable_until_date = None
        self.uploads_dataset_1.save()

        response = client.delete(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert UploadsDataset.objects.filter(pk=uploads_dataset_1_pk).exists()
        assert self.uploads_dataset_1.locked is False
        assert self.uploads_dataset_1.publication_date is not None
        assert self.uploads_dataset_1.deletable_until_date is None

        self.uploads_dataset_1.deletable_until_date = timezone.now() + timezone.timedelta(
            minutes=int(get_dbsettings_value("DATASET_DELETABLE_WITHIN_THRESHOLD")),
        )
        self.uploads_dataset_1.save()

        response = client.delete(url)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not UploadsDataset.objects.filter(pk=uploads_dataset_1_pk).exists()

        self.uploads_dataset_2.deletable_until_date = timezone.now() - timezone.timedelta(hours=1)
        self.uploads_dataset_2.save()

        set_request_for_user(initial_users["user_1"])

        version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        uploads_version = UploadsVersion.objects.create(
            dataset=self.uploads_dataset_2,
            version_file=version_file,
        )

        uploads_dataset_2_pk = self.uploads_dataset_2.pk
        version_file_pk = version_file.pk
        uploads_version_pk = uploads_version.pk

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": self.uploads_dataset_2.pk,
            },
        )

        response = client.delete(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert UploadsDataset.objects.filter(pk=uploads_dataset_2_pk).exists()
        assert self.uploads_dataset_2.locked is False
        assert self.uploads_dataset_2.publication_date is not None
        assert self.uploads_dataset_2.deletable_until_date is not None
        assert UploadsVersion.objects.filter(pk=uploads_version.pk).exists()
        assert UploadsVersionFile.objects.filter(pk=version_file.pk).exists()

        url = reverse(
            "uploads-version-detail",
            kwargs={
                "pk": uploads_version.pk,
            },
        )

        response = client.delete(url)
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
        assert UploadsDataset.objects.filter(pk=uploads_dataset_2_pk).exists()
        assert self.uploads_dataset_2.locked is False
        assert self.uploads_dataset_2.publication_date is not None
        assert self.uploads_dataset_2.deletable_until_date is not None
        assert UploadsVersion.objects.filter(pk=uploads_version.pk).exists()
        assert UploadsVersionFile.objects.filter(pk=version_file.pk).exists()

        self.uploads_dataset_2.deletable_until_date = timezone.now() + timezone.timedelta(
            minutes=int(get_dbsettings_value("DATASET_DELETABLE_WITHIN_THRESHOLD")),
        )
        self.uploads_dataset_2.save()

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": self.uploads_dataset_2.pk,
            },
        )

        auth_user(client, initial_users["user_1"])

        response = client.delete(url)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not UploadsDataset.objects.filter(pk=uploads_dataset_2_pk).exists()
        assert not UploadsVersion.objects.filter(pk=uploads_version_pk).exists()
        assert not UploadsVersionFile.objects.filter(pk=version_file_pk).exists()

    def test_folder_editor_cannot_delete_foreign_dataset_within_grace_period(self, client, initial_users):
        """
        Regression test: a folder Editor who is NOT the dataset creator must not be
        able to delete another user's dataset while the deletable-within-threshold
        grace period is still active. The bug report observed a partial state where
        the dataset row remained but its versions/files were gone (frontend then 404d
        on the first-version lookup).
        """
        set_request_for_user(initial_users["user_1"])

        project_membership_user_2, _ = ProjectMembership.objects.update_or_create(
            project=self.project_1,
            member=initial_users["user_2"],
            defaults={
                "is_project_admin": False,
                "can_create_folders": False,
                "is_metadata_template_admin": False,
            },
        )

        FolderPermission.objects.update_or_create(
            folder=self.folder_1,
            project_membership=project_membership_user_2,
            defaults={
                "is_folder_admin": False,
                "is_metadata_template_admin": False,
                "can_edit": True,
            },
        )

        version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )
        uploads_version = UploadsVersion.objects.create(
            dataset=self.uploads_dataset_1,
            version_file=version_file,
        )

        self.uploads_dataset_1.deletable_until_date = timezone.now() + timezone.timedelta(
            minutes=int(get_dbsettings_value("DATASET_DELETABLE_WITHIN_THRESHOLD")),
        )
        self.uploads_dataset_1.save()

        uploads_dataset_1_pk = self.uploads_dataset_1.pk
        uploads_version_pk = uploads_version.pk
        version_file_pk = version_file.pk

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": self.uploads_dataset_1.pk,
            },
        )

        auth_user(client, initial_users["user_2"])

        response = client.delete(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN
        # The dataset AND all its cascade-reachable rows must be fully intact —
        # no partial state is allowed.
        assert UploadsDataset.objects.filter(pk=uploads_dataset_1_pk).exists()
        assert UploadsVersion.objects.filter(pk=uploads_version_pk).exists()
        assert UploadsVersionFile.objects.filter(pk=version_file_pk).exists()

    def test_dataset_delete_rolls_back_on_foreign_version_failure(self, client, initial_users):
        """
        Regression test: if a pre_delete signal raises mid-cascade (e.g. a version
        created by a different user refuses to be deleted), the outer atomic in
        UploadsDataset.delete() must roll back EVERYTHING — no dataset without
        versions, no versions without files, no orphaned disk artefacts.
        """
        set_request_for_user(initial_users["user_1"])

        version_file_1 = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )
        uploads_version_1 = UploadsVersion.objects.create(
            dataset=self.uploads_dataset_1,
            version_file=version_file_1,
        )

        set_request_for_user(initial_users["user_2"])

        version_file_2 = UploadsVersionFile.objects.create(
            uploaded_file=sample_file2,
        )
        uploads_version_2 = UploadsVersion.objects.create(
            dataset=self.uploads_dataset_1,
            version_file=version_file_2,
        )

        # reset_deletable_until_date_on_foreign_version nulled the window when
        # user_2 added a version; restore it so CanDeleteDataset lets the request
        # through and the mid-cascade failure path is actually exercised.
        self.uploads_dataset_1.deletable_until_date = timezone.now() + timezone.timedelta(
            minutes=int(get_dbsettings_value("DATASET_DELETABLE_WITHIN_THRESHOLD")),
        )
        self.uploads_dataset_1.save()

        uploads_dataset_1_pk = self.uploads_dataset_1.pk
        uploads_version_1_pk = uploads_version_1.pk
        uploads_version_2_pk = uploads_version_2.pk
        version_file_1_pk = version_file_1.pk
        version_file_2_pk = version_file_2.pk

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": self.uploads_dataset_1.pk,
            },
        )

        auth_user(client, initial_users["user_1"])

        response = client.delete(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert UploadsDataset.objects.filter(pk=uploads_dataset_1_pk).exists()
        assert UploadsVersion.objects.filter(pk=uploads_version_1_pk).exists()
        assert UploadsVersion.objects.filter(pk=uploads_version_2_pk).exists()
        assert UploadsVersionFile.objects.filter(pk=version_file_1_pk).exists()
        assert UploadsVersionFile.objects.filter(pk=version_file_2_pk).exists()

    def test_bulk_delete_requires_authentication(self, client, initial_users):
        """
        Regression test: the bulk-delete action must refuse unauthenticated callers
        at the view level (previously it relied solely on inline checks because
        get_permissions() returned [] for actions missing from the mapping).
        """
        # The autouse `auth_user_1` fixture pre-authenticates the client; clear it
        # so we genuinely test the unauthenticated path.
        client.credentials()

        url = reverse("uploads-dataset-list")
        action_url = f"{url}bulk-delete/"

        response = client.post(
            action_url,
            {
                "uploads_datasets": [
                    str(self.uploads_dataset_1.pk),
                ],
            },
            format="json",
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
        assert UploadsDataset.objects.filter(pk=self.uploads_dataset_1.pk).exists()

    def test_bulk_delete(self, client, initial_users):
        """
        Ensure we can bulk delete datasets as long as none have been published.
        """
        set_request_for_user(initial_users["user_1"])

        uploads_dataset_1 = UploadsDataset.objects.create(
            name="Dataset 1",
        )
        uploads_dataset_1_pk = uploads_dataset_1.pk

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

        set_request_for_user(initial_users["user_2"])

        uploads_dataset_3 = UploadsDataset.objects.create(
            name="Dataset 3",
        )
        uploads_dataset_3_pk = uploads_dataset_3.pk

        set_request_for_user(initial_users["user_1"])

        url = reverse("uploads-dataset-list")
        action_url = f"{url}bulk-delete/"

        auth_user(client, initial_users["user_2"])

        response = client.post(
            action_url,
            {
                "uploads_datasets": [
                    uploads_dataset_1.pk,
                    uploads_dataset_2.pk,
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["success"]) == 0
        assert len(response.data["error"]) == 2
        assert str(uploads_dataset_1_pk) in response.data["error"]
        assert str(uploads_dataset_2_pk) in response.data["error"]
        assert UploadsDataset.objects.filter(pk=uploads_dataset_1_pk).exists()
        assert UploadsDataset.objects.filter(pk=uploads_dataset_2_pk).exists()
        assert UploadsDataset.objects.filter(pk=uploads_dataset_3_pk).exists()
        assert UploadsVersion.objects.filter(pk=uploads_version_pk).exists()
        assert UploadsVersionFile.objects.filter(pk=version_file_pk).exists()
        assert os.path.exists(version_file_path)

        auth_user(client, initial_users["user_1"])

        response = client.post(
            action_url,
            {
                "uploads_datasets": [
                    "7a49c23c-fa4d-4cb2-a619-abc4c2b3bbb1",
                    uploads_dataset_1.pk,
                    uploads_dataset_2.pk,
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert UploadsDataset.objects.filter(pk=uploads_dataset_1_pk).exists()
        assert UploadsDataset.objects.filter(pk=uploads_dataset_2_pk).exists()
        assert UploadsDataset.objects.filter(pk=uploads_dataset_3_pk).exists()
        assert UploadsVersion.objects.filter(pk=uploads_version_pk).exists()
        assert UploadsVersionFile.objects.filter(pk=version_file_pk).exists()
        assert os.path.exists(version_file_path)

        response = client.post(
            action_url,
            {
                "uploads_datasets": [],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert UploadsDataset.objects.filter(pk=uploads_dataset_1_pk).exists()
        assert UploadsDataset.objects.filter(pk=uploads_dataset_2_pk).exists()
        assert UploadsDataset.objects.filter(pk=uploads_dataset_3_pk).exists()
        assert UploadsVersion.objects.filter(pk=uploads_version_pk).exists()
        assert UploadsVersionFile.objects.filter(pk=version_file_pk).exists()
        assert os.path.exists(version_file_path)

        response = client.post(
            action_url,
            {
                "uploads_datasets": [
                    uploads_dataset_3.pk,
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["success"]) == 0
        assert len(response.data["error"]) == 1
        assert str(uploads_dataset_3_pk) in response.data["error"]
        assert UploadsDataset.objects.filter(pk=uploads_dataset_1_pk).exists()
        assert UploadsDataset.objects.filter(pk=uploads_dataset_2_pk).exists()
        assert UploadsDataset.objects.filter(pk=uploads_dataset_3_pk).exists()
        assert UploadsVersion.objects.filter(pk=uploads_version_pk).exists()
        assert UploadsVersionFile.objects.filter(pk=version_file_pk).exists()
        assert os.path.exists(version_file_path)

        uploads_dataset_2.publication_date = timezone.now()
        uploads_dataset_2.save()

        response = client.post(
            action_url,
            {
                "uploads_datasets": [
                    uploads_dataset_2.pk,
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["success"]) == 0
        assert len(response.data["error"]) == 1
        assert str(uploads_dataset_2_pk) in response.data["error"]
        assert UploadsDataset.objects.filter(pk=uploads_dataset_1_pk).exists()
        assert UploadsDataset.objects.filter(pk=uploads_dataset_2_pk).exists()
        assert UploadsDataset.objects.filter(pk=uploads_dataset_3_pk).exists()
        assert UploadsVersion.objects.filter(pk=uploads_version_pk).exists()
        assert UploadsVersionFile.objects.filter(pk=version_file_pk).exists()
        assert os.path.exists(version_file_path)

        uploads_dataset_2.publication_date = None
        uploads_dataset_2.save()

        with TestCase.captureOnCommitCallbacks(execute=True):
            response = client.post(
                action_url,
                {
                    "uploads_datasets": [
                        uploads_dataset_1.pk,
                        uploads_dataset_2.pk,
                    ],
                },
                format="json",
            )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["success"]) == 2
        assert str(uploads_dataset_1_pk) in response.data["success"]
        assert str(uploads_dataset_2_pk) in response.data["success"]
        assert len(response.data["error"]) == 0
        assert not UploadsDataset.objects.filter(pk=uploads_dataset_1_pk).exists()
        assert not UploadsDataset.objects.filter(pk=uploads_dataset_2_pk).exists()
        assert UploadsDataset.objects.filter(pk=uploads_dataset_3_pk).exists()
        assert not UploadsVersion.objects.filter(pk=uploads_version_pk).exists()
        assert not UploadsVersionFile.objects.filter(pk=version_file_pk).exists()
        assert not os.path.exists(version_file_path)

        # A published dataset whose deletable_until_date has expired must not be deleted
        # and must remain fully accessible after the bulk-delete attempt.
        auth_user(client, initial_users["user_1"])
        self.uploads_dataset_1.deletable_until_date = timezone.now() - timezone.timedelta(hours=1)
        self.uploads_dataset_1.save()
        uploads_dataset_1_pk = self.uploads_dataset_1.pk

        response = client.post(
            action_url,
            {
                "uploads_datasets": [
                    self.uploads_dataset_1.pk,
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["success"]) == 0
        assert len(response.data["error"]) == 1
        assert str(uploads_dataset_1_pk) in response.data["error"]
        assert UploadsDataset.objects.filter(pk=uploads_dataset_1_pk).exists()

        detail_url = reverse("uploads-dataset-detail", kwargs={"pk": self.uploads_dataset_1.pk})
        response = client.get(detail_url, format="json")
        assert response.status_code == status.HTTP_200_OK

        # A published dataset WITH a version file that is within the deletable window
        # must be fully deleted (dataset + version + version file) in a single bulk-delete call.
        auth_user(client, initial_users["user_1"])
        set_request_for_user(initial_users["user_1"])
        self.uploads_dataset_2.deletable_until_date = timezone.now() + timezone.timedelta(
            minutes=int(get_dbsettings_value("DATASET_DELETABLE_WITHIN_THRESHOLD")),
        )
        self.uploads_dataset_2.save()

        uploads_version_2 = create_uploads_version_with_new_file_for_dataset(
            dataset=self.uploads_dataset_2,
            uploaded_file=sample_file,
        )
        uploads_version_2_pk = uploads_version_2.pk
        version_file_2_pk = uploads_version_2.version_file.pk
        uploads_dataset_2_pk = self.uploads_dataset_2.pk

        response = client.post(
            action_url,
            {
                "uploads_datasets": [
                    self.uploads_dataset_2.pk,
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["success"]) == 1
        assert len(response.data["error"]) == 0
        assert str(uploads_dataset_2_pk) in response.data["success"]
        assert not UploadsDataset.objects.filter(pk=uploads_dataset_2_pk).exists()
        assert not UploadsVersion.objects.filter(pk=uploads_version_2_pk).exists()
        assert not UploadsVersionFile.objects.filter(pk=version_file_2_pk).exists()

    def test_bulk_delete_dataset_with_multiple_versions_sharing_version_file(self, client, initial_users):
        """
        Ensure bulk delete succeeds in a single request when a dataset has multiple
        versions pointing to the same UploadsVersionFile. Previously, deleting the
        first version's file would CASCADE-delete sibling versions, and the explicit
        delete loop would then fail on stale references with UploadsVersionFile.DoesNotExist.
        """
        set_request_for_user(initial_users["user_1"])

        uploads_dataset = UploadsDataset.objects.create(
            name="Dataset with shared file",
        )
        uploads_dataset_pk = uploads_dataset.pk

        version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )
        version_file_pk = version_file.pk

        version_1 = UploadsVersion.objects.create(
            dataset=uploads_dataset,
            version_file=version_file,
        )
        version_1_pk = version_1.pk

        version_2 = UploadsVersion.objects.create(
            dataset=uploads_dataset,
            version_file=version_file,
        )
        version_2_pk = version_2.pk

        url = reverse("uploads-dataset-list")
        action_url = f"{url}bulk-delete/"

        auth_user(client, initial_users["user_1"])

        response = client.post(
            action_url,
            {
                "uploads_datasets": [
                    uploads_dataset.pk,
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["success"]) == 1
        assert str(uploads_dataset_pk) in response.data["success"]
        assert len(response.data["error"]) == 0
        assert not UploadsDataset.objects.filter(pk=uploads_dataset_pk).exists()
        assert not UploadsVersion.objects.filter(pk=version_1_pk).exists()
        assert not UploadsVersion.objects.filter(pk=version_2_pk).exists()
        assert not UploadsVersionFile.objects.filter(pk=version_file_pk).exists()

    def test_bulk_delete_rejects_non_creator_editor_in_folder(self, client, initial_users):
        """
        Regression for reporter ticket: a folder Editor who did NOT create the dataset
        must not be able to bulk-delete a published dataset within its 24h deletable
        window. The reported symptom was "dataset remains listed, contents are 404"
        after a supposed Editor-triggered delete — this test locks in that the delete
        is refused outright, with no partial-cascade side-effects on the file contents.
        """
        set_request_for_user(initial_users["user_1"])

        version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )
        version_file_pk = version_file.pk
        version_file_path = version_file.uploaded_file.path

        uploads_version = UploadsVersion.objects.create(
            dataset=self.uploads_dataset_1,
            version_file=version_file,
        )
        uploads_version_pk = uploads_version.pk
        uploads_dataset_pk = self.uploads_dataset_1.pk

        # User 2 is granted Editor permission on folder_1 — the folder dataset_1 lives in.
        project_membership = ProjectMembership.objects.create(
            project=self.project_1,
            member=initial_users["user_2"],
        )
        FolderPermission.objects.create(
            folder=self.folder_1,
            project_membership=project_membership,
            can_edit=True,
        )

        url = reverse("uploads-dataset-list")
        action_url = f"{url}bulk-delete/"

        auth_user(client, initial_users["user_2"])

        response = client.post(
            action_url,
            {
                "uploads_datasets": [
                    uploads_dataset_pk,
                ],
            },
            format="json",
        )

        # Editor is not the creator — bulk-delete must refuse.
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["success"]) == 0
        assert len(response.data["error"]) == 1
        assert str(uploads_dataset_pk) in response.data["error"]

        # Nothing should be deleted — neither metadata nor file contents on disk.
        assert UploadsDataset.objects.filter(pk=uploads_dataset_pk).exists()
        assert UploadsVersion.objects.filter(pk=uploads_version_pk).exists()
        assert UploadsVersionFile.objects.filter(pk=version_file_pk).exists()
        assert os.path.exists(version_file_path)

    def test_bulk_delete_dataset_with_three_versions_sharing_version_file(self, client, initial_users):
        """
        Extension of the 2-version shared-file regression: with THREE versions pointing
        to the same UploadsVersionFile, the first version's delete CASCADE-removes both
        siblings at once. The fix in UploadsVersion.delete() must tolerate two stale
        references in the outer loop, not just one.
        """
        set_request_for_user(initial_users["user_1"])

        uploads_dataset = UploadsDataset.objects.create(
            name="Dataset with three-way shared file",
        )
        uploads_dataset_pk = uploads_dataset.pk

        version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )
        version_file_pk = version_file.pk
        version_file_path = version_file.uploaded_file.path

        version_pks = [
            UploadsVersion.objects.create(
                dataset=uploads_dataset,
                version_file=version_file,
            ).pk
            for _ in range(3)
        ]

        url = reverse("uploads-dataset-list")
        action_url = f"{url}bulk-delete/"

        auth_user(client, initial_users["user_1"])

        with TestCase.captureOnCommitCallbacks(execute=True):
            response = client.post(
                action_url,
                {
                    "uploads_datasets": [
                        uploads_dataset_pk,
                    ],
                },
                format="json",
            )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["success"]) == 1
        assert str(uploads_dataset_pk) in response.data["success"]
        assert len(response.data["error"]) == 0
        assert not UploadsDataset.objects.filter(pk=uploads_dataset_pk).exists()
        for version_pk in version_pks:
            assert not UploadsVersion.objects.filter(pk=version_pk).exists()
        assert not UploadsVersionFile.objects.filter(pk=version_file_pk).exists()
        assert not os.path.exists(version_file_path)

    def test_bulk_delete_rolls_back_cleanly_when_cascade_fails(self, client, initial_users, mocker):
        """
        Structural invariant: if any step of the dataset cascade raises, the bulk-delete
        endpoint must leave the dataset fully intact — no half-deleted versions, no
        orphaned rows, and crucially no files missing from disk. This locks in the
        atomic() + on_commit contract around UploadsDataset.delete().
        """
        set_request_for_user(initial_users["user_1"])

        uploads_dataset = UploadsDataset.objects.create(
            name="Dataset that will fail to delete",
        )
        uploads_dataset_pk = uploads_dataset.pk

        version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )
        version_file_pk = version_file.pk
        version_file_path = version_file.uploaded_file.path

        uploads_version = UploadsVersion.objects.create(
            dataset=uploads_dataset,
            version_file=version_file,
        )
        uploads_version_pk = uploads_version.pk

        # Force the cascade to fail mid-flight. The outer atomic() must roll back
        # every partial DB write, and the on_commit-deferred os.remove must never run.
        original_delete = UploadsVersion.delete

        def _explode(self, *args, **kwargs):
            original_delete(self, *args, **kwargs)
            raise RuntimeError("simulated cascade failure")

        mocker.patch.object(UploadsVersion, "delete", autospec=True, side_effect=_explode)

        url = reverse("uploads-dataset-list")
        action_url = f"{url}bulk-delete/"

        auth_user(client, initial_users["user_1"])

        # captureOnCommitCallbacks(execute=True) would force-run any surviving
        # on_commit callbacks; the invariant under test is that the rolled-back
        # savepoint DISCARDS those callbacks, so executing them here must still
        # leave the file on disk intact.
        with TestCase.captureOnCommitCallbacks(execute=True):
            response = client.post(
                action_url,
                {
                    "uploads_datasets": [
                        uploads_dataset_pk,
                    ],
                },
                format="json",
            )

        # bulk_delete swallows the exception and reports it in the error list.
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["success"]) == 0
        assert len(response.data["error"]) == 1
        assert str(uploads_dataset_pk) in response.data["error"]

        # Nothing must be half-deleted: all rows survive and the file stays on disk.
        assert UploadsDataset.objects.filter(pk=uploads_dataset_pk).exists()
        assert UploadsVersion.objects.filter(pk=uploads_version_pk).exists()
        assert UploadsVersionFile.objects.filter(pk=version_file_pk).exists()
        assert os.path.exists(version_file_path)

    def test_check_expired_draft(self, client, initial_users):
        """
        Ensure we can determine if a dataset in the drafts section has expired.
        """
        set_request_for_user(initial_users["user_1"])

        folder = Folder.objects.create(
            name="Folder",
            project=self.project_1,
        )

        uploads_dataset_1 = UploadsDataset.objects.create(
            name="Dataset 1",
        )

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": uploads_dataset_1.pk,
            },
        )
        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_expired"] is False
        assert response.data["expiry_date"] is not None
        assert datetime.datetime.fromisoformat(response.data["expiry_date"]) > timezone.now()

        uploads_dataset_1.creation_date = timezone.now() - timedelta(days=settings.DRAFT_FILES_MAX_LIFETIME)
        uploads_dataset_1.save()

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_expired"] is True
        assert response.data["expiry_date"] is not None
        assert datetime.datetime.fromisoformat(response.data["expiry_date"]) < timezone.now()

        uploads_dataset_1.publication_date = timezone.now()
        uploads_dataset_1.folder = folder
        uploads_dataset_1.save()

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_expired"] is False
        assert response.data["expiry_date"] is None

        uploads_dataset_1.creation_date = timezone.now() - timedelta(days=settings.DRAFT_FILES_MAX_LIFETIME)
        uploads_dataset_1.save()

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_expired"] is False
        assert response.data["expiry_date"] is None

        url = reverse("uploads-dataset-list")
        url_filter = f"{url}?folder={folder.pk}"

        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        remove_expired_dataset_drafts()
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        uploads_dataset_1.creation_date = timezone.now() - timedelta(days=settings.DRAFT_FILES_MAX_LIFETIME)
        uploads_dataset_1.save()

        remove_expired_dataset_drafts()
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        uploads_dataset_1.creation_date = timezone.now() - timedelta(days=settings.DRAFT_FILES_MAX_LIFETIME)
        uploads_dataset_1.publication_date = None
        uploads_dataset_1.folder = None
        uploads_dataset_1.save()

        remove_expired_dataset_drafts()
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0

    def test_bulk_publish_datasets(self, client, initial_users):
        """
        Ensure we can publish uploads datasets in bulk.
        """
        set_request_for_user(initial_users["user_1"])

        uploads_dataset_1 = UploadsDataset.objects.create()
        uploads_dataset_2 = UploadsDataset.objects.create()
        uploads_dataset_3 = UploadsDataset.objects.create()

        url = reverse("uploads-dataset-bulk-publish")

        # This request must trigger: "You must provide at least one uploads dataset."
        response = client.post(
            url,
            {
                "folder": self.folder_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        # This request must trigger: "At least one uploads dataset provided does not exist."
        response = client.post(
            url,
            {
                "uploads_datasets": [
                    "7a49c23c-fa4d-4cb2-a619-abc4c2b3bbb1",
                ],
                "folder": self.folder_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

        # This request must trigger: "A folder with this primary key does not exist."
        response = client.post(
            url,
            {
                "uploads_datasets": [
                    uploads_dataset_1.pk,
                ],
                "folder": "7a49c23c-fa4d-4cb2-a619-abc4c2b3bbb1",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

        assert uploads_dataset_1.publication_date is None
        assert uploads_dataset_1.is_published() is False

        assert uploads_dataset_2.publication_date is None
        assert uploads_dataset_2.is_published() is False

        assert uploads_dataset_3.publication_date is None
        assert uploads_dataset_3.is_published() is False

        response = client.post(
            url,
            {
                "uploads_datasets": [
                    uploads_dataset_1.pk,
                    uploads_dataset_2.pk,
                    uploads_dataset_3.pk,
                ],
                "folder": self.folder_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        uploads_dataset_1.refresh_from_db()
        assert uploads_dataset_1.publication_date is not None
        assert uploads_dataset_1.is_published() is True

        uploads_dataset_2.refresh_from_db()
        assert uploads_dataset_2.publication_date is not None
        assert uploads_dataset_2.is_published() is True

        uploads_dataset_3.refresh_from_db()
        assert uploads_dataset_3.publication_date is not None
        assert uploads_dataset_3.is_published() is True

    def test_bulk_publish_datasets_partial_failure_contract(self, client, initial_users):
        """
        Ensure bulk publish returns partial-success details if one dataset fails to publish.
        """
        set_request_for_user(initial_users["user_1"])

        uploads_dataset_success = UploadsDataset.objects.create()
        uploads_dataset_failure = UploadsDataset.objects.create()
        uploads_version_failure = UploadsVersion.objects.create(
            dataset=uploads_dataset_failure,
            version_file=None,
            name="Broken version",
        )

        url = reverse("uploads-dataset-bulk-publish")

        response = client.post(
            url,
            {
                "uploads_datasets": [
                    uploads_dataset_success.pk,
                    uploads_dataset_failure.pk,
                ],
                "folder": self.folder_1.pk,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_207_MULTI_STATUS
        assert response.data["success"] == [str(uploads_dataset_success.pk)]
        assert len(response.data["error"]) == 1
        assert response.data["error"][0]["pk"] == str(uploads_dataset_failure.pk)
        assert "has no file attached" in response.data["error"][0]["error"]

        uploads_dataset_success.refresh_from_db()
        uploads_dataset_failure.refresh_from_db()
        uploads_version_failure.refresh_from_db()

        assert uploads_dataset_success.is_published() is True
        assert uploads_dataset_failure.is_published() is False
        assert uploads_dataset_failure.publication_date is None
        assert uploads_version_failure.publication_date is None
        assert uploads_dataset_failure.uploads_versions.count() == 1

    def test_upgrade_metadata(self, client, initial_users):
        """
        Ensure we can immediately upgrade metadata for a single uploads dataset.
        """
        set_request_for_user(initial_users["user_1"])

        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        uploads_dataset_1 = UploadsDataset.objects.create(
            name="Dataset 1",
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

        assert uploads_dataset_1.uploads_versions.count() == 1
        assert uploads_dataset_1.is_latest_version_upgradeable() is False
        assert uploads_dataset_1.metadata_upgrade_status == UploadsDataset.Status.FINISHED
        assert uploads_dataset_1.metadata_template is None

        url = reverse(
            "uploads-dataset-upgrade-metadata",
            kwargs={
                "pk": uploads_dataset_1.pk,
            },
        )

        response = client.post(
            url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        assert uploads_dataset_1.uploads_versions.count() == 1
        assert uploads_dataset_1.is_latest_version_upgradeable() is False
        assert uploads_dataset_1.metadata_upgrade_status == UploadsDataset.Status.FINISHED
        assert uploads_dataset_1.metadata_template is None

        uploads_dataset_1.metadata_template = metadata_template_1
        uploads_dataset_1.save()

        assert uploads_dataset_1.uploads_versions.count() == 1
        assert uploads_dataset_1.is_latest_version_upgradeable() is True
        assert uploads_dataset_1.metadata_upgrade_status == UploadsDataset.Status.FINISHED
        assert uploads_dataset_1.metadata_template == metadata_template_1

        url = reverse(
            "uploads-dataset-upgrade-metadata",
            kwargs={
                "pk": uploads_dataset_1.pk,
            },
        )

        response = client.post(
            url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        assert uploads_dataset_1.uploads_versions.count() == 2
        assert uploads_dataset_1.is_latest_version_upgradeable() is False
        assert uploads_dataset_1.metadata_upgrade_status == UploadsDataset.Status.FINISHED
        assert uploads_dataset_1.metadata_template == metadata_template_1

    def test_upgrade_metadata_failure_sets_error_status(self, client, initial_users, monkeypatch):
        """
        Ensure a failed immediate metadata upgrade stores ERROR in metadata_upgrade_status.
        """
        set_request_for_user(initial_users["user_1"])

        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        uploads_dataset = UploadsDataset.objects.create(
            name="Dataset 1",
        )

        UploadsVersion.objects.create(
            dataset=uploads_dataset,
            version_file=uploads_version_file,
        )

        def _mock_upgrade_latest_version_metadata(self, max_upgrade=False):
            return False, "upgrade failed"

        monkeypatch.setattr(UploadsDataset, "upgrade_latest_version_metadata", _mock_upgrade_latest_version_metadata)

        url = reverse(
            "uploads-dataset-upgrade-metadata",
            kwargs={
                "pk": uploads_dataset.pk,
            },
        )

        response = client.post(
            url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "upgrade failed"

        uploads_dataset.refresh_from_db()
        assert uploads_dataset.metadata_upgrade_status == UploadsDataset.Status.ERROR

    def test_upgrade_metadata_dereferences_field_without_version_link(self, client, initial_users):
        """
        Ensure upgrade dereferences stale metadata when the new field is not linked via previous_version_field.
        """
        set_request_for_user(initial_users["user_1"])

        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        dataset = UploadsDataset.objects.create(
            name="Dataset 1",
        )

        uploads_version = UploadsVersion.objects.create(
            dataset=dataset,
            version_file=uploads_version_file,
        )

        metadata_template_container = MetadataTemplateContainer.objects.create(
            name="Template container",
        )
        metadata_template_v1 = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container,
        )
        metadata_template_field_v1 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_v1,
            custom_key="integer_to_text",
            field_type=MetadataFieldType.INTEGER,
            mandatory=False,
        )
        metadata_template_field_v1.set_value("12")

        set_metadata(
            assigned_to_content_type=uploads_version.get_content_type(),
            assigned_to_object_id=uploads_version.pk,
            custom_key=metadata_template_field_v1.custom_key,
            field_type=metadata_template_field_v1.field_type,
            value="12",
            config=metadata_template_field_v1.config,
            metadata_template_field=metadata_template_field_v1,
            order=0,
        )
        uploads_version.metadata_templates.set([metadata_template_v1])

        metadata_template_v2 = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container,
        )
        metadata_template_field_v2 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_v2,
            custom_key=metadata_template_field_v1.custom_key,
            field_type=MetadataFieldType.TEXT,
            mandatory=False,
        )
        metadata_template_field_v2.set_value(None)

        dataset.metadata_template = metadata_template_v2
        dataset.save()

        url = reverse(
            "uploads-dataset-upgrade-metadata",
            kwargs={
                "pk": dataset.pk,
            },
        )

        response = client.post(
            url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        dataset.refresh_from_db()
        latest_version = dataset.latest_version
        assert latest_version is not None
        assert latest_version.metadata.count() == 2
        assert latest_version.metadata_templates.filter(pk=metadata_template_v2.pk).exists()
        assert latest_version.metadata_templates.filter(pk=metadata_template_v1.pk).exists() is False

        old_metadata = latest_version.metadata.get(metadata_template_field=None)
        assert old_metadata.custom_key == metadata_template_field_v1.custom_key
        assert old_metadata.field_type == MetadataFieldType.INTEGER
        assert old_metadata.get_value() == "12"

        upgraded_metadata = latest_version.metadata.get(metadata_template_field=metadata_template_field_v2)
        assert upgraded_metadata.field_type == MetadataFieldType.TEXT
        assert upgraded_metadata.get_value() is None

    def test_upgrade_metadata_dereferences_field_after_project_template_unassignment(
        self,
        client,
        initial_users,
    ):
        """
        Ensure upgrade dereferences stale metadata and unlinks template relations when a project assignment is removed.
        """
        set_request_for_user(initial_users["user_1"])

        project = Project.objects.create(
            name="Project 1",
        )
        folder = project.folders.first()

        metadata_template_container = MetadataTemplateContainer.objects.create(
            name="Project template container",
            assigned_to_content_type=project.get_content_type(),
            assigned_to_object_id=project.pk,
        )
        metadata_template = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container,
        )
        metadata_template_field = MetadataTemplateField.objects.create(
            metadata_template=metadata_template,
            custom_key="project_template_text_field",
            field_type=MetadataFieldType.TEXT,
            mandatory=False,
        )
        metadata_template_field.set_value("Template default")

        project.metadata_template = metadata_template
        project.save()

        dataset = UploadsDataset.objects.create(
            name="Dataset 1",
            folder=folder,
            publication_date=timezone.now(),
        )

        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )
        uploads_version = UploadsVersion.objects.create(
            dataset=dataset,
            version_file=uploads_version_file,
            publication_date=timezone.now(),
        )

        set_metadata(
            assigned_to_content_type=uploads_version.get_content_type(),
            assigned_to_object_id=uploads_version.pk,
            custom_key=metadata_template_field.custom_key,
            field_type=metadata_template_field.field_type,
            value="Dataset value",
            config=metadata_template_field.config,
            metadata_template_field=metadata_template_field,
            order=0,
        )
        uploads_version.link_currently_active_metadata_templates()

        dataset.refresh_from_db()
        assert dataset.is_latest_version_upgradeable() is False
        assert dataset.latest_version.metadata_templates.filter(pk=metadata_template.pk).exists()

        project.metadata_template = None
        project.save()

        dataset.refresh_from_db()
        assert dataset.is_latest_version_upgradeable() is True

        detail_url = reverse("uploads-dataset-detail", kwargs={"pk": dataset.pk})
        upgrade_payload_url = f"{detail_url}upgrade-metadata-payload/"
        response = client.get(upgrade_payload_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["template_versions"]) == 1
        assert response.data["template_versions"][0]["container_pk"] == str(metadata_template_container.pk)
        assert response.data["template_versions"][0]["old_template_pk"] == str(metadata_template.pk)
        assert response.data["template_versions"][0]["new_template_pk"] is None

        upgrade_url = f"{detail_url}upgrade-metadata/"
        response = client.post(
            upgrade_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        dataset.refresh_from_db()
        latest_version = dataset.latest_version
        assert latest_version is not None
        assert dataset.uploads_versions.count() == 2
        assert latest_version.metadata_templates.count() == 0

        metadata = latest_version.metadata.get(custom_key=metadata_template_field.custom_key)
        assert metadata.field_type == metadata_template_field.field_type
        assert metadata.get_value() == "Dataset value"
        assert metadata.metadata_template_field is None

    def test_bulk_upgrade_metadata(self, client, initial_users):
        """
        Ensure we can trigger the upgrade metadata process for uploads datasets in bulk.
        """
        set_request_for_user(initial_users["user_1"])

        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        uploads_dataset_1 = UploadsDataset.objects.create(
            name="Dataset 1",
        )

        UploadsVersion.objects.create(
            dataset=uploads_dataset_1,
            version_file=uploads_version_file,
        )

        uploads_dataset_2 = UploadsDataset.objects.create(
            name="Dataset 2",
        )

        UploadsVersion.objects.create(
            dataset=uploads_dataset_2,
            version_file=uploads_version_file,
        )

        uploads_dataset_3 = UploadsDataset.objects.create(
            name="Dataset 3",
        )

        UploadsVersion.objects.create(
            dataset=uploads_dataset_3,
            version_file=uploads_version_file,
        )

        metadata_template_container_1 = MetadataTemplateContainer.objects.create(
            name="Global metadata template container 1",
        )

        metadata_template_1 = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_1,
        )

        url = reverse("uploads-dataset-bulk-upgrade-metadata")

        # This request must trigger: "At least one uploads dataset provided does not exist."
        response = client.post(
            url,
            {
                "uploads_datasets": [
                    "7a49c23c-fa4d-4cb2-a619-abc4c2b3bbb1",
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

        # This request must trigger: "You must provide at least one uploads dataset."
        response = client.post(
            url,
            {
                "uploads_datasets": [],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        assert uploads_dataset_1.is_latest_version_upgradeable() is False
        assert uploads_dataset_1.metadata_upgrade_status == UploadsDataset.Status.FINISHED
        assert uploads_dataset_1.metadata_template is None

        assert uploads_dataset_2.is_latest_version_upgradeable() is False
        assert uploads_dataset_2.metadata_upgrade_status == UploadsDataset.Status.FINISHED
        assert uploads_dataset_2.metadata_template is None

        assert uploads_dataset_3.is_latest_version_upgradeable() is False
        assert uploads_dataset_3.metadata_upgrade_status == UploadsDataset.Status.FINISHED
        assert uploads_dataset_3.metadata_template is None

        response = client.post(
            url,
            {
                "uploads_datasets": [
                    uploads_dataset_1.pk,
                    uploads_dataset_2.pk,
                    uploads_dataset_3.pk,
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        uploads_dataset_1.metadata_template = metadata_template_1
        uploads_dataset_1.save()

        uploads_dataset_2.metadata_template = metadata_template_1
        uploads_dataset_2.save()

        assert uploads_dataset_1.is_latest_version_upgradeable() is True
        assert uploads_dataset_1.metadata_upgrade_status == UploadsDataset.Status.FINISHED
        assert uploads_dataset_1.metadata_template == metadata_template_1

        assert uploads_dataset_2.is_latest_version_upgradeable() is True
        assert uploads_dataset_2.metadata_upgrade_status == UploadsDataset.Status.FINISHED
        assert uploads_dataset_2.metadata_template == metadata_template_1

        assert uploads_dataset_3.is_latest_version_upgradeable() is False
        assert uploads_dataset_3.metadata_upgrade_status == UploadsDataset.Status.FINISHED
        assert uploads_dataset_3.metadata_template is None

        response = client.post(
            url,
            {
                "uploads_datasets": [
                    uploads_dataset_2.pk,
                    uploads_dataset_3.pk,
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        uploads_dataset_1.refresh_from_db()
        assert uploads_dataset_1.is_latest_version_upgradeable() is True
        assert uploads_dataset_1.metadata_upgrade_status == UploadsDataset.Status.FINISHED
        assert uploads_dataset_1.metadata_template == metadata_template_1

        uploads_dataset_2.refresh_from_db()
        assert uploads_dataset_2.is_latest_version_upgradeable() is True
        assert uploads_dataset_2.metadata_upgrade_status == UploadsDataset.Status.SCHEDULED
        assert uploads_dataset_2.metadata_template == metadata_template_1

        uploads_dataset_3.refresh_from_db()
        assert uploads_dataset_3.is_latest_version_upgradeable() is False
        assert uploads_dataset_3.metadata_upgrade_status == UploadsDataset.Status.FINISHED
        assert uploads_dataset_3.metadata_template is None

        set_request_for_user(initial_users["regular_user"])

        uploads_dataset_4 = UploadsDataset.objects.create(
            name="Dataset 4",
        )

        UploadsVersion.objects.create(
            dataset=uploads_dataset_4,
            version_file=uploads_version_file,
        )

        assert uploads_dataset_4.is_latest_version_upgradeable() is False
        assert uploads_dataset_4.metadata_upgrade_status == UploadsDataset.Status.FINISHED
        assert uploads_dataset_4.metadata_template is None

        uploads_dataset_4.metadata_template = metadata_template_1
        uploads_dataset_4.save()

        assert uploads_dataset_4.is_latest_version_upgradeable() is True
        assert uploads_dataset_4.metadata_upgrade_status == UploadsDataset.Status.FINISHED
        assert uploads_dataset_4.metadata_template == metadata_template_1

        auth_user(client, initial_users["user_1"])

        # This request must trigger: "You must not edit any published uploads datasets in a folder you haven't got the permission to."
        response = client.post(
            url,
            {
                "uploads_datasets": [
                    uploads_dataset_4.pk,
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        set_request_for_user(initial_users["user_1"])

        assert uploads_dataset_4.is_latest_version_upgradeable() is True
        assert uploads_dataset_4.metadata_upgrade_status == UploadsDataset.Status.FINISHED
        assert uploads_dataset_4.metadata_template == metadata_template_1

        auth_user(client, initial_users["regular_user"])

        response = client.post(
            url,
            {
                "uploads_datasets": [
                    uploads_dataset_4.pk,
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        uploads_dataset_4.refresh_from_db()
        assert uploads_dataset_4.is_latest_version_upgradeable() is True
        assert uploads_dataset_4.metadata_upgrade_status == UploadsDataset.Status.SCHEDULED
        assert uploads_dataset_4.metadata_template == metadata_template_1

    def test_bulk_upgrade_metadata_locked_by_other_user(self, client, initial_users):
        """
        Ensure we can trigger the upgrade metadata process for uploads datasets in bulk,
        even if they are locked by another user.
        """
        user_1 = initial_users["user_1"]
        user_2 = initial_users["user_2"]

        # Setup: user_1 creates a dataset
        set_request_for_user(user_1)

        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        dataset = UploadsDataset.objects.create(
            name="Locked Dataset",
            created_by=user_1,
        )

        UploadsVersion.objects.create(
            dataset=dataset,
            version_file=uploads_version_file,
        )

        metadata_template_container = MetadataTemplateContainer.objects.create(
            name="Test Container",
        )
        metadata_template = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container,
        )

        dataset.metadata_template = metadata_template
        dataset.save()

        # Verify it is upgradeable
        assert dataset.is_latest_version_upgradeable() is True
        assert dataset.metadata_upgrade_status == UploadsDataset.Status.FINISHED

        # Now user_2 locks the dataset
        set_request_for_user(user_2)
        dataset.lock()
        dataset.refresh_from_db()
        assert dataset.locked is True
        assert dataset.locked_by == user_2

        # User 1 tries to trigger bulk upgrade
        # We use the client authenticated as user_1
        auth_user(client, user_1)

        url = reverse("uploads-dataset-bulk-upgrade-metadata")
        response = client.post(
            url,
            {
                "uploads_datasets": [dataset.pk],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED

        # Refresh and check status
        dataset.refresh_from_db()
        # It should be SCHEDULED despite the lock
        assert dataset.metadata_upgrade_status == UploadsDataset.Status.SCHEDULED

    def test_metadata_template_container_pool(self, client, initial_users):
        """
        Ensure we can set and get the dataset's metadata template container pool.
        """
        url = reverse(
            "folder-detail",
            kwargs={
                "pk": self.folder_1.pk,
            },
        )
        folder_action_url = f"{url}metadata-template-container-pool/"

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": self.uploads_dataset_1.pk,
            },
        )
        dataset_action_url = f"{url}metadata-template-container-pool/"

        response = client.get(dataset_action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0

        response = client.put(
            folder_action_url,
            {
                "metadata_template_container": [
                    "8530f9ea-d04a-40b1-b502-82b9cdc78727",
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

        response = client.put(
            folder_action_url,
            {
                "metadata_template_container": [
                    self.metadata_template_container_1.pk,
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        response = client.get(dataset_action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        metadata_template_container_2 = MetadataTemplateContainer.objects.create(
            name="Metadata Template Container 2",
        )

        metadata_template_2 = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_2,
        )

        response = client.put(
            folder_action_url,
            {
                "metadata_template_container": [
                    metadata_template_container_2.pk,
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        response = client.get(dataset_action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        response = client.put(
            folder_action_url,
            {
                "metadata_template_container": [
                    self.metadata_template_container_1.pk,
                    metadata_template_container_2.pk,
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        response = client.get(dataset_action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

        response = client.put(
            folder_action_url,
            {
                "metadata_template_container": [],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        response = client.get(dataset_action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0

        self.project_1.metadata_template = self.metadata_template_1
        self.project_1.save()

        response = client.get(dataset_action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        self.folder_1.metadata_template = metadata_template_2
        self.folder_1.save()

        response = client.get(dataset_action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

        metadata_template_container_3 = MetadataTemplateContainer.objects.create(
            name="Dataset metadata template container",
            assigned_to_content_type=self.uploads_dataset_1.get_content_type(),
            assigned_to_object_id=self.uploads_dataset_1.pk,
        )

        metadata_template_3 = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_3,
        )

        self.uploads_dataset_1.metadata_template = metadata_template_3
        self.uploads_dataset_1.save()

        response = client.get(dataset_action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 3

        self.uploads_dataset_1.metadata_template = None
        self.uploads_dataset_1.save()

        response = client.get(dataset_action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

        self.folder_1.metadata_template = None
        self.folder_1.save()

        response = client.get(dataset_action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        self.project_1.metadata_template = None
        self.project_1.save()

        response = client.get(dataset_action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0

    def test_publish_to_folder_with_view_permission_only(self, client, initial_users):
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

        auth_user(client, initial_users["user_2"])

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": dataset_1.pk,
            },
        )

        # We can't use reverse for the action as this would collide with other routers
        action_url = f"{url}publish/"

        response = client.post(
            action_url,
            {
                "folder": folder_1.pk,
            },
            format="multipart",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        set_request_for_user(initial_users["user_1"])

        folder_permission_user_2.can_edit = True
        folder_permission_user_2.save()

        auth_user(client, initial_users["user_2"])

        response = client.post(
            action_url,
            {
                "folder": folder_1.pk,
            },
            format="multipart",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["publication_date"] is not None

    def _create_dataset_with_upgrade_conflict(self):
        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        dataset = UploadsDataset.objects.create(
            name="Dataset upgrade conflict test",
        )

        uploads_version = UploadsVersion.objects.create(
            dataset=dataset,
            version_file=uploads_version_file,
        )

        metadata_template_container = MetadataTemplateContainer.objects.create(
            name="Upgrade conflict test container",
        )
        metadata_template_v1 = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container,
        )

        metadata_template_field_v1 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_v1,
            custom_key="upgrade_conflict_selection",
            field_type=MetadataFieldType.SELECTION,
            config=dict(
                options=[
                    "Option A",
                    "Option B",
                ],
            ),
            mandatory=False,
        )
        metadata_template_field_v1.set_value("Option A")

        set_metadata(
            assigned_to_content_type=uploads_version.get_content_type(),
            assigned_to_object_id=uploads_version.pk,
            custom_key=metadata_template_field_v1.custom_key,
            field_type=metadata_template_field_v1.field_type,
            value="Option A",
            config=metadata_template_field_v1.config,
            metadata_template_field=metadata_template_field_v1,
            order=0,
        )

        uploads_version.metadata_templates.set(
            [metadata_template_v1],
        )

        metadata_template_v2 = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container,
        )
        metadata_template_field_v2 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_v2,
            custom_key="upgrade_conflict_text",
            field_type=MetadataFieldType.TEXT,
            mandatory=False,
            previous_version_field=metadata_template_field_v1,
        )
        metadata_template_field_v2.set_value(None)

        dataset.metadata_template = metadata_template_v2
        dataset.save()

        assert dataset.uploads_versions.count() == 1
        assert dataset.is_latest_version_upgradeable() is True

        metadata = dataset.latest_version.metadata.get(
            metadata_template_field=metadata_template_field_v1,
        )

        return (
            dataset,
            metadata_template_container,
            metadata_template_v1,
            metadata_template_v2,
            metadata_template_field_v2,
            metadata,
        )

    def _create_dataset_with_upgrade_option_mismatch_conflict(self):
        uploads_version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        dataset = UploadsDataset.objects.create(
            name="Dataset upgrade option mismatch test",
        )

        uploads_version = UploadsVersion.objects.create(
            dataset=dataset,
            version_file=uploads_version_file,
        )

        metadata_template_container = MetadataTemplateContainer.objects.create(
            name="Upgrade option mismatch test container",
        )
        metadata_template_v1 = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container,
        )

        metadata_template_field_v1 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_v1,
            custom_key="upgrade_conflict_selection",
            field_type=MetadataFieldType.SELECTION,
            config=dict(
                options=[
                    "1",
                    "2",
                ],
            ),
            mandatory=False,
        )
        metadata_template_field_v1.set_value("1")

        set_metadata(
            assigned_to_content_type=uploads_version.get_content_type(),
            assigned_to_object_id=uploads_version.pk,
            custom_key=metadata_template_field_v1.custom_key,
            field_type=metadata_template_field_v1.field_type,
            value="1",
            config=metadata_template_field_v1.config,
            metadata_template_field=metadata_template_field_v1,
            order=0,
        )

        uploads_version.metadata_templates.set(
            [metadata_template_v1],
        )

        metadata_template_v2 = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container,
        )
        metadata_template_field_v2 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_v2,
            custom_key="upgrade_conflict_multi_selection",
            field_type=MetadataFieldType.MULTI_SELECTION,
            config=dict(
                options=[
                    "Option 1",
                    "Option 2",
                ],
            ),
            mandatory=False,
            previous_version_field=metadata_template_field_v1,
        )
        metadata_template_field_v2.set_value(
            [
                "Option 1",
                "Option 2",
            ],
        )

        dataset.metadata_template = metadata_template_v2
        dataset.save()

        assert dataset.uploads_versions.count() == 1
        assert dataset.is_latest_version_upgradeable() is True

        metadata = dataset.latest_version.metadata.get(
            metadata_template_field=metadata_template_field_v1,
        )

        return (
            dataset,
            metadata_template_container,
            metadata_template_v1,
            metadata_template_v2,
            metadata_template_field_v2,
            metadata,
        )

    def test_upgrade_metadata_payload_lists_conflicts_and_template_versions(self, client, initial_users):
        """
        Ensure the conflict payload only returns conflict and template-version context.
        """
        set_request_for_user(initial_users["user_1"])
        (
            dataset,
            metadata_template_container,
            metadata_template_v1,
            metadata_template_v2,
            metadata_template_field_v2,
            metadata,
        ) = self._create_dataset_with_upgrade_conflict()

        detail_url = reverse("uploads-dataset-detail", kwargs={"pk": dataset.pk})
        upgrade_payload_url = f"{detail_url}upgrade-metadata-payload/"

        response = client.get(upgrade_payload_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert "metadata" not in response.data
        assert "metadata_templates" not in response.data
        assert response.data["base_version_pk"] == str(dataset.latest_version.pk)
        assert len(response.data["template_versions"]) == 1
        assert response.data["template_versions"][0]["container_pk"] == str(metadata_template_container.pk)
        assert response.data["template_versions"][0]["old_template_pk"] == str(metadata_template_v1.pk)
        assert response.data["template_versions"][0]["new_template_pk"] == str(metadata_template_v2.pk)
        assert len(response.data["conflicts"]) == 1

        conflict = response.data["conflicts"][0]
        assert conflict["conflict_id"] == f"{metadata.pk}:{metadata_template_field_v2.pk}"
        assert "selection values" in conflict["error"]
        assert conflict["old"]["field_type"] == MetadataFieldType.SELECTION
        assert conflict["old"]["metadata_template_field"]["custom_key"] == "upgrade_conflict_selection"
        assert conflict["old"]["metadata_template_field"]["metadata_template"]["pk"] == str(metadata_template_v1.pk)
        assert conflict["new"]["field_type"] == MetadataFieldType.TEXT
        assert conflict["new"]["metadata_template_field"]["custom_key"] == "upgrade_conflict_text"
        assert conflict["new"]["metadata_template_field"]["metadata_template"]["pk"] == str(metadata_template_v2.pk)
        assert conflict["new"]["value"] is None

    def test_upgrade_metadata_payload_resolves_branched_template_field_history(self, client, initial_users):
        """
        Ensure upgrade payload generation can target one branch of restored template field history.
        """
        set_request_for_user(initial_users["user_1"])
        (
            dataset,
            metadata_template_container,
            metadata_template_v1,
            _metadata_template_v2,
            _metadata_template_field_v2,
            metadata,
        ) = self._create_dataset_with_upgrade_conflict()

        metadata_template_v3 = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container,
        )
        metadata_template_field_v3 = MetadataTemplateField.objects.create(
            metadata_template=metadata_template_v3,
            custom_key="upgrade_conflict_text_branch",
            field_type=MetadataFieldType.TEXT,
            mandatory=False,
            previous_version_field=metadata.metadata_template_field,
        )
        metadata_template_field_v3.set_value(None)

        dataset.metadata_template = metadata_template_v3
        dataset.save()

        detail_url = reverse("uploads-dataset-detail", kwargs={"pk": dataset.pk})
        upgrade_payload_url = f"{detail_url}upgrade-metadata-payload/"

        response = client.get(upgrade_payload_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["base_version_pk"] == str(dataset.latest_version.pk)
        assert len(response.data["template_versions"]) == 1
        assert response.data["template_versions"][0]["container_pk"] == str(metadata_template_container.pk)
        assert response.data["template_versions"][0]["old_template_pk"] == str(metadata_template_v1.pk)
        assert response.data["template_versions"][0]["new_template_pk"] == str(metadata_template_v3.pk)
        assert len(response.data["conflicts"]) == 1

        conflict = response.data["conflicts"][0]
        assert conflict["conflict_id"] == f"{metadata.pk}:{metadata_template_field_v3.pk}"
        assert conflict["new"]["metadata_template_field"]["pk"] == str(metadata_template_field_v3.pk)

    def test_resolve_upgrade_metadata_conflict_uses_resolutions_only(self, client, initial_users):
        """
        Ensure conflict resolution uses base_version_pk + resolutions and links recomputed template versions.
        """
        set_request_for_user(initial_users["user_1"])
        (
            dataset,
            _metadata_template_container,
            _metadata_template_v1,
            metadata_template_v2,
            metadata_template_field_v2,
            _metadata,
        ) = self._create_dataset_with_upgrade_conflict()

        detail_url = reverse("uploads-dataset-detail", kwargs={"pk": dataset.pk})
        upgrade_payload_url = f"{detail_url}upgrade-metadata-payload/"
        resolve_url = f"{detail_url}resolve-upgrade-metadata-conflict/"

        response = client.get(upgrade_payload_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        base_version_pk = response.data["base_version_pk"]
        conflict_id = response.data["conflicts"][0]["conflict_id"]

        response = client.post(
            resolve_url,
            {
                "base_version_pk": base_version_pk,
                "resolutions": [
                    {
                        "conflict_id": conflict_id,
                        "value": "Manually resolved text",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        dataset.refresh_from_db()
        assert dataset.uploads_versions.count() == 2
        assert dataset.is_latest_version_upgradeable() is False

        dataset.latest_version.refresh_from_db()
        assert dataset.latest_version.metadata_templates.filter(pk=metadata_template_v2.pk).exists()
        metadata = dataset.latest_version.metadata.get(
            metadata_template_field=metadata_template_field_v2,
        )
        assert metadata.field_type == MetadataFieldType.TEXT
        assert metadata.get_value() == "Manually resolved text"

    def test_upgrade_metadata_payload_lists_option_mismatch_conflict_for_selection_to_multi_selection(
        self,
        client,
        initial_users,
    ):
        """
        Ensure option mismatches after successful conversion are surfaced as upgrade conflicts.
        """
        set_request_for_user(initial_users["user_1"])
        (
            dataset,
            metadata_template_container,
            metadata_template_v1,
            metadata_template_v2,
            metadata_template_field_v2,
            metadata,
        ) = self._create_dataset_with_upgrade_option_mismatch_conflict()

        detail_url = reverse("uploads-dataset-detail", kwargs={"pk": dataset.pk})
        upgrade_payload_url = f"{detail_url}upgrade-metadata-payload/"

        response = client.get(upgrade_payload_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["base_version_pk"] == str(dataset.latest_version.pk)
        assert len(response.data["template_versions"]) == 1
        assert response.data["template_versions"][0]["container_pk"] == str(metadata_template_container.pk)
        assert response.data["template_versions"][0]["old_template_pk"] == str(metadata_template_v1.pk)
        assert response.data["template_versions"][0]["new_template_pk"] == str(metadata_template_v2.pk)
        assert len(response.data["conflicts"]) == 1

        conflict = response.data["conflicts"][0]
        assert conflict["conflict_id"] == f"{metadata.pk}:{metadata_template_field_v2.pk}"
        assert "configured options" in conflict["error"]
        assert conflict["old"]["field_type"] == MetadataFieldType.SELECTION
        assert conflict["new"]["field_type"] == MetadataFieldType.MULTI_SELECTION
        assert conflict["new"]["value"] is None

    def test_resolve_upgrade_metadata_conflict_succeeds_for_option_mismatch_conflict(
        self,
        client,
        initial_users,
    ):
        """
        Ensure option-mismatch conflicts can be resolved via manual value selection.
        """
        set_request_for_user(initial_users["user_1"])
        (
            dataset,
            _metadata_template_container,
            _metadata_template_v1,
            metadata_template_v2,
            metadata_template_field_v2,
            _metadata,
        ) = self._create_dataset_with_upgrade_option_mismatch_conflict()

        detail_url = reverse("uploads-dataset-detail", kwargs={"pk": dataset.pk})
        upgrade_payload_url = f"{detail_url}upgrade-metadata-payload/"
        resolve_url = f"{detail_url}resolve-upgrade-metadata-conflict/"

        response = client.get(upgrade_payload_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        base_version_pk = response.data["base_version_pk"]
        conflict_id = response.data["conflicts"][0]["conflict_id"]

        response = client.post(
            resolve_url,
            {
                "base_version_pk": base_version_pk,
                "resolutions": [
                    {
                        "conflict_id": conflict_id,
                        "value": [
                            "  Option 1",
                            "Option 2  ",
                        ],
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        dataset.refresh_from_db()
        assert dataset.uploads_versions.count() == 2
        assert dataset.is_latest_version_upgradeable() is False

        dataset.latest_version.refresh_from_db()
        assert dataset.latest_version.metadata_templates.filter(pk=metadata_template_v2.pk).exists()
        metadata = dataset.latest_version.metadata.get(
            metadata_template_field=metadata_template_field_v2,
        )
        assert metadata.field_type == MetadataFieldType.MULTI_SELECTION
        assert metadata.get_value() == [
            "Option 1",
            "Option 2",
        ]

    def test_resolve_upgrade_metadata_conflict_sets_status_finished_after_error(self, client, initial_users):
        """
        Ensure manual conflict resolve resets metadata_upgrade_status to FINISHED from an error state.
        """
        set_request_for_user(initial_users["user_1"])
        (dataset, _, _, _, _, _) = self._create_dataset_with_upgrade_conflict()

        detail_url = reverse("uploads-dataset-detail", kwargs={"pk": dataset.pk})
        upgrade_payload_url = f"{detail_url}upgrade-metadata-payload/"
        resolve_url = f"{detail_url}resolve-upgrade-metadata-conflict/"

        dataset.metadata_upgrade_status = UploadsDataset.Status.ERROR
        dataset.save()

        payload_response = client.get(upgrade_payload_url, format="json")
        assert payload_response.status_code == status.HTTP_200_OK

        response = client.post(
            resolve_url,
            {
                "base_version_pk": payload_response.data["base_version_pk"],
                "resolutions": [
                    {
                        "conflict_id": payload_response.data["conflicts"][0]["conflict_id"],
                        "value": "Resolved after error",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        dataset.refresh_from_db()
        assert dataset.metadata_upgrade_status == UploadsDataset.Status.FINISHED

    def test_resolve_upgrade_metadata_conflict_rejects_stale_base_version(self, client, initial_users):
        """
        Ensure stale payloads are rejected when latest version changed in between.
        """
        set_request_for_user(initial_users["user_1"])
        (dataset, _, _, _, _, _) = self._create_dataset_with_upgrade_conflict()

        detail_url = reverse("uploads-dataset-detail", kwargs={"pk": dataset.pk})
        upgrade_payload_url = f"{detail_url}upgrade-metadata-payload/"
        resolve_url = f"{detail_url}resolve-upgrade-metadata-conflict/"

        response = client.get(upgrade_payload_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        payload = response.data

        UploadsVersion.objects.create(
            dataset=dataset,
            version_file=dataset.latest_version.version_file,
        )

        response = client.post(
            resolve_url,
            {
                "base_version_pk": payload["base_version_pk"],
                "resolutions": [
                    {
                        "conflict_id": payload["conflicts"][0]["conflict_id"],
                        "value": "Manually resolved text",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_409_CONFLICT
        assert "outdated" in response.data["error"]

    def test_resolve_upgrade_metadata_conflict_requires_resolution_for_each_conflict(self, client, initial_users):
        """
        Ensure each conflict must be resolved before creating a new version.
        """
        set_request_for_user(initial_users["user_1"])
        (dataset, _, _, _, _, _) = self._create_dataset_with_upgrade_conflict()

        detail_url = reverse("uploads-dataset-detail", kwargs={"pk": dataset.pk})
        upgrade_payload_url = f"{detail_url}upgrade-metadata-payload/"
        resolve_url = f"{detail_url}resolve-upgrade-metadata-conflict/"

        response = client.get(upgrade_payload_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        payload = response.data

        response = client.post(
            resolve_url,
            {
                "base_version_pk": payload["base_version_pk"],
                "resolutions": [],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert payload["conflicts"][0]["conflict_id"] in response.data["missing_conflicts"]


@pytest.mark.django_db
class TestUploadsDatasetLockStatusMixin:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.project_1 = Project.objects.create(
            name="Project 1",
        )

        self.dataset_1 = UploadsDataset.objects.create(
            name="Dataset 1",
            folder=self.project_1.folders.first(),
        )

    def test_lock(self, client, initial_users):
        """
        Ensure we have an expected lock behavior.
        """
        set_request_for_user(initial_users["user_1"])

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": self.dataset_1.pk,
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
                "name": "Dataset 1",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Dataset 1"

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
                "name": "Dataset 1 - Edit #1",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Dataset 1 - Edit #1"

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
        assert response.status_code == status.HTTP_404_NOT_FOUND

        assert self.project_1.members_count == 1
        assert self.project_1.folders.first().members_count == 1

        ProjectMembership.objects.create(
            project=self.project_1,
            member=initial_users["user_2"],
            is_project_admin=True,
        )

        assert self.project_1.members_count == 2
        assert self.project_1.folders.first().members_count == 2

        folder_permission = FolderPermission.objects.get(
            project_membership__member=initial_users["user_2"],
            folder=self.project_1.folders.first(),
        )
        assert folder_permission.is_folder_admin is True
        assert folder_permission.is_metadata_template_admin is True
        assert folder_permission.can_edit is True

        response = client.patch(
            url,
            {
                "name": "Dataset 1 - Edit #3",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Dataset 1 - Edit #3"

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
class TestUploadsVersionAPI:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        storage_1 = DynamicStorage.objects.get(
            default=True,
        )

        self.project_1 = Project.objects.create(
            name="Project 1",
        )

        self.folder_1 = self.project_1.folders.first()
        self.folder_1.storage = storage_1
        self.folder_1.save()

        self.uploads_version_file_1 = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        self.uploads_dataset_1 = UploadsDataset.objects.create(
            name="Dataset 1",
        )

        self.uploads_version_1 = UploadsVersion.objects.create(
            dataset=self.uploads_dataset_1,
            version_file=self.uploads_version_file_1,
        )

    def test_read_uploads_version_list(self, client):
        """
        Ensure we can't read the uploads versions list without any filter.
        """
        url = reverse("uploads-version-list")

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0

        url_filter = f"{url}?dataset={self.uploads_dataset_1.pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        url_filter = f"{url}?dataset={self.uploads_dataset_1.pk}&metadata_is_complete=true"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        url_filter = f"{url}?dataset={self.uploads_dataset_1.pk}&metadata_is_complete=false"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0

        self.uploads_version_1.metadata_is_complete = False
        self.uploads_version_1.save()
        assert self.uploads_version_1.metadata_is_complete is False

        url_filter = f"{url}?dataset={self.uploads_dataset_1.pk}&metadata_is_complete=true"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0

        url_filter = f"{url}?dataset={self.uploads_dataset_1.pk}&metadata_is_complete=false"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

    def test_create_uploads_version(self, client):
        """
        Ensure we can't create a new uploads version on its own.
        """
        url = reverse("uploads-version-list")

        response = client.post(url, format="json")
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_read_uploads_version_details(self, client):
        """
        Ensure we can read the uploads version details.
        """
        url = reverse(
            "uploads-version-detail",
            kwargs={
                "pk": self.uploads_version_1.pk,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["pk"] == str(self.uploads_version_1.pk)

    def test_check_uploads_version_status(self, client):
        """
        Ensure the status of the uploads version changes after an update.
        """
        self.uploads_version_1.status = UploadsVersion.Status.SCHEDULED
        self.uploads_version_1.save()

        url = reverse(
            "uploads-version-detail",
            kwargs={
                "pk": self.uploads_version_1.pk,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == UploadsVersion.Status.SCHEDULED

        self.uploads_version_1.status = UploadsVersion.Status.FINISHED
        self.uploads_version_1.save()

        url = reverse(
            "uploads-version-detail",
            kwargs={
                "pk": self.uploads_version_1.pk,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == UploadsVersion.Status.FINISHED

        response = client.patch(
            url,
            {
                "metadata": [
                    {
                        "custom_key": "custom_key_1",
                        "value": "custom value 1",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["metadata"]) == 0
        assert response.data["status"] == UploadsVersion.Status.FINISHED

        response = client.patch(
            url,
            {
                "status": UploadsVersion.Status.SCHEDULED,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == UploadsVersion.Status.SCHEDULED

    def test_change_uploads_version_details(self, client):
        """
        Ensure we can change the uploads version details.
        """
        url = reverse(
            "uploads-version-detail",
            kwargs={
                "pk": self.uploads_version_1.pk,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == self.uploads_version_1.name
        assert response.data["status"] == UploadsVersion.Status.SCHEDULED

        name = "Test name"

        response = client.patch(
            url,
            {
                "name": name,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == name

        response = client.patch(
            url,
            {
                "metadata": [
                    {
                        "custom_key": "custom_key_1",
                        "value": "custom value 1",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["metadata"]) == 0

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": self.uploads_version_1.dataset.pk,
            },
        )

        # We can't use reverse for the action as this would collide with other routers
        action_url = f"{url}publish/"

        response = client.post(
            action_url,
            {
                "folder": self.project_1.folders.first().pk,
            },
            format="multipart",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["publication_date"] is not None

        url = reverse(
            "uploads-version-detail",
            kwargs={
                "pk": self.uploads_version_1.pk,
            },
        )

        name = "Test name 2"

        response = client.patch(
            url,
            {
                "name": name,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == name

    def test_filter_versions_by_dataset_folder_excludes_versions_without_dataset(self, client):
        """
        Test that filtering versions by dataset__folder no longer returns versions without a dataset.
        This test verifies the fix that removed outdated functionality that allowed listing
        versions without a dataset when filtering by dataset__folder.
        """
        # Create a version without a dataset (previously could be returned incorrectly)
        uploads_version_without_dataset = UploadsVersion.objects.create(
            name="Version without dataset",
            publication_date=timezone.now(),  # Make it published
        )

        # Create a dataset in folder_1 and a version with this dataset
        dataset_with_folder = UploadsDataset.objects.create(
            name="Dataset with folder",
            folder=self.folder_1,
            publication_date=timezone.now(),  # Make it published
        )
        uploads_version_with_dataset = UploadsVersion.objects.create(
            name="Version with dataset",
            dataset=dataset_with_folder,
            publication_date=timezone.now(),  # Make it published
        )

        # Test filtering by dataset__folder - should only return versions with datasets
        url = reverse("uploads-version-list")
        response = client.get(f"{url}?dataset__folder={self.folder_1.pk}", format="json")

        assert response.status_code == status.HTTP_200_OK
        results = response.data["results"]

        # Verify that only versions with datasets are returned
        version_ids = [item["pk"] for item in results]
        assert str(uploads_version_with_dataset.pk) in version_ids
        assert str(uploads_version_without_dataset.pk) not in version_ids

    def test_filter_versions_by_dataset_with_permissions(self, client, initial_users):
        """
        Test that filtering versions by dataset respects folder permissions.
        """
        # A user without permissions
        user_2 = initial_users["user_2"]

        # Create two datasets in different folders
        folder_2 = Folder.objects.create(
            name="Folder 2",
            project=self.project_1,
            storage=self.folder_1.storage,
        )

        # Dataset in folder_1 (accessible to user_1)
        dataset_1 = UploadsDataset.objects.create(
            name="Dataset 1",
            folder=self.folder_1,
            publication_date=timezone.now(),
        )
        version_1 = UploadsVersion.objects.create(
            name="Version 1",
            dataset=dataset_1,
            publication_date=timezone.now(),
        )

        # Dataset in folder_2 (we'll grant permission to user_2)
        dataset_2 = UploadsDataset.objects.create(
            name="Dataset 2",
            folder=folder_2,
            publication_date=timezone.now(),
        )
        version_2 = UploadsVersion.objects.create(
            name="Version 2",
            dataset=dataset_2,
            publication_date=timezone.now(),
        )

        assert self.folder_1.members_count == 1

        assert folder_2.members_count == 1

        # Give user_2 permission to folder_2 but not folder_1
        project_membership_2 = ProjectMembership.objects.create(
            project=self.project_1,
            member=initial_users["user_2"],
        )

        FolderPermission.objects.create(
            folder=folder_2,
            project_membership=project_membership_2,
        )

        assert folder_2.members_count == 2

        # Test filtering by dataset with user_1
        url = reverse("uploads-version-list")
        response = client.get(f"{url}?dataset={dataset_1.pk}", format="json")
        assert response.status_code == status.HTTP_200_OK
        results = response.data["results"]
        assert len(results) == 1
        assert results[0]["pk"] == str(version_1.pk)

        response = client.get(f"{url}?dataset={dataset_2.pk}", format="json")
        assert response.status_code == status.HTTP_200_OK
        results = response.data["results"]
        assert len(results) == 1
        assert results[0]["pk"] == str(version_2.pk)

        # Switch to user_2
        set_request_for_user(user_2)
        client.force_authenticate(user=user_2)

        # user_2 can't access dataset_1's versions
        response = client.get(f"{url}?dataset={dataset_1.pk}", format="json")
        assert response.status_code == status.HTTP_200_OK
        results = response.data["results"]
        assert len(results) == 0  # No results due to permission check

        # But user_2 can access dataset_2's versions
        response = client.get(f"{url}?dataset={dataset_2.pk}", format="json")
        assert response.status_code == status.HTTP_200_OK
        results = response.data["results"]
        assert len(results) == 1
        assert results[0]["pk"] == str(version_2.pk)

    def test_folder_viewable_queryset_excludes_versions_without_dataset(self):
        """
        Test that the folder_viewable queryset method properly excludes versions without datasets
        when filtering by dataset__folder. This verifies the fix for the outdated functionality.
        """
        # Create a version without a dataset
        version_without_dataset = UploadsVersion.objects.create(
            name="Version without dataset",
            publication_date=timezone.now(),  # Make it published
        )

        # Create a version with a dataset in folder_1
        dataset_in_folder = UploadsDataset.objects.create(
            name="Dataset in folder",
            folder=self.folder_1,
            publication_date=timezone.now(),  # Make it published
        )
        version_with_dataset = UploadsVersion.objects.create(
            name="Version with dataset",
            dataset=dataset_in_folder,
            publication_date=timezone.now(),  # Make it published
        )

        # Get versions using the folder_viewable queryset method
        filtered_versions = UploadsVersion.objects.folder_viewable(folder_pk=self.folder_1.pk)

        # Verify that only versions with datasets in the specified folder are returned
        assert filtered_versions.count() == 1
        assert filtered_versions.first().pk == version_with_dataset.pk

        # Verify that the version without a dataset is excluded
        version_pks = [v.pk for v in filtered_versions]
        assert version_without_dataset.pk not in version_pks

    def test_uploads_version_diff(self, client, initial_users):
        """
        Ensure we have a correct diff between two uploads versions.
        """
        url = reverse("uploads-dataset-list")

        response = client.post(
            url,
            {
                "name": "Test dataset",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        dataset = response.data

        uploads_dataset_1 = UploadsDataset.objects.get(pk=dataset["pk"])

        url_dataset = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": dataset["pk"],
            },
        )

        response = client.get(url_dataset, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Test dataset"

        # We can't use reverse for the action as this would collide with other routers
        file_action_url = f"{url_dataset}file/"
        version_action_url = f"{url_dataset}version/"

        response = client.post(
            file_action_url,
            {
                "file": sample_file,
            },
            format="multipart",
        )
        assert response.status_code == status.HTTP_201_CREATED

        response = client.post(
            version_action_url,
            {
                "metadata": [
                    {
                        "custom_key": "custom_key_1",
                        "value": "custom value",
                    },
                    {
                        "custom_key": "custom_key_2",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        uploads_version_1 = UploadsVersion.objects.get(pk=response.data["pk"])

        uploads_version_1_metadata_1 = Metadata.objects.get(
            assigned_to_content_type=uploads_version_1.get_content_type(),
            assigned_to_object_id=uploads_version_1.pk,
            custom_key="custom_key_1",
        )

        uploads_version_1_metadata_2 = Metadata.objects.get(
            assigned_to_content_type=uploads_version_1.get_content_type(),
            assigned_to_object_id=uploads_version_1.pk,
            custom_key="custom_key_2",
        )

        url_uploads_version_1 = reverse(
            "uploads-version-detail",
            kwargs={
                "pk": uploads_version_1.pk,
            },
        )

        response = client.get(url_uploads_version_1, format="json")
        assert response.status_code == status.HTTP_200_OK

        response = client.get(f"{url_uploads_version_1}diff/", format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        response = client.get(f"{url_uploads_version_1}diff/?compare=test", format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        response = client.get(
            f"{url_uploads_version_1}diff/?compare=85bc7d2f-e712-40c7-b39e-fad2839fa758",
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

        response = client.get(f"{url_uploads_version_1}diff/?compare={uploads_version_1.pk}", format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        response = client.post(
            version_action_url,
            {
                "metadata": [
                    {
                        "custom_key": "custom_key_1",
                        "value": "custom value",
                        "previous_version_field": uploads_version_1_metadata_1.pk,
                    },
                    {
                        "custom_key": "custom_key_2",
                        "value": "custom value 2",
                        "previous_version_field": uploads_version_1_metadata_2.pk,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        uploads_version_2 = UploadsVersion.objects.get(pk=response.data["pk"])

        uploads_version_2_metadata_1 = Metadata.objects.get(
            assigned_to_content_type=uploads_version_2.get_content_type(),
            assigned_to_object_id=uploads_version_2.pk,
            custom_key="custom_key_1",
        )

        uploads_version_2_metadata_2 = Metadata.objects.get(
            assigned_to_content_type=uploads_version_2.get_content_type(),
            assigned_to_object_id=uploads_version_2.pk,
            custom_key="custom_key_2",
        )

        url_uploads_version_2 = reverse(
            "uploads-version-detail",
            kwargs={
                "pk": uploads_version_2.pk,
            },
        )

        response = client.get(url_uploads_version_2, format="json")
        assert response.status_code == status.HTTP_200_OK

        response = client.get(f"{url_uploads_version_2}diff/?compare={uploads_version_1.pk}", format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["version"]["old"]["pk"] == str(uploads_version_1.pk)
        assert response.data["version"]["new"]["pk"] == str(uploads_version_2.pk)
        assert len(response.data["metadata"]) == 1
        assert "version_file" not in response.data
        assert response.data["metadata"][0]["old"]["pk"] == str(uploads_version_1_metadata_2.pk)
        assert response.data["metadata"][0]["old"]["value"] == uploads_version_1_metadata_2.get_value()
        assert response.data["metadata"][0]["new"]["pk"] == str(uploads_version_2_metadata_2.pk)
        assert response.data["metadata"][0]["new"]["value"] == uploads_version_2_metadata_2.get_value()
        assert response.data["metadata"][0]["old"]["value"] != response.data["metadata"][0]["new"]["value"]

        response = client.get(f"{url_uploads_version_1}diff/?compare={uploads_version_2.pk}", format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["version"]["old"]["pk"] == str(uploads_version_1.pk)
        assert response.data["version"]["new"]["pk"] == str(uploads_version_2.pk)
        assert len(response.data["metadata"]) == 1
        assert "version_file" not in response.data
        assert response.data["metadata"][0]["old"]["pk"] == str(uploads_version_1_metadata_2.pk)
        assert response.data["metadata"][0]["old"]["value"] == uploads_version_1_metadata_2.get_value()
        assert response.data["metadata"][0]["new"]["pk"] == str(uploads_version_2_metadata_2.pk)
        assert response.data["metadata"][0]["new"]["value"] == uploads_version_2_metadata_2.get_value()
        assert response.data["metadata"][0]["old"]["value"] != response.data["metadata"][0]["new"]["value"]

        response = client.post(
            version_action_url,
            {
                "metadata": [
                    {
                        "custom_key": "custom_key_3",
                        "value": "custom value",
                    },
                    {
                        "custom_key": "custom_key_4",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        uploads_version_3 = UploadsVersion.objects.get(pk=response.data["pk"])

        uploads_version_3_metadata_1 = Metadata.objects.get(
            assigned_to_content_type=uploads_version_3.get_content_type(),
            assigned_to_object_id=uploads_version_3.pk,
            custom_key="custom_key_3",
        )

        uploads_version_3_metadata_2 = Metadata.objects.get(
            assigned_to_content_type=uploads_version_3.get_content_type(),
            assigned_to_object_id=uploads_version_3.pk,
            custom_key="custom_key_4",
        )

        url_uploads_version_3 = reverse(
            "uploads-version-detail",
            kwargs={
                "pk": uploads_version_3.pk,
            },
        )

        response = client.get(url_uploads_version_3, format="json")
        assert response.status_code == status.HTTP_200_OK

        response = client.get(f"{url_uploads_version_3}diff/?compare={uploads_version_2.pk}", format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["version"]["old"]["pk"] == str(uploads_version_2.pk)
        assert response.data["version"]["new"]["pk"] == str(uploads_version_3.pk)
        assert len(response.data["metadata"]) == 4
        assert "version_file" not in response.data
        assert response.data["metadata"][0]["old"] is None
        assert response.data["metadata"][0]["new"]["pk"] == str(uploads_version_3_metadata_1.pk)
        assert response.data["metadata"][0]["new"]["value"] == uploads_version_3_metadata_1.get_value()
        assert response.data["metadata"][1]["old"] is None
        assert response.data["metadata"][1]["new"]["pk"] == str(uploads_version_3_metadata_2.pk)
        assert response.data["metadata"][1]["new"]["value"] == uploads_version_3_metadata_2.get_value()
        assert response.data["metadata"][2]["old"]["pk"] == str(uploads_version_2_metadata_1.pk)
        assert response.data["metadata"][2]["old"]["value"] == uploads_version_2_metadata_1.get_value()
        assert response.data["metadata"][2]["new"] is None
        assert response.data["metadata"][3]["old"]["pk"] == str(uploads_version_2_metadata_2.pk)
        assert response.data["metadata"][3]["old"]["value"] == uploads_version_2_metadata_2.get_value()
        assert response.data["metadata"][3]["new"] is None

        response = client.get(f"{url_uploads_version_1}diff/?compare={uploads_version_3.pk}", format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["version"]["old"]["pk"] == str(uploads_version_1.pk)
        assert response.data["version"]["new"]["pk"] == str(uploads_version_3.pk)
        assert len(response.data["metadata"]) == 4
        assert "version_file" not in response.data
        assert response.data["metadata"][0]["old"] is None
        assert response.data["metadata"][0]["new"]["pk"] == str(uploads_version_3_metadata_1.pk)
        assert response.data["metadata"][0]["new"]["value"] == uploads_version_3_metadata_1.get_value()
        assert response.data["metadata"][1]["old"] is None
        assert response.data["metadata"][1]["new"]["pk"] == str(uploads_version_3_metadata_2.pk)
        assert response.data["metadata"][1]["new"]["value"] == uploads_version_3_metadata_2.get_value()
        assert response.data["metadata"][2]["old"]["pk"] == str(uploads_version_1_metadata_1.pk)
        assert response.data["metadata"][2]["old"]["value"] == uploads_version_1_metadata_1.get_value()
        assert response.data["metadata"][2]["new"] is None
        assert response.data["metadata"][3]["old"]["pk"] == str(uploads_version_1_metadata_2.pk)
        assert response.data["metadata"][3]["old"]["value"] == uploads_version_1_metadata_2.get_value()
        assert response.data["metadata"][3]["new"] is None

        response = client.post(
            version_action_url,
            {
                "metadata": [
                    {
                        "custom_key": "custom_key_3",
                        "value": "Changed value",
                        "previous_version_field": uploads_version_3_metadata_1.pk,
                        "order": 0,
                    },
                    {
                        "custom_key": "custom_key_5",
                        "field_type": MetadataFieldType.INTEGER,
                        "previous_version_field": uploads_version_3_metadata_2.pk,
                        "order": 1,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        uploads_version_4 = UploadsVersion.objects.get(pk=response.data["pk"])

        uploads_version_4_metadata_1 = Metadata.objects.get(
            assigned_to_content_type=uploads_version_4.get_content_type(),
            assigned_to_object_id=uploads_version_4.pk,
            custom_key="custom_key_3",
        )

        uploads_version_4_metadata_2 = Metadata.objects.get(
            assigned_to_content_type=uploads_version_4.get_content_type(),
            assigned_to_object_id=uploads_version_4.pk,
            custom_key="custom_key_5",
        )

        url_uploads_version_4 = reverse(
            "uploads-version-detail",
            kwargs={
                "pk": uploads_version_4.pk,
            },
        )

        response = client.get(url_uploads_version_4, format="json")
        assert response.status_code == status.HTTP_200_OK

        response = client.get(f"{url_uploads_version_4}diff/?compare={uploads_version_3.pk}", format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["version"]["old"]["pk"] == str(uploads_version_3.pk)
        assert response.data["version"]["new"]["pk"] == str(uploads_version_4.pk)
        assert len(response.data["metadata"]) == 2
        assert "version_file" not in response.data
        assert response.data["metadata"][0]["old"]["pk"] == str(uploads_version_3_metadata_1.pk)
        assert response.data["metadata"][0]["old"]["value"] == uploads_version_3_metadata_1.get_value()
        assert response.data["metadata"][0]["old"]["custom_key"] == uploads_version_3_metadata_1.custom_key
        assert response.data["metadata"][0]["old"]["field_type"] == uploads_version_3_metadata_1.field_type
        assert response.data["metadata"][0]["old"]["order"] == uploads_version_3_metadata_1.order
        assert response.data["metadata"][0]["new"]["pk"] == str(uploads_version_4_metadata_1.pk)
        assert response.data["metadata"][0]["new"]["value"] == uploads_version_4_metadata_1.get_value()
        assert response.data["metadata"][0]["new"]["custom_key"] == uploads_version_4_metadata_1.custom_key
        assert response.data["metadata"][0]["new"]["field_type"] == uploads_version_4_metadata_1.field_type
        assert response.data["metadata"][0]["new"]["order"] == uploads_version_4_metadata_1.order
        assert response.data["metadata"][0]["old"]["value"] != response.data["metadata"][0]["new"]["value"]
        assert response.data["metadata"][1]["old"]["pk"] == str(uploads_version_3_metadata_2.pk)
        assert response.data["metadata"][1]["old"]["value"] == uploads_version_3_metadata_2.get_value()
        assert response.data["metadata"][1]["old"]["custom_key"] == uploads_version_3_metadata_2.custom_key
        assert response.data["metadata"][1]["old"]["field_type"] == uploads_version_3_metadata_2.field_type
        assert response.data["metadata"][1]["old"]["order"] == uploads_version_3_metadata_2.order
        assert response.data["metadata"][1]["new"]["pk"] == str(uploads_version_4_metadata_2.pk)
        assert response.data["metadata"][1]["new"]["value"] == uploads_version_4_metadata_2.get_value()
        assert response.data["metadata"][1]["new"]["custom_key"] == uploads_version_4_metadata_2.custom_key
        assert response.data["metadata"][1]["new"]["field_type"] == uploads_version_4_metadata_2.field_type
        assert response.data["metadata"][1]["old"]["custom_key"] != response.data["metadata"][1]["new"]["custom_key"]
        assert response.data["metadata"][1]["new"]["order"] == uploads_version_4_metadata_2.order

        response = client.post(
            version_action_url,
            {
                "metadata": [
                    {
                        "custom_key": "custom_key_3",
                        "value": "Changed value",
                        "previous_version_field": uploads_version_4_metadata_1.pk,
                        "order": 2,
                    },
                    {
                        "custom_key": "custom_key_5",
                        "field_type": MetadataFieldType.INTEGER,
                        "previous_version_field": uploads_version_4_metadata_2.pk,
                        "order": 1,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        uploads_version_5 = UploadsVersion.objects.get(pk=response.data["pk"])

        uploads_version_5_metadata_1 = Metadata.objects.get(
            assigned_to_content_type=uploads_version_5.get_content_type(),
            assigned_to_object_id=uploads_version_5.pk,
            custom_key="custom_key_3",
        )

        Metadata.objects.get(
            assigned_to_content_type=uploads_version_5.get_content_type(),
            assigned_to_object_id=uploads_version_5.pk,
            custom_key="custom_key_5",
        )

        url_uploads_version_5 = reverse(
            "uploads-version-detail",
            kwargs={
                "pk": uploads_version_5.pk,
            },
        )

        response = client.get(url_uploads_version_5, format="json")
        assert response.status_code == status.HTTP_200_OK

        response = client.get(f"{url_uploads_version_5}diff/?compare={uploads_version_4.pk}", format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["version"]["old"]["pk"] == str(uploads_version_4.pk)
        assert response.data["version"]["new"]["pk"] == str(uploads_version_5.pk)
        assert len(response.data["metadata"]) == 1
        assert "version_file" not in response.data
        assert response.data["metadata"][0]["old"]["pk"] == str(uploads_version_4_metadata_1.pk)
        assert response.data["metadata"][0]["old"]["value"] == uploads_version_4_metadata_1.get_value()
        assert response.data["metadata"][0]["old"]["custom_key"] == uploads_version_4_metadata_1.custom_key
        assert response.data["metadata"][0]["old"]["field_type"] == uploads_version_4_metadata_1.field_type
        assert response.data["metadata"][0]["old"]["order"] == uploads_version_4_metadata_1.order
        assert response.data["metadata"][0]["new"]["pk"] == str(uploads_version_5_metadata_1.pk)
        assert response.data["metadata"][0]["new"]["value"] == uploads_version_5_metadata_1.get_value()
        assert response.data["metadata"][0]["new"]["custom_key"] == uploads_version_5_metadata_1.custom_key
        assert response.data["metadata"][0]["new"]["field_type"] == uploads_version_5_metadata_1.field_type
        assert response.data["metadata"][0]["old"]["order"] != response.data["metadata"][0]["new"]["order"]
        assert response.data["metadata"][0]["new"]["order"] == uploads_version_5_metadata_1.order

        response = client.post(
            file_action_url,
            {
                "file": sample_file,
            },
            format="multipart",
        )
        assert response.status_code == status.HTTP_201_CREATED

        uploads_dataset_1.refresh_from_db()
        uploads_version_6 = uploads_dataset_1.latest_version

        assert uploads_version_1.version_file == uploads_version_2.version_file
        assert uploads_version_2.version_file == uploads_version_3.version_file
        assert uploads_version_3.version_file == uploads_version_4.version_file
        assert uploads_version_4.version_file == uploads_version_5.version_file
        assert uploads_version_5.version_file != uploads_version_6.version_file

        url_uploads_version_6 = reverse(
            "uploads-version-detail",
            kwargs={
                "pk": uploads_version_6.pk,
            },
        )

        response = client.get(url_uploads_version_6, format="json")
        assert response.status_code == status.HTTP_200_OK

        response = client.get(f"{url_uploads_version_6}diff/?compare={uploads_version_5.pk}", format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["version"]["old"]["pk"] == str(uploads_version_5.pk)
        assert response.data["version"]["new"]["pk"] == str(uploads_version_6.pk)
        assert "metadata" not in response.data
        assert response.data["version_file"] is not None
        assert response.data["version_file"]["old"]["pk"] == str(uploads_version_5.version_file.pk)
        assert response.data["version_file"]["new"]["pk"] == str(uploads_version_6.version_file.pk)
        assert response.data["version_file"]["old"]["pk"] != response.data["version_file"]["new"]["pk"]

    def test_uploads_version_diff_ignores_null_order_changes(self, client):
        """
        Ensure diff does not report changes when only the default order changes from None.
        """
        url = reverse("uploads-dataset-list")

        response = client.post(
            url,
            {
                "name": "Test dataset",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        dataset = response.data

        url_dataset = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": dataset["pk"],
            },
        )

        file_action_url = f"{url_dataset}file/"
        version_action_url = f"{url_dataset}version/"

        response = client.post(
            file_action_url,
            {
                "file": sample_file,
            },
            format="multipart",
        )
        assert response.status_code == status.HTTP_201_CREATED

        response = client.post(
            version_action_url,
            {
                "metadata": [
                    {
                        "custom_key": "custom_key_1",
                        "value": "custom value",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        uploads_version_1 = UploadsVersion.objects.get(pk=response.data["pk"])

        uploads_version_1_metadata_1 = Metadata.objects.get(
            assigned_to_content_type=uploads_version_1.get_content_type(),
            assigned_to_object_id=uploads_version_1.pk,
            custom_key="custom_key_1",
        )

        response = client.post(
            version_action_url,
            {
                "metadata": [
                    {
                        "custom_key": "custom_key_1",
                        "value": "custom value",
                        "previous_version_field": uploads_version_1_metadata_1.pk,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        uploads_version_2 = UploadsVersion.objects.get(pk=response.data["pk"])

        url_uploads_version_2 = reverse(
            "uploads-version-detail",
            kwargs={
                "pk": uploads_version_2.pk,
            },
        )

        response = client.get(f"{url_uploads_version_2}diff/?compare={uploads_version_1.pk}", format="json")
        assert response.status_code == status.HTTP_200_OK
        assert "metadata" not in response.data

    def test_uploads_version_file_diff(self, client, initial_users):
        """
        Ensure we have a correct diff between two uploads version files.
        """
        url = reverse("uploads-dataset-list")

        response = client.post(
            url,
            {
                "name": "Test dataset",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        dataset = response.data

        url_dataset = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": dataset["pk"],
            },
        )

        response = client.get(url_dataset, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Test dataset"

        # We can't use reverse for the action as this would collide with other routers
        file_action_url = f"{url_dataset}file/"

        response = client.post(
            file_action_url,
            {
                "file": sample_file,
            },
            format="multipart",
        )
        assert response.status_code == status.HTTP_201_CREATED

        uploads_version_1 = response.data

        uploads_version_file_1 = UploadsVersionFile.objects.get(pk=uploads_version_1["version_file"]["pk"])

        set_metadata(
            assigned_to_content_type=uploads_version_file_1.get_content_type(),
            assigned_to_object_id=uploads_version_file_1.pk,
            custom_key="FILE_NAME",
            value=uploads_version_file_1.name,
        )

        set_metadata(
            assigned_to_content_type=uploads_version_file_1.get_content_type(),
            assigned_to_object_id=uploads_version_file_1.pk,
            custom_key="FILE_SIZE",
            value=uploads_version_file_1.file_size,
        )

        set_metadata(
            assigned_to_content_type=uploads_version_file_1.get_content_type(),
            assigned_to_object_id=uploads_version_file_1.pk,
            custom_key="RANDOM_EXIF_1",
            value="1",
        )

        url_uploads_version_1 = reverse(
            "uploads-version-detail",
            kwargs={
                "pk": uploads_version_1["pk"],
            },
        )

        response = client.post(
            file_action_url,
            {
                "file": sample_file,
            },
            format="multipart",
        )
        assert response.status_code == status.HTTP_201_CREATED

        uploads_version_2 = response.data

        uploads_version_file_2 = UploadsVersionFile.objects.get(pk=uploads_version_2["version_file"]["pk"])

        set_metadata(
            assigned_to_content_type=uploads_version_file_2.get_content_type(),
            assigned_to_object_id=uploads_version_file_2.pk,
            custom_key="FILE_NAME",
            value=uploads_version_file_2.name,
        )

        set_metadata(
            assigned_to_content_type=uploads_version_file_2.get_content_type(),
            assigned_to_object_id=uploads_version_file_2.pk,
            custom_key="FILE_SIZE",
            value=uploads_version_file_2.file_size,
        )

        set_metadata(
            assigned_to_content_type=uploads_version_file_2.get_content_type(),
            assigned_to_object_id=uploads_version_file_2.pk,
            custom_key="RANDOM_EXIF_2",
            value="2",
        )

        response = client.get(f"{url_uploads_version_1}diff/?compare={uploads_version_2['pk']}", format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["version"]["old"]["pk"] == uploads_version_1["pk"]
        assert response.data["version"]["new"]["pk"] == uploads_version_2["pk"]
        assert "version_file" in response.data
        assert response.data["version_file"]["old"]["pk"] == str(uploads_version_file_1.pk)
        assert response.data["version_file"]["old"]["name"] == uploads_version_file_1.name
        assert response.data["version_file"]["new"]["pk"] == str(uploads_version_file_2.pk)
        assert response.data["version_file"]["new"]["name"] == uploads_version_file_2.name
        assert "version_file_metadata" in response.data
        assert len(response.data["version_file_metadata"]) == 3
        assert response.data["version_file_metadata"][0]["old"] is None
        assert response.data["version_file_metadata"][0]["new"]["custom_key"] == "RANDOM_EXIF_2"
        assert response.data["version_file_metadata"][0]["new"]["value"] == "2"
        assert response.data["version_file_metadata"][1]["old"]["custom_key"] == "FILE_NAME"
        assert response.data["version_file_metadata"][1]["old"]["value"] == uploads_version_file_1.name
        assert response.data["version_file_metadata"][1]["new"]["custom_key"] == "FILE_NAME"
        assert response.data["version_file_metadata"][1]["new"]["value"] == uploads_version_file_2.name
        assert response.data["version_file_metadata"][2]["old"]["custom_key"] == "RANDOM_EXIF_1"
        assert response.data["version_file_metadata"][2]["old"]["value"] == "1"
        assert response.data["version_file_metadata"][2]["new"] is None

        response = client.post(
            file_action_url,
            {
                "file": sample_file,
            },
            format="multipart",
        )
        assert response.status_code == status.HTTP_201_CREATED

        uploads_version_3 = response.data

        uploads_version_file_3 = UploadsVersionFile.objects.get(pk=uploads_version_3["version_file"]["pk"])

        set_metadata(
            assigned_to_content_type=uploads_version_file_3.get_content_type(),
            assigned_to_object_id=uploads_version_file_3.pk,
            custom_key="FILE_NAME",
            value=uploads_version_file_3.name,
        )

        set_metadata(
            assigned_to_content_type=uploads_version_file_3.get_content_type(),
            assigned_to_object_id=uploads_version_file_3.pk,
            custom_key="FILE_SIZE",
            value=uploads_version_file_3.file_size,
        )

        set_metadata(
            assigned_to_content_type=uploads_version_file_3.get_content_type(),
            assigned_to_object_id=uploads_version_file_3.pk,
            custom_key="RANDOM_EXIF_2",
            value="2",
        )

        set_metadata(
            assigned_to_content_type=uploads_version_file_3.get_content_type(),
            assigned_to_object_id=uploads_version_file_3.pk,
            custom_key="RANDOM_EXIF_2",
            value="New metadata with same key but different value",
        )

        url_uploads_version_2 = reverse(
            "uploads-version-detail",
            kwargs={
                "pk": uploads_version_2["pk"],
            },
        )

        response = client.get(f"{url_uploads_version_2}diff/?compare={uploads_version_3['pk']}", format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["version"]["old"]["pk"] == uploads_version_2["pk"]
        assert response.data["version"]["new"]["pk"] == uploads_version_3["pk"]
        assert "version_file" in response.data
        assert response.data["version_file"]["old"]["pk"] == str(uploads_version_file_2.pk)
        assert response.data["version_file"]["old"]["name"] == uploads_version_file_2.name
        assert response.data["version_file"]["new"]["pk"] == str(uploads_version_file_3.pk)
        assert response.data["version_file"]["new"]["name"] == uploads_version_file_3.name
        assert "version_file_metadata" in response.data
        assert len(response.data["version_file_metadata"]) == 2
        assert response.data["version_file_metadata"][0]["old"] is None
        assert response.data["version_file_metadata"][0]["new"]["custom_key"] == "RANDOM_EXIF_2"
        assert (
            response.data["version_file_metadata"][0]["new"]["value"]
            == "New metadata with same key but different value"
        )
        assert response.data["version_file_metadata"][1]["old"]["custom_key"] == "FILE_NAME"
        assert response.data["version_file_metadata"][1]["old"]["value"] == uploads_version_file_2.name
        assert response.data["version_file_metadata"][1]["new"]["custom_key"] == "FILE_NAME"
        assert response.data["version_file_metadata"][1]["new"]["value"] == uploads_version_file_3.name


@pytest.mark.django_db
class TestUploadsVersionLockStatusMixin:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.project_1 = Project.objects.create(
            name="Project 1",
        )

        self.dataset_1 = UploadsDataset.objects.create(
            name="Dataset 1",
            folder=self.project_1.folders.first(),
        )

        self.uploads_version_1 = UploadsVersion.objects.create(
            dataset=self.dataset_1,
        )

    def test_lock(self, client, initial_users):
        """
        Ensure we have an expected lock behavior.
        """
        set_request_for_user(initial_users["user_1"])

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": self.dataset_1.pk,
            },
        )

        lock_url = f"{url}lock/"
        unlock_url = f"{url}unlock/"
        status_url = f"{url}status/"

        uploads_version_url = reverse(
            "uploads-version-detail",
            kwargs={
                "pk": self.uploads_version_1.pk,
            },
        )

        uploads_version_status_url = f"{url}status/"

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None

        response = client.get(uploads_version_status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None

        response = client.patch(
            url,
            {
                "name": "Dataset 1",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Dataset 1"

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None

        response = client.get(uploads_version_status_url, format="json")
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

        response = client.get(uploads_version_status_url, format="json")
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

        response = client.get(uploads_version_status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is True
        assert response.data["locked_by"]["pk"] == initial_users["user_1"].pk
        assert response.data["locked_at"] is not None
        assert response.data["locked_at"] > last_lock_time

        response = client.patch(
            url,
            {
                "name": "Dataset 1 - Edit #1",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Dataset 1 - Edit #1"

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None

        response = client.get(uploads_version_status_url, format="json")
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

        response = client.get(uploads_version_status_url, format="json")
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

        response = client.get(uploads_version_status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None

        auth_user(client, initial_users["user_2"])

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND

        response = client.get(uploads_version_url, format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND

        assert self.project_1.members_count == 1
        assert self.project_1.folders.first().members_count == 1

        ProjectMembership.objects.create(
            project=self.project_1,
            member=initial_users["user_2"],
            is_project_admin=True,
        )

        assert self.project_1.members_count == 2
        assert self.project_1.folders.first().members_count == 2

        folder_permission = FolderPermission.objects.get(
            project_membership__member=initial_users["user_2"],
            folder=self.project_1.folders.first(),
        )
        assert folder_permission.is_folder_admin is True
        assert folder_permission.is_metadata_template_admin is True
        assert folder_permission.can_edit is True

        response = client.patch(
            url,
            {
                "name": "Dataset 1 - Edit #3",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Dataset 1 - Edit #3"

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None

        response = client.get(uploads_version_status_url, format="json")
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

        response = client.get(uploads_version_status_url, format="json")
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

        response = client.get(uploads_version_status_url, format="json")
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

        response = client.get(uploads_version_status_url, format="json")
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

        response = client.get(uploads_version_status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None


@pytest.mark.django_db
class TestUploadsVersionFileAPI:
    @pytest.fixture(autouse=True)
    def _setup(self):
        self.uploads_version_file_1 = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        self.uploads_version_1 = UploadsVersion.objects.create(
            version_file=self.uploads_version_file_1,
        )

    def test_read_uploads_version_file_details(self, client):
        """
        Ensure we can read the uploads version file details.
        """
        url = reverse(
            "uploads-version-file-detail",
            kwargs={
                "pk": self.uploads_version_file_1.pk,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == self.uploads_version_file_1.name


@pytest.mark.django_db
class TestUploadsVersionMetadataAPI:
    @pytest.fixture(autouse=True)
    def _setup(self):
        self.uploads_version_1 = UploadsVersion.objects.create()

        self.uploads_version_2 = UploadsVersion.objects.create()

        set_metadata(
            assigned_to_content_type=self.uploads_version_1.get_content_type(),
            assigned_to_object_id=self.uploads_version_1.pk,
            custom_key="custom_key_1",
            value="custom_value_1",
        )

    def test_read_uploads_version_metadata_list(self, client):
        """
        Ensure we can read the uploads version metadata list.
        """
        url = reverse("metadata-list")

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0

        url_filter = (
            f"{url}?assigned_to_content_type={get_content_type_for_object(self.uploads_version_1.get_content_type())}"
            f"&assigned_to_object_id={self.uploads_version_1.pk}"
        )
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        url_filter = (
            f"{url}?assigned_to_content_type={get_content_type_for_object(self.uploads_version_2.get_content_type())}"
            f"&assigned_to_object_id={self.uploads_version_2.pk}"
        )
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0


@pytest.mark.django_db
class TestUploadsVersionFileMetadataAPI:
    @pytest.fixture(autouse=True)
    def _setup(self):
        self.uploads_version_file_1 = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        self.uploads_version_file_2 = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )

        self.uploads_version_file_metadata_1 = set_metadata(
            assigned_to_content_type=self.uploads_version_file_1.get_content_type(),
            assigned_to_object_id=self.uploads_version_file_1.pk,
            custom_key="custom_key_1",
            value="custom_value_1",
        )

    def test_read_uploads_version_file_metadata_list(self, client):
        """
        Ensure we can read the uploads version file metadata list.
        """
        url = reverse("metadata-list")

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0

        url_filter = (
            f"{url}?assigned_to_content_type={get_content_type_for_object(self.uploads_version_file_1.get_content_type())}"
            f"&assigned_to_object_id={self.uploads_version_file_1.pk}"
        )
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        url_filter = (
            f"{url}?assigned_to_content_type={get_content_type_for_object(self.uploads_version_file_2.get_content_type())}"
            f"&assigned_to_object_id={self.uploads_version_file_2.pk}"
        )
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0


@pytest.mark.django_db
class TestListUploadsVersionInFolderAPI:
    @pytest.fixture(autouse=True)
    def _setup(self, client, initial_users):
        set_request_for_user(initial_users["tum_member_user"])
        # Create a project
        project = Project.objects.create(
            name="Project Name",
        )

        # get the auto created "General" folder
        self.general_folder = Folder.objects.get(project=project.pk)

        # Add a regular project member, who will have folder permissions
        project_membership_1 = ProjectMembership.objects.create(
            member=initial_users["regular_user"],
            project=project,
        )
        FolderPermission.objects.create(
            folder=self.general_folder,
            project_membership=project_membership_1,
            is_folder_admin=False,
            can_edit=False,
        )

        # Add another regular project member, who will not have folder permissions
        ProjectMembership.objects.create(
            member=initial_users["regular_user_2"],
            project=project,
        )

        # create 4, 2 and 1 datasets with upload_versions created_by 3 different users and link them with the
        # general_folder, except the first version of the member user which is not linked to a folder and not published
        uploads_dataset_1 = UploadsDataset.objects.create(
            name="Dataset tum_member_user 1",
        )
        UploadsVersion.objects.create(
            dataset=uploads_dataset_1,
        )

        self.general_folder.unlock()
        uploads_dataset_2 = UploadsDataset.objects.create(
            name="Dataset tum_member_user 2",
            folder=self.general_folder,
        )
        UploadsVersion.objects.create(
            dataset=uploads_dataset_2,
            publication_date=timezone.now(),
        )

        uploads_dataset_3 = UploadsDataset.objects.create(
            name="Dataset tum_member_user 3",
            folder=self.general_folder,
        )
        UploadsVersion.objects.create(
            dataset=uploads_dataset_3,
            publication_date=timezone.now(),
        )

        uploads_dataset_4 = UploadsDataset.objects.create(
            name="Dataset tum_member_user 4",
            folder=self.general_folder,
        )
        UploadsVersion.objects.create(
            dataset=uploads_dataset_4,
            publication_date=timezone.now(),
        )

        self.general_folder.unlock()
        set_request_for_user(initial_users["regular_user"])
        uploads_dataset_5 = UploadsDataset.objects.create(
            name="Dataset regular_user 1",
            folder=self.general_folder,
        )
        UploadsVersion.objects.create(
            dataset=uploads_dataset_5,
            publication_date=timezone.now(),
        )

        uploads_dataset_6 = UploadsDataset.objects.create(
            name="Dataset regular_user 2",
            folder=self.general_folder,
        )
        UploadsVersion.objects.create(
            dataset=uploads_dataset_6,
            publication_date=timezone.now(),
        )

        self.general_folder.unlock()
        set_request_for_user(initial_users["regular_user_2"])
        uploads_dataset_7 = UploadsDataset.objects.create(
            name="Dataset regular_user_2 3",
            folder=self.general_folder,
        )
        UploadsVersion.objects.create(
            dataset=uploads_dataset_7,
            publication_date=timezone.now(),
        )

    def test_list_uploads_version_with_valid_folder(self, client, initial_users):
        """
        Ensure users with the appropriate permissions can read versions in a folder
        """
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("uploads-version-list")
        url_filter = f"{url}?dataset__folder={self.general_folder.pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        # there should be 6 published versions in this folder for this user
        assert len(response.data["results"]) == 6
        assert response.data["count"] == 6

        auth_user(client, initial_users["regular_user"])
        url = reverse("uploads-version-list")
        url_filter = f"{url}?dataset__folder={self.general_folder.pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        # there should be 6 published versions in this folder for this user
        assert len(response.data["results"]) == 6
        assert response.data["count"] == 6

    def test_list_uploads_version_with_valid_folder_no_permissions(self, client, initial_users):
        """
        Ensure users without appropriate permissions get no results for a valid folder
        """
        auth_user(client, initial_users["regular_user_2"])
        url = reverse("uploads-version-list")
        url_filter = f"{url}?dataset__folder={self.general_folder.pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        # there should be 0 published versions in this folder for this user
        assert len(response.data["results"]) == 0
        assert response.data["count"] == 0

    def test_list_uploads_version_with_invalid_folder(self, client, initial_users):
        """
        Ensure invalid folders give a invalid_choice error 400
        """
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("uploads-version-list")
        invalid_uuid = uuid.uuid4()
        url_filter = f"{url}?dataset__folder={invalid_uuid}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["dataset__folder"][0].code == "invalid_choice"

    def test_list_uploads_version_with_valid_folder_and_another_filter(self, client, initial_users):
        """
        Ensure users with appropriate permissions can read versions in a folder with another filter on top
        """
        auth_user(client, initial_users["regular_user"])
        url = reverse("uploads-version-list")
        url_filter = f"{url}?dataset__folder={self.general_folder.pk}&created_by={initial_users['tum_member_user'].pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        # there should be 3 published versions in this folder created_by this user
        assert len(response.data["results"]) == 3
        assert response.data["count"] == 3

        auth_user(client, initial_users["regular_user"])
        url = reverse("uploads-version-list")
        url_filter = f"{url}?dataset__folder={self.general_folder.pk}&created_by={initial_users['regular_user'].pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        # there should be 2 published versions in this folder created_by this user
        assert len(response.data["results"]) == 2
        assert response.data["count"] == 2

        auth_user(client, initial_users["regular_user"])
        url = reverse("uploads-version-list")
        url_filter = f"{url}?dataset__folder={self.general_folder.pk}&created_by={initial_users['regular_user_2'].pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        # there should be 1 published versions in this folder created_by this user
        assert len(response.data["results"]) == 1
        assert response.data["count"] == 1


@pytest.mark.django_db
class TestViewDetailUploadsVersionInFolderAPI:
    @pytest.fixture(autouse=True)
    def _setup(self, client, initial_users):
        set_request_for_user(initial_users["tum_member_user"])

        project = Project.objects.create(
            name="Project Name",
        )

        self.general_folder = project.folders.first()

        project_membership_1 = ProjectMembership.objects.create(
            member=initial_users["regular_user"],
            project=project,
        )

        FolderPermission.objects.create(
            folder=self.general_folder,
            project_membership=project_membership_1,
            is_folder_admin=False,
            can_edit=True,
        )

        ProjectMembership.objects.create(
            member=initial_users["regular_user_2"],
            project=project,
        )

        set_request_for_user(initial_users["regular_user"])

        self.uploads_version_draft_1 = UploadsVersion.objects.create()

        self.uploads_version_draft_2 = UploadsVersion.objects.create()

        self.uploads_version_draft_3 = UploadsVersion.objects.create()

        self.uploads_version_in_folder_1 = UploadsVersion.objects.create(
            publication_date=timezone.now(),
        )
        self.uploads_version_in_folder_1.dataset.folder = self.general_folder
        self.uploads_version_in_folder_1.dataset.publication_date = timezone.now()
        self.uploads_version_in_folder_1.dataset.save()

        self.uploads_version_in_folder_2 = UploadsVersion.objects.create(
            publication_date=timezone.now(),
        )
        self.uploads_version_in_folder_2.dataset.folder = self.general_folder
        self.uploads_version_in_folder_2.dataset.publication_date = timezone.now()
        self.uploads_version_in_folder_2.dataset.save()

    def test_list_uploads_dataset_drafts(self, client, initial_users):
        """
        Ensure each user can read the list for datasets uploaded by themselves (draft files).
        """

        auth_user(client, initial_users["regular_user"])
        url = reverse("uploads-dataset-list")
        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 3

        auth_user(client, initial_users["tum_member_user"])
        url = reverse("uploads-dataset-list")
        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0

        auth_user(client, initial_users["regular_user_2"])
        url = reverse("uploads-dataset-list")
        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0

    def test_list_uploads_version_with_valid_folder(self, client, initial_users):
        """
        Ensure users with the appropriate permissions can read versions in a folder
        """
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("uploads-version-list")
        url_filter = f"{url}?dataset__folder={self.general_folder.pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        # there should be 2 published versions in this folder for this user
        assert len(response.data["results"]) == 2
        assert response.data["count"] == 2

        auth_user(client, initial_users["regular_user"])
        url = reverse("uploads-version-list")
        url_filter = f"{url}?dataset__folder={self.general_folder.pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        # there should be 2 published versions in this folder for this user
        assert len(response.data["results"]) == 2
        assert response.data["count"] == 2

        auth_user(client, initial_users["regular_user_2"])
        url = reverse("uploads-version-list")
        url_filter = f"{url}?dataset__folder={self.general_folder.pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        # there should be 0 published versions in this folder for this user
        assert len(response.data["results"]) == 0
        assert response.data["count"] == 0

    def test_view_uploads_version_detail_in_draft(self, client, initial_users):
        """
        Ensure users read versions details of their own drafts
        """
        auth_user(client, initial_users["tum_member_user"])
        url = reverse(
            "uploads-version-detail",
            kwargs={
                "pk": self.uploads_version_draft_1.pk,
            },
        )
        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND

        auth_user(client, initial_users["regular_user"])
        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_published"] is False

        auth_user(client, initial_users["regular_user"])
        url = reverse(
            "uploads-version-detail",
            kwargs={
                "pk": self.uploads_version_draft_2.pk,
            },
        )
        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_published"] is False

    def test_view_uploads_version_detail_in_folder(self, client, initial_users):
        """
        Ensure users with the appropriate permissions can read versions details in a folder
        """
        auth_user(client, initial_users["tum_member_user"])
        url = reverse(
            "uploads-version-detail",
            kwargs={
                "pk": self.uploads_version_in_folder_1.pk,
            },
        )
        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_published"] is True

        auth_user(client, initial_users["regular_user"])
        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_published"] is True

        auth_user(client, initial_users["regular_user_2"])
        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND

        auth_user(client, initial_users["tum_member_user"])
        url = reverse(
            "uploads-version-detail",
            kwargs={
                "pk": self.uploads_version_in_folder_2.pk,
            },
        )
        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_published"] is True

        auth_user(client, initial_users["regular_user"])
        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_published"] is True

        auth_user(client, initial_users["regular_user_2"])
        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND
