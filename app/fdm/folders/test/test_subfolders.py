import pytest
from conftest import auth_user
from django.urls import reverse
from rest_framework import status

from fdm.core.helpers import set_request_for_user
from fdm.folders.models import FolderPermission, Subfolder
from fdm.projects.models import Project, ProjectMembership
from fdm.uploads.models import UploadsDataset


@pytest.mark.django_db
class TestSubfolderAPI:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.project_1 = Project.objects.create(
            name="Project 1",
        )

        self.folder_1 = self.project_1.folder.first()

        self.project_2 = Project.objects.create(
            name="Project 2",
        )

        self.folder_2 = self.project_2.folder.first()

    def _create_subfolder(self, client, name, parent=None, folder=None):
        return client.post(
            reverse("subfolder-list"),
            {
                "folder": str((folder or self.folder_1).pk),
                "parent": str(parent) if parent else None,
                "name": name,
            },
            format="json",
        )

    def _create_published_dataset(self, folder, subfolder=None):
        dataset = UploadsDataset.objects.create(
            name="dataset",
            folder=folder,
            subfolder=subfolder,
        )
        dataset.publish()
        return dataset

    def test_create_nested_subfolders_and_list_levels(self, client):
        """
        Ensure we can create nested subfolders and list each level separately.
        """
        response = self._create_subfolder(client, "10")
        assert response.status_code == status.HTTP_201_CREATED
        subfolder_10 = response.data["pk"]

        response = self._create_subfolder(client, "pdata", parent=subfolder_10)
        assert response.status_code == status.HTTP_201_CREATED
        assert [item["name"] for item in response.data["path"]] == ["10", "pdata"]

        response = self._create_subfolder(client, "11")
        assert response.status_code == status.HTTP_201_CREATED

        response = client.get(
            f"{reverse('subfolder-list')}?folder={self.folder_1.pk}&parent__isnull=true",
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert [item["name"] for item in response.data] == ["10", "11"]
        assert response.data[0]["subfolders_count"] == 1

        response = client.get(f"{reverse('subfolder-list')}?parent={subfolder_10}", format="json")
        assert response.status_code == status.HTTP_200_OK
        assert [item["name"] for item in response.data] == ["pdata"]

    def test_subfolder_names_must_be_unique_per_level_and_valid(self, client):
        """
        Ensure subfolder names are unique within their parent and must not contain slashes.
        """
        response = self._create_subfolder(client, "10")
        assert response.status_code == status.HTTP_201_CREATED
        subfolder_10 = response.data["pk"]

        response = self._create_subfolder(client, "10")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        # The same name is fine on another level
        response = self._create_subfolder(client, "10", parent=subfolder_10)
        assert response.status_code == status.HTTP_201_CREATED

        response = self._create_subfolder(client, "a/b")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        response = self._create_subfolder(client, "  ")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_parent_must_belong_to_the_same_folder(self, client):
        """
        Ensure a subfolder can't be created below a subfolder of another folder.
        """
        other = Subfolder.objects.create(folder=self.folder_2, name="other")

        response = self._create_subfolder(client, "10", parent=other.pk)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_rename_and_move_subfolders(self, client):
        """
        Ensure subfolders can be renamed and moved, but not into themselves or their descendants.
        """
        top = Subfolder.objects.create(folder=self.folder_1, name="top")
        child = Subfolder.objects.create(folder=self.folder_1, parent=top, name="child")
        other = Subfolder.objects.create(folder=self.folder_1, name="other")

        url = reverse("subfolder-detail", kwargs={"pk": top.pk})

        response = client.patch(url, {"name": "renamed"}, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "renamed"

        response = client.patch(url, {"parent": str(child.pk)}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        response = client.patch(url, {"parent": str(top.pk)}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        response = client.patch(url, {"parent": str(other.pk)}, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert [item["name"] for item in response.data["path"]] == ["other", "renamed"]

        response = client.patch(url, {"parent": None}, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["parent"] is None

    def test_delete_only_subfolders_without_files(self, client):
        """
        Ensure only subfolders without any files in their tree can be deleted, including their empty subfolders.
        """
        top = Subfolder.objects.create(folder=self.folder_1, name="top")
        child = Subfolder.objects.create(folder=self.folder_1, parent=top, name="child")
        dataset = self._create_published_dataset(self.folder_1, child)

        url = reverse("subfolder-detail", kwargs={"pk": top.pk})

        response = client.delete(url)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        dataset.subfolder = None
        dataset.save()

        response = client.delete(url)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not Subfolder.objects.filter(pk__in=[top.pk, child.pk]).exists()

    def test_permissions(self, client, initial_users):
        """
        Ensure folder members without edit rights can only read subfolders and other users can't see them at all.
        """
        subfolder = Subfolder.objects.create(folder=self.folder_1, name="10")

        project_membership_user_2 = ProjectMembership.objects.create(
            project=self.project_1,
            member=initial_users["user_2"],
        )

        FolderPermission.objects.create(
            folder=self.folder_1,
            project_membership=project_membership_user_2,
            can_edit=False,
        )

        auth_user(client, initial_users["user_2"])

        response = client.get(f"{reverse('subfolder-list')}?folder={self.folder_1.pk}", format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        response = self._create_subfolder(client, "11")
        assert response.status_code == status.HTTP_403_FORBIDDEN

        response = client.patch(
            reverse("subfolder-detail", kwargs={"pk": subfolder.pk}),
            {"name": "renamed"},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        auth_user(client, initial_users["regular_user"])

        response = client.get(f"{reverse('subfolder-list')}?folder={self.folder_1.pk}", format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0

        response = client.get(reverse("subfolder-detail", kwargs={"pk": subfolder.pk}), format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestDatasetsInSubfolders:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.project_1 = Project.objects.create(
            name="Project 1",
        )

        self.folder_1 = self.project_1.folder.first()

        self.project_2 = Project.objects.create(
            name="Project 2",
        )

        self.folder_2 = self.project_2.folder.first()

    def test_create_dataset_recreates_folder_structure(self, client):
        """
        Ensure uploading files of a folder recreates its directory structure as subfolders and reuses them.
        """
        url = reverse("uploads-dataset-list")

        response = client.post(
            url,
            {"name": "1r", "folder": str(self.folder_1.pk), "subfolder_path": "10 / pdata / 1"},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        response = client.post(
            url,
            {"name": "acqus", "folder": str(self.folder_1.pk), "subfolder_path": "10"},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        subfolder_10 = Subfolder.objects.get(folder=self.folder_1, parent=None, name="10")
        subfolder_1 = Subfolder.objects.get(folder=self.folder_1, parent__parent=subfolder_10, name="1")

        assert Subfolder.objects.filter(folder=self.folder_1).count() == 3
        assert UploadsDataset.objects.get(name="1r").subfolder == subfolder_1
        assert UploadsDataset.objects.get(name="acqus").subfolder == subfolder_10

        # Uploading a folder while browsing a subfolder creates the structure below it
        response = client.post(
            url,
            {
                "name": "fid",
                "folder": str(self.folder_1.pk),
                "subfolder": str(subfolder_10.pk),
                "subfolder_path": "11",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert [subfolder.name for subfolder in UploadsDataset.objects.get(name="fid").subfolder.get_path()] == [
            "10",
            "11",
        ]

    def test_create_dataset_rejects_foreign_subfolders(self, client):
        """
        Ensure datasets can't be created in a subfolder of another folder or as drafts in a subfolder.
        """
        other = Subfolder.objects.create(folder=self.folder_2, name="other")
        url = reverse("uploads-dataset-list")

        response = client.post(
            url,
            {"name": "file", "folder": str(self.folder_1.pk), "subfolder": str(other.pk)},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        response = client.post(url, {"name": "file", "subfolder_path": "10"}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_list_and_move_datasets_between_subfolders(self, client):
        """
        Ensure datasets can be listed per subfolder level and moved between subfolders of their folder.
        """
        subfolder = Subfolder.objects.create(folder=self.folder_1, name="10")
        other = Subfolder.objects.create(folder=self.folder_2, name="other")

        dataset_root = UploadsDataset.objects.create(name="root", folder=self.folder_1)
        dataset_root.publish()
        dataset_nested = UploadsDataset.objects.create(name="nested", folder=self.folder_1, subfolder=subfolder)
        dataset_nested.publish()

        url = reverse("uploads-dataset-list")

        response = client.get(f"{url}?folder={self.folder_1.pk}&subfolder__isnull=true", format="json")
        assert [item["name"] for item in response.data["results"]] == ["root"]

        response = client.get(f"{url}?folder={self.folder_1.pk}&subfolder={subfolder.pk}", format="json")
        assert [item["name"] for item in response.data["results"]] == ["nested"]

        response = client.get(f"{url}?folder={self.folder_1.pk}", format="json")
        assert len(response.data["results"]) == 2

        detail_url = reverse("uploads-dataset-detail", kwargs={"pk": dataset_root.pk})

        response = client.patch(detail_url, {"subfolder": str(subfolder.pk)}, format="json")
        assert response.status_code == status.HTTP_200_OK
        dataset_root.refresh_from_db()
        assert dataset_root.subfolder == subfolder

        response = client.patch(detail_url, {"subfolder": str(other.pk)}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        response = client.patch(detail_url, {"subfolder": None}, format="json")
        assert response.status_code == status.HTTP_200_OK
        dataset_root.refresh_from_db()
        assert dataset_root.subfolder is None

    def test_bulk_publish_drafts_into_subfolder(self, client):
        """
        Ensure draft files can be added to a subfolder of a folder.
        """
        subfolder = Subfolder.objects.create(folder=self.folder_1, name="10")
        other = Subfolder.objects.create(folder=self.folder_2, name="other")
        draft = UploadsDataset.objects.create(name="draft")

        url = reverse("uploads-dataset-bulk-publish")

        response = client.post(
            url,
            {"uploads_datasets": [str(draft.pk)], "folder": str(self.folder_1.pk), "subfolder": str(other.pk)},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        response = client.post(
            url,
            {"uploads_datasets": [str(draft.pk)], "folder": str(self.folder_1.pk), "subfolder": str(subfolder.pk)},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        draft.refresh_from_db()
        assert draft.folder == self.folder_1
        assert draft.subfolder == subfolder


@pytest.mark.django_db
class TestSubfolderMetadata:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.project = Project.objects.create(name="Project")
        self.folder = self.project.folder.first()
        self.subfolder = Subfolder.objects.create(folder=self.folder, name="5")

    @staticmethod
    def _summary(metadata):
        return [(entry["custom_key"], entry["field_type"], entry["value"]) for entry in metadata]

    def test_set_and_replace_subfolder_metadata(self, client):
        """
        Ensure subfolder metadata uses the same typed format as folder and file metadata and can be set, replaced and
        cleared.
        """
        url = reverse("subfolder-metadata", kwargs={"pk": self.subfolder.pk})

        response = client.put(
            url,
            {
                "metadata": [
                    {"custom_key": "Nucleus", "field_type": "TEXT", "value": "1H", "config": {}},
                    {"custom_key": "Scans", "field_type": "INTEGER", "value": "16", "config": {}},
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        assert self._summary(response.data["metadata"]) == [("Nucleus", "TEXT", "1H"), ("Scans", "INTEGER", "16")]

        response = client.put(url, {"metadata": [{"custom_key": "Nucleus", "value": "13C"}]}, format="json")
        assert self._summary(response.data["metadata"]) == [("Nucleus", "TEXT", "13C")]

        response = client.get(reverse("subfolder-detail", kwargs={"pk": self.subfolder.pk}))
        assert self._summary(response.data["metadata"]) == [("Nucleus", "TEXT", "13C")]

        # Values must match their type, and names must be unique
        response = client.put(
            url,
            {"metadata": [{"custom_key": "Scans", "field_type": "INTEGER", "value": "many"}]},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        response = client.put(
            url,
            {"metadata": [{"custom_key": "A", "value": "1"}, {"custom_key": "a", "value": "2"}]},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        # A failed save keeps the previous metadata
        response = client.get(reverse("subfolder-detail", kwargs={"pk": self.subfolder.pk}))
        assert self._summary(response.data["metadata"]) == [("Nucleus", "TEXT", "13C")]

        response = client.put(url, {"metadata": []}, format="json")
        assert response.data["metadata"] == []

    def test_only_editors_can_change_subfolder_metadata(self, client, initial_users):
        membership = ProjectMembership.objects.create(project=self.project, member=initial_users["user_2"])
        FolderPermission.objects.create(folder=self.folder, project_membership=membership, can_edit=False)

        auth_user(client, initial_users["user_2"])

        response = client.put(
            reverse("subfolder-metadata", kwargs={"pk": self.subfolder.pk}),
            {"metadata": [{"custom_key": "Nucleus", "value": "1H"}]},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
