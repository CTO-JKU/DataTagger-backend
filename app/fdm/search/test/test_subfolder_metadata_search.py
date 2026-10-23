import pytest
from conftest import auth_user
from django.urls import reverse
from rest_framework import status

from fdm.core.helpers import set_request_for_user
from fdm.folders.models import Subfolder
from fdm.metadata.helpers import set_metadata
from fdm.projects.models import Project
from fdm.uploads.models import UploadsDataset


@pytest.mark.django_db
class TestSubfolderSearch:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.project = Project.objects.create(name="Project")
        self.folder = self.project.folder.first()
        self.folder.name = "NMR"
        self.folder.save()

        self.experiment = Subfolder.get_or_create_path(folder=self.folder, path="FH-027 / 5")
        self.pdata = Subfolder.get_or_create_path(folder=self.folder, path="pdata / 1", parent=self.experiment)
        set_metadata(
            custom_key="Solvent",
            value="C6D6",
            assigned_to_content_type=self.experiment.get_content_type(),
            assigned_to_object_id=self.experiment.pk,
        )

        for name, subfolder in [("acqus", self.experiment), ("procs", self.pdata), ("readme.txt", None)]:
            UploadsDataset.objects.create(name=name, folder=self.folder, subfolder=subfolder).publish()

    @staticmethod
    def _search(client, payload):
        response = client.post(reverse("search-global"), payload, format="json")
        assert response.status_code == status.HTTP_200_OK, response.data
        return response.data["results"]

    @staticmethod
    def _names(results):
        return {(result["result_type"], result["item"]["name"]) for result in results}

    def test_text_search_finds_only_the_subfolder(self, client):
        """
        Ensure searching for subfolder metadata returns the subfolder itself once, not the files inside it.
        """
        results = self._search(
            client,
            {"search_text": "C6D6", "result_types": ["project", "folder", "subfolder", "dataset"]},
        )

        assert self._names(results) == {("subfolder", "5")}
        item = results[0]["item"]
        assert [entry["name"] for entry in item["path"]] == ["FH-027", "5"]
        assert item["folder"]["name"] == "NMR"
        assert [(entry["custom_key"], entry["value"]) for entry in item["metadata"]] == [("Solvent", "C6D6")]

    def test_subfolders_are_found_by_name(self, client):
        results = self._search(client, {"search_text": "pdata", "result_types": ["subfolder"]})
        assert self._names(results) == {("subfolder", "pdata")}

    def test_metadata_rules_find_the_subfolder(self, client):
        """
        Ensure structured metadata.key / metadata.value rules match subfolders by their own metadata.
        """
        payload = {
            "result_types": ["subfolder", "dataset"],
            "group": {
                "operator": "AND",
                "conditions": [
                    {"field": "metadata.key", "operator": "is", "value": "Solvent"},
                    {"field": "metadata.value", "operator": "contains", "value": "D6"},
                ],
            },
        }
        assert self._names(self._search(client, payload)) == {("subfolder", "5")}

        payload["group"]["conditions"][1]["value"] = "CDCl3"
        assert self._names(self._search(client, payload)) == set()

    def test_compact_results_and_folder_filter(self, client):
        results = self._search(
            client,
            {
                "search_text": "C6D6",
                "result_types": ["subfolder"],
                "response_mode": "compact",
                "group": {
                    "operator": "AND",
                    "conditions": [{"field": "folder_name", "operator": "is", "value": "NMR"}],
                },
            },
        )
        assert results[0]["item"]["folder"]["project"]["name"] == "Project"
        assert [entry["name"] for entry in results[0]["item"]["path"]] == ["FH-027", "5"]

    def test_subfolders_are_only_found_with_access(self, client, initial_users):
        auth_user(client, initial_users["regular_user"])

        assert self._search(client, {"search_text": "C6D6", "result_types": ["subfolder"]}) == []
