import pytest
from conftest import auth_user
from django.urls import reverse
from rest_framework import status

from fdm.core.helpers import set_request_for_user
from fdm.folders.models import Folder, FolderPermission
from fdm.metadata.enums import MetadataFieldType
from fdm.metadata.helpers import set_metadata
from fdm.metadata.models import MetadataField, MetadataTag, MetadataTemplateField
from fdm.projects.models import Project, ProjectMembership
from fdm.uploads.models import UploadsDataset, UploadsVersion, UploadsVersionFile


@pytest.mark.django_db
class TestMetadataTagSignal:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.project = Project.objects.create(name="Tag Test Project")
        self.folder = self.project.folder.first()

        self.tag_field = MetadataField.objects.create(
            key="test_tag_field",
            field_type=MetadataFieldType.TAG,
        )

    def test_creates_tags_for_tag_type_on_folder(self, initial_users):
        set_metadata(
            field=self.tag_field,
            value=["alpha", "beta"],
            config={"options": ["alpha", "beta"]},
            assigned_to_content_type=self.folder.get_content_type(),
            assigned_to_object_id=self.folder.pk,
        )

        tags = MetadataTag.objects.filter(folder=self.folder)
        assert tags.count() == 2
        assert set(tags.values_list("value", flat=True)) == {"alpha", "beta"}

    def test_creates_tags_via_uploads_version(self, initial_users):
        dataset = UploadsDataset.objects.create(
            name="Test Dataset",
            folder=self.folder,
        )
        version = UploadsVersion.objects.create(dataset=dataset)

        set_metadata(
            field=self.tag_field,
            value=["gamma"],
            config={"options": ["gamma"]},
            assigned_to_content_type=version.get_content_type(),
            assigned_to_object_id=version.pk,
        )

        tags = MetadataTag.objects.filter(folder=self.folder)
        assert tags.count() == 1
        assert tags.first().value == "gamma"

    def test_creates_tags_via_uploads_version_file(self, initial_users):
        dataset = UploadsDataset.objects.create(
            name="Test Dataset",
            folder=self.folder,
        )
        version_file = UploadsVersionFile.objects.create()
        UploadsVersion.objects.create(dataset=dataset, version_file=version_file)

        set_metadata(
            field=self.tag_field,
            value=["delta"],
            config={"options": ["delta"]},
            assigned_to_content_type=version_file.get_content_type(),
            assigned_to_object_id=version_file.pk,
        )

        tags = MetadataTag.objects.filter(folder=self.folder)
        assert tags.count() == 1
        assert tags.first().value == "delta"

    def test_skips_non_tag_field_types(self, initial_users):
        text_field = MetadataField.objects.create(
            key="test_text_field",
            field_type=MetadataFieldType.TEXT,
        )

        set_metadata(
            field=text_field,
            value="some text",
            assigned_to_content_type=self.folder.get_content_type(),
            assigned_to_object_id=self.folder.pk,
        )

        assert MetadataTag.objects.count() == 0

    def test_skips_project_assigned_metadata(self, initial_users):
        set_metadata(
            field=self.tag_field,
            value=["project_tag"],
            config={"options": ["project_tag"]},
            assigned_to_content_type=self.project.get_content_type(),
            assigned_to_object_id=self.project.pk,
        )

        assert MetadataTag.objects.count() == 0

    def test_deduplicates_same_tag_value(self, initial_users):
        set_metadata(
            field=self.tag_field,
            value=["alpha", "beta"],
            config={"options": ["alpha", "beta"]},
            assigned_to_content_type=self.folder.get_content_type(),
            assigned_to_object_id=self.folder.pk,
        )

        set_metadata(
            field=self.tag_field,
            value=["alpha", "gamma"],
            config={"options": ["alpha", "gamma"]},
            assigned_to_content_type=self.folder.get_content_type(),
            assigned_to_object_id=self.folder.pk,
        )

        tags = MetadataTag.objects.filter(folder=self.folder)
        assert tags.count() == 3
        assert set(tags.values_list("value", flat=True)) == {"alpha", "beta", "gamma"}

    def test_handles_empty_value_list(self, initial_users):
        set_metadata(
            field=self.tag_field,
            value=[],
            config={"options": []},
            assigned_to_content_type=self.folder.get_content_type(),
            assigned_to_object_id=self.folder.pk,
        )

        assert MetadataTag.objects.count() == 0

    def test_handles_none_value(self, initial_users):
        set_metadata(
            field=self.tag_field,
            value=None,
            config={"options": []},
            assigned_to_content_type=self.folder.get_content_type(),
            assigned_to_object_id=self.folder.pk,
        )

        assert MetadataTag.objects.count() == 0

    def test_does_not_trigger_for_metadata_template_field(self, initial_users):
        from fdm.metadata.models import MetadataTemplate, MetadataTemplateContainer

        container = MetadataTemplateContainer.objects.create(name="Test Container")
        template = MetadataTemplate.objects.create(metadata_template_container=container)

        MetadataTemplateField.objects.create(
            metadata_template=template,
            field=self.tag_field,
            field_type=MetadataFieldType.TAG,
            value={"value": ["template_tag"]},
            config={"options": ["template_tag"]},
        )

        assert MetadataTag.objects.count() == 0


@pytest.mark.django_db
class TestMetadataTagAPI:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.project = Project.objects.create(name="Tag API Project")
        self.folder = self.project.folder.first()

        self.tag_field = MetadataField.objects.create(
            key="api_tag_field",
            field_type=MetadataFieldType.TAG,
        )

        set_metadata(
            field=self.tag_field,
            value=["api_tag_1", "api_tag_2"],
            config={"options": ["api_tag_1", "api_tag_2"]},
            assigned_to_content_type=self.folder.get_content_type(),
            assigned_to_object_id=self.folder.pk,
        )

    def test_list_returns_empty_without_folder_filter(self, client):
        url = reverse("metadata-tag-list")

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0

    def test_list_returns_tags_for_folder(self, client):
        url = reverse("metadata-tag-list")
        url_filter = f"{url}?folder={self.folder.pk}"

        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 2
        values = {tag["value"] for tag in response.data["results"]}
        assert values == {"api_tag_1", "api_tag_2"}

    def test_list_returns_empty_for_non_member(self, client, initial_users):
        auth_user(client, initial_users["regular_user"])

        url = reverse("metadata-tag-list")
        url_filter = f"{url}?folder={self.folder.pk}"

        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0

    def test_list_only_returns_tags_for_permitted_folders(self, client, initial_users):
        set_request_for_user(initial_users["user_1"])

        project_2 = Project.objects.create(name="Other Project")
        folder_2 = project_2.folder.first()

        set_metadata(
            field=self.tag_field,
            value=["other_tag"],
            config={"options": ["other_tag"]},
            assigned_to_content_type=folder_2.get_content_type(),
            assigned_to_object_id=folder_2.pk,
        )

        auth_user(client, initial_users["regular_user"])

        project_membership = ProjectMembership.objects.create(
            project=self.project,
            member=initial_users["regular_user"],
        )
        FolderPermission.objects.create(
            folder=self.folder,
            project_membership=project_membership,
        )

        url = reverse("metadata-tag-list")

        url_filter = f"{url}?folder={self.folder.pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 2

        url_filter = f"{url}?folder={folder_2.pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0

    def test_search_filter(self, client):
        url = reverse("metadata-tag-list")
        url_filter = f"{url}?folder={self.folder.pk}&search=tag_1"

        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1
        assert response.data["results"][0]["value"] == "api_tag_1"

    def test_retrieve_tag(self, client):
        tag = MetadataTag.objects.filter(folder=self.folder).first()
        url = reverse("metadata-tag-detail", kwargs={"pk": tag.pk})

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["value"] == tag.value
        assert str(response.data["folder"]) == str(self.folder.pk)
