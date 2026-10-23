import json
import zipfile

import pytest
from conftest import auth_user
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status

from fdm.core.helpers import get_content_type_for_model, set_request_for_user
from fdm.elabftw.models import ElabftwConnection, ExperimentLink
from fdm.exports.models import ExportJob
from fdm.exports.services import build_export_zip_file
from fdm.folders.models import Subfolder
from fdm.metadata.helpers import set_metadata
from fdm.projects.models import Project
from fdm.uploads.models import UploadsDataset, UploadsVersion, UploadsVersionFile

ELABFTW_URL = "https://elab.example.org"


def _create_published_file(folder, name, subfolder=None):
    dataset = UploadsDataset.objects.create(name=name, folder=folder, subfolder=subfolder)
    version_file = UploadsVersionFile.objects.create(
        uploaded_file=SimpleUploadedFile(name, b"content", content_type="application/octet-stream"),
    )
    UploadsVersion.objects.create(dataset=dataset, version_file=version_file)
    dataset.publish()
    return dataset


def _link(target_type, target, experiment_id, title):
    return ExperimentLink.objects.create(
        target_type=target_type,
        target_id=target.pk,
        elabftw_url=ELABFTW_URL,
        experiment_id=experiment_id,
        title=title,
    )


@pytest.mark.django_db
class TestEvaluationExport:
    @pytest.fixture(autouse=True)
    def _setup(self, settings, tmp_path, initial_users, mocker):
        settings.MEDIA_ROOT = tmp_path
        self.tmp_path = tmp_path
        self.user = initial_users["user_1"]
        set_request_for_user(self.user)

        mocker.patch(
            "fdm.elabftw.exports.get_experiment_details",
            side_effect=lambda connection, experiment_id: {"id": experiment_id, "body": "<p>Procedure</p>"},
        )

        self.project = Project.objects.create(name="Project")
        self.folder = self.project.folder.first()
        self.folder.name = "NMR"
        self.folder.save()

        self.experiment_5 = Subfolder.get_or_create_path(folder=self.folder, path="FH-027 / 5")
        self.pdata = Subfolder.get_or_create_path(folder=self.folder, path="pdata / 1", parent=self.experiment_5)
        set_metadata(
            custom_key="Nucleus",
            value="1H",
            assigned_to_content_type=self.experiment_5.get_content_type(),
            assigned_to_object_id=self.experiment_5.pk,
        )

        _create_published_file(self.folder, "acqus", self.experiment_5)
        _create_published_file(self.folder, "procs", self.pdata)
        self.other = _create_published_file(self.folder, "unrelated.txt")

        self.link_2644 = _link("subfolder", self.experiment_5, 2644, "FH-027 Synthesis")
        self.link_13 = _link("dataset", self.other, 13, "GPC")

        ElabftwConnection.objects.create(user=self.user, url=ELABFTW_URL, api_key="key")

    def _build(self, object_ids, metadata_format="json", include_metadata=True):
        export_job = ExportJob.objects.create(
            export_type=get_content_type_for_model(ExperimentLink),
            object_ids=[str(object_id) for object_id in object_ids],
            metadata_format=metadata_format,
            include_uploads_version_file=True,
            include_uploads_version_file_metadata=include_metadata,
            created_by=self.user,
            last_modified_by=self.user,
        )
        return zipfile.ZipFile(self.tmp_path / build_export_zip_file(export_job))

    def test_linked_experiments_overview(self, client, initial_users):
        """
        Ensure the overview lists each linked experiment once, with its places and covered files.
        """
        response = client.get(reverse("experiment-link-experiments"))
        assert response.status_code == status.HTTP_200_OK
        assert [(item["experiment_id"], item["files_count"]) for item in response.data] == [(2644, 2), (13, 1)]
        assert response.data[0]["places"][0]["path"] == "Project / NMR / FH-027 / 5"

        auth_user(client, initial_users["regular_user"])
        response = client.get(reverse("experiment-link-experiments"))
        assert response.data == []

    def test_evaluation_export_with_metadata(self):
        """
        Ensure the evaluation zip has one directory per experiment with the files in their folder structure and a
        metadata file covering the experiment, subfolders and files.
        """
        with self._build([self.link_2644.pk, self.link_13.pk]) as archive:
            names = sorted(archive.namelist())
            metadata = json.loads(archive.read("FH-027 Synthesis (#2644)/metadata.json"))

        assert names == [
            "FH-027 Synthesis (#2644)/NMR/FH-027/5/acqus",
            "FH-027 Synthesis (#2644)/NMR/FH-027/5/pdata/1/procs",
            "FH-027 Synthesis (#2644)/metadata.json",
            "GPC (#13)/NMR/unrelated.txt",
            "GPC (#13)/metadata.json",
        ]
        assert metadata["experiment"]["id"] == 2644
        assert metadata["experiment"]["body"] == "<p>Procedure</p>"
        assert [file["path"] for file in metadata["files"]] == ["NMR/FH-027/5/acqus", "NMR/FH-027/5/pdata/1/procs"]
        subfolders = {item["path"]: item["metadata"] for item in metadata["subfolders"]}
        assert subfolders["NMR/FH-027/5"] == [{"key": "Nucleus", "value": "1H", "field_type": "TEXT"}]

    def test_evaluation_export_csv_and_files_only(self):
        with self._build([self.link_2644.pk], metadata_format="csv") as archive:
            names = archive.namelist()
            rows = archive.read("FH-027 Synthesis (#2644)/metadata.csv").decode().splitlines()

        assert "FH-027 Synthesis (#2644)/metadata.csv" in names
        assert rows[0] == "level,path,key,value"
        assert "subfolder,NMR/FH-027/5,Nucleus,1H" in rows

        with self._build([self.link_2644.pk], include_metadata=False) as archive:
            assert not [name for name in archive.namelist() if "/metadata." in name]

    def test_evaluation_export_requires_access(self, initial_users):
        export_job = ExportJob.objects.create(
            export_type=get_content_type_for_model(ExperimentLink),
            object_ids=[str(self.link_2644.pk)],
            created_by=initial_users["regular_user"],
            last_modified_by=initial_users["regular_user"],
        )

        with pytest.raises(PermissionDenied):
            build_export_zip_file(export_job)
