import pytest
import requests
from conftest import auth_user
from django.urls import reverse
from rest_framework import status

from fdm.core.helpers import set_request_for_user
from fdm.elabftw.models import ElabftwConnection, ExperimentLink, normalize_elabftw_url
from fdm.folders.models import FolderPermission, Subfolder
from fdm.projects.models import Project, ProjectMembership
from fdm.uploads.models import UploadsDataset

ELABFTW_URL = "https://elab.example.org"


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        if self.payload is None:
            raise ValueError("No JSON")
        return self.payload


def fake_elabftw(url, headers=None, params=None, **kwargs):
    if headers.get("Authorization") != "valid-key":
        return FakeResponse(401, {"description": "No corresponding API key found!"})

    if url.endswith("/api/v2/users/me"):
        return FakeResponse(200, {"fullname": "Toni Tester", "email": "toni@example.org"})

    if url.endswith("/api/v2/experiments"):
        experiments = [
            {"id": 12, "title": "NMR of FH-027", "date": "2026-10-20", "category_title": "NMR", "status_title": "Done"},
            {"id": 13, "title": "GPC of MK27", "date": "2026-10-21", "category_title": "GPC", "status_title": None},
        ]
        query = (params or {}).get("q", "")
        return FakeResponse(200, [experiment for experiment in experiments if query in experiment["title"]])

    if url.endswith("/api/v2/experiments/12"):
        return FakeResponse(200, {"id": 12, "title": "NMR of FH-027", "date": "2026-10-20"})

    if url.endswith("/api/v2/experiments/13"):
        return FakeResponse(200, {"id": 13, "title": "GPC of MK27", "date": "2026-10-21"})

    return FakeResponse(404, {"description": "Nothing to show with this id"})


@pytest.mark.django_db
class TestElabftw:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users, mocker):
        self.requests_mock = mocker.patch("fdm.elabftw.services.requests.get", side_effect=fake_elabftw)
        set_request_for_user(initial_users["user_1"])

        self.project = Project.objects.create(name="Project")
        self.folder = self.project.folder.first()
        self.subfolder = Subfolder.objects.create(folder=self.folder, name="FH-027")
        self.dataset = UploadsDataset.objects.create(name="fid", folder=self.folder)

    def _connect(self, client, api_key="valid-key"):
        return client.put(
            reverse("elabftw-connection"),
            {"url": f"{ELABFTW_URL}/experiments.php", "api_key": api_key},
            format="json",
        )

    def _link(self, client, target_type, target_id, experiment_id=12):
        return client.post(
            reverse("experiment-link-list"),
            {"target_type": target_type, "target_id": str(target_id), "experiment_id": experiment_id},
            format="json",
        )

    def test_normalize_elabftw_url(self):
        assert normalize_elabftw_url(" https://elab.example.org/ ") == ELABFTW_URL
        assert normalize_elabftw_url("https://elab.example.org/experiments.php?mode=view&id=3") == ELABFTW_URL
        assert normalize_elabftw_url("https://elab.example.org/api/v2/") == ELABFTW_URL

    def test_connection_never_returns_the_api_key(self, client):
        """
        Ensure the connection can be saved, read and deleted, and the API key never leaves the server.
        """
        response = client.get(reverse("elabftw-connection"))
        assert response.status_code == status.HTTP_404_NOT_FOUND

        response = client.put(reverse("elabftw-connection"), {"url": ELABFTW_URL}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        response = self._connect(client)
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {"url": ELABFTW_URL, "has_api_key": True, "verify_tls": True}

        # Updating without a new key keeps the stored one
        response = client.put(reverse("elabftw-connection"), {"verify_tls": False}, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert ElabftwConnection.objects.get().api_key == "valid-key"

        response = client.put(reverse("elabftw-connection"), {"url": "elab.example.org"}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        response = client.delete(reverse("elabftw-connection"))
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not ElabftwConnection.objects.exists()

    def test_connection_test_and_errors(self, client):
        """
        Ensure the connection test reports the eLabFTW user, and readable errors for wrong keys or addresses.
        """
        self._connect(client)
        response = client.post(reverse("elabftw-connection-test"))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["fullname"] == "Toni Tester"

        self._connect(client, api_key="wrong-key")
        response = client.post(reverse("elabftw-connection-test"))
        assert response.status_code == status.HTTP_502_BAD_GATEWAY
        assert "API key" in response.data["error"]

        self.requests_mock.side_effect = requests.exceptions.ConnectionError()
        response = client.post(reverse("elabftw-connection-test"))
        assert response.status_code == status.HTTP_502_BAD_GATEWAY
        assert "could not be reached" in response.data["error"]

    def test_search_experiments(self, client):
        """
        Ensure experiments can be searched by text and by ID and include a link to eLabFTW.
        """
        response = client.get(reverse("elabftw-experiments"))
        assert response.status_code == status.HTTP_404_NOT_FOUND

        self._connect(client)

        response = client.get(f"{reverse('elabftw-experiments')}?search=NMR")
        assert response.status_code == status.HTTP_200_OK
        assert [experiment["id"] for experiment in response.data] == [12]
        assert response.data[0]["url"] == f"{ELABFTW_URL}/experiments.php?mode=view&id=12"
        assert response.data[0]["category"] == "NMR"

        response = client.get(f"{reverse('elabftw-experiments')}?search=%2312")
        assert [experiment["title"] for experiment in response.data] == ["NMR of FH-027"]

    def test_link_folder_subfolder_and_file(self, client):
        """
        Ensure experiments can be linked to folders, subfolders and files, once each, and unlinked again.
        """
        self._connect(client)

        for target_type, target in [("folder", self.folder), ("subfolder", self.subfolder), ("dataset", self.dataset)]:
            response = self._link(client, target_type, target.pk)
            assert response.status_code == status.HTTP_201_CREATED
            assert response.data["title"] == "NMR of FH-027"
            assert response.data["experiment_url"] == f"{ELABFTW_URL}/experiments.php?mode=view&id=12"

            response = client.get(
                f"{reverse('experiment-link-list')}?target_type={target_type}&target_id={target.pk}",
            )
            assert [link["experiment_id"] for link in response.data] == [12]

        response = self._link(client, "subfolder", self.subfolder.pk)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

        response = self._link(client, "subfolder", self.subfolder.pk, experiment_id=99)
        assert response.status_code == status.HTTP_502_BAD_GATEWAY

        link = ExperimentLink.objects.get(target_type="subfolder")
        response = client.delete(reverse("experiment-link-detail", kwargs={"pk": link.pk}))
        assert response.status_code == status.HTTP_204_NO_CONTENT

    def test_files_and_subfolders_inherit_links(self, client):
        """
        Ensure links on a folder or subfolder also apply to everything inside it, including files added later, and
        that several experiments can be linked.
        """
        self._connect(client)
        nested = Subfolder.objects.create(folder=self.folder, parent=self.subfolder, name="pdata")
        self._link(client, "folder", self.folder.pk, experiment_id=13)
        self._link(client, "subfolder", self.subfolder.pk, experiment_id=12)

        # Added after linking
        new_file = UploadsDataset.objects.create(name="procs", folder=self.folder, subfolder=nested)

        response = client.get(f"{reverse('experiment-link-list')}?target_type=dataset&target_id={new_file.pk}")
        assert [(link["experiment_id"], link["inherited_from"]["name"]) for link in response.data] == [
            (12, "FH-027"),
            (13, self.folder.name),
        ]

        # Direct links are listed first, and an experiment linked twice only once, from the nearest place
        self._link(client, "dataset", new_file.pk, experiment_id=12)
        response = client.get(f"{reverse('experiment-link-list')}?target_type=dataset&target_id={new_file.pk}")
        assert [(link["experiment_id"], link["inherited_from"]) for link in response.data][0] == (12, None)
        assert len(response.data) == 2

        response = client.get(f"{reverse('experiment-link-list')}?target_type=subfolder&target_id={self.subfolder.pk}")
        assert [(link["experiment_id"], bool(link["inherited_from"])) for link in response.data] == [
            (12, False),
            (13, True),
        ]

        # Files outside the subfolder only get the folder's link
        response = client.get(f"{reverse('experiment-link-list')}?target_type=dataset&target_id={self.dataset.pk}")
        assert [link["experiment_id"] for link in response.data] == [13]

    def test_links_are_removed_with_their_target(self, client):
        self._connect(client)
        self._link(client, "subfolder", self.subfolder.pk)

        self.subfolder.delete()

        assert not ExperimentLink.objects.exists()

    def test_permissions(self, client, initial_users):
        """
        Ensure folder members without edit rights can see but not change links, and others see nothing.
        """
        self._connect(client)
        self._link(client, "folder", self.folder.pk)
        link = ExperimentLink.objects.get()

        membership = ProjectMembership.objects.create(project=self.project, member=initial_users["user_2"])
        FolderPermission.objects.create(folder=self.folder, project_membership=membership, can_edit=False)

        auth_user(client, initial_users["user_2"])
        set_request_for_user(initial_users["user_2"])
        ElabftwConnection.objects.create(user=initial_users["user_2"], url=ELABFTW_URL, api_key="valid-key")

        response = client.get(f"{reverse('experiment-link-list')}?target_type=folder&target_id={self.folder.pk}")
        assert len(response.data) == 1

        response = self._link(client, "subfolder", self.subfolder.pk)
        assert response.status_code == status.HTTP_403_FORBIDDEN

        response = client.delete(reverse("experiment-link-detail", kwargs={"pk": link.pk}))
        assert response.status_code == status.HTTP_403_FORBIDDEN

        auth_user(client, initial_users["regular_user"])

        response = client.get(f"{reverse('experiment-link-list')}?target_type=folder&target_id={self.folder.pk}")
        assert response.data == []

        response = client.delete(reverse("experiment-link-detail", kwargs={"pk": link.pk}))
        assert response.status_code == status.HTTP_404_NOT_FOUND
