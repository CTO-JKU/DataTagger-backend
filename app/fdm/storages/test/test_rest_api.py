import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status

from fdm.core.helpers import get_content_type_for_model, get_content_type_for_object
from fdm.file_parser.models import FileParser
from fdm.folders.models import get_default_folder_storage
from fdm.projects.models import Project
from fdm.storages.models import DynamicStorage
from fdm.storages.models.mappings import DEFAULT_STORAGE_TYPE
from fdm.uploads.models import UploadsVersion, UploadsVersionFile

sample_file = SimpleUploadedFile(
    "file.jpg",
    b"",
    content_type="image/jpg",
)


@pytest.mark.django_db
class TestStoragesAPI:
    @pytest.fixture(autouse=True)
    def _setup(self):
        self.storage_1 = DynamicStorage.objects.get(
            default=True,
        )

        self.storage_2 = DynamicStorage.objects.create(
            name="NAS storage",
            storage_type="private_dss",
        )

    def test_complete_publishing_process(self, client):
        """
        Simulate the entire process of uploading and publishing a file.
        """
        url = reverse("project-list")

        response = client.post(
            url,
            {
                "name": "Test project",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        project = response.data

        url = reverse(
            "project-detail",
            kwargs={
                "pk": project["pk"],
            },
        )
        action_url = f"{url}folders/"

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        folders = response.data

        url = reverse("metadata-template-container-list")

        response = client.post(
            url,
            {
                "name": "Test metadata template",
                "assigned_to_content_type": get_content_type_for_model(Project),
                "assigned_to_object_id": project["pk"],
                "metadata_template_fields": [
                    {
                        "custom_key": "metadata_template_field_1",
                        "mandatory": False,
                    },
                    {
                        "custom_key": "metadata_template_field_2",
                        "mandatory": True,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        metadata_template = response.data["latest_metadata_template"]

        url = reverse("metadata-template-field-list")

        response = client.get(f"{url}?search=metadata_template_field_2", format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        metadata_template_field_2 = response.data["results"][0]

        url = reverse(
            "folder-detail",
            kwargs={
                "pk": folders[0]["pk"],
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["storage"]["pk"] == str(get_default_folder_storage().pk)

        response = client.patch(
            url,
            {
                "storage": self.storage_1.pk,
                "metadata_template": metadata_template["pk"],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["storage"]["pk"] == str(self.storage_1.pk)

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

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": dataset["pk"],
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Test dataset"

        # We can't use reverse for the action as this would collide with other routers
        action_url = f"{url}file/"

        response = client.post(
            action_url,
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

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK

        dataset = response.data

        # We must manually update the 'metadata_is_complete' flag as this usually is a delayed Celery task
        uploads_version = UploadsVersion.objects.get(pk=dataset["latest_version"]["pk"])
        uploads_version.metadata_is_complete = uploads_version.check_metadata_completeness()
        uploads_version.save()

        url = reverse(
            "uploads-version-detail",
            kwargs={
                "pk": dataset["latest_version"]["pk"],
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK

        # At this moment in time it can't be determined if the metadata is complete as the version
        # has no connection to a folder or metadata template.
        assert response.data["metadata_is_complete"] is True

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": dataset["pk"],
            },
        )

        # We can't use reverse for the action as this would collide with other routers
        action_url = f"{url}publish/"

        response = client.post(
            action_url,
            {
                "folder": folders[0]["pk"],
            },
            format="multipart",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["publication_date"] is not None

        dataset = response.data

        # We must manually update the 'metadata_is_complete' flag as this usually is a delayed Celery task
        uploads_version = UploadsVersion.objects.get(pk=dataset["latest_version"]["pk"])
        uploads_version.metadata_is_complete = uploads_version.check_metadata_completeness()
        uploads_version.save()

        url = reverse(
            "uploads-version-detail",
            kwargs={
                "pk": dataset["latest_version"]["pk"],
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK

        # Now that the version has been published, is connected to a folder and also a
        # metadata template, it should not be in a complete state.
        assert response.data["metadata_is_complete"] is False

        url = reverse(
            "uploads-dataset-detail",
            kwargs={
                "pk": dataset["pk"],
            },
        )

        # We can't use reverse for the action as this would collide with other routers
        action_url = f"{url}version/"

        client.post(
            action_url,
            {
                "metadata": [
                    {
                        "custom_key": "metadata_template_field_2",
                        "value": "Test value",
                        "metadata_template_field": metadata_template_field_2["pk"],
                    },
                ],
            },
            format="json",
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["latest_version"]["pk"] != dataset["latest_version"]["pk"]

        dataset = response.data

        # We must manually update the 'metadata_is_complete' flag as this usually is a delayed Celery task
        uploads_version = UploadsVersion.objects.get(pk=dataset["latest_version"]["pk"])
        uploads_version.metadata_is_complete = uploads_version.check_metadata_completeness()
        uploads_version.save()

        url = reverse(
            "uploads-version-detail",
            kwargs={
                "pk": dataset["latest_version"]["pk"],
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_is_complete"] is True
