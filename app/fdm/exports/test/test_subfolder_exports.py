import zipfile

import pytest
from conftest import auth_user
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status

from fdm.core.helpers import get_content_type_for_model, set_request_for_user
from fdm.exports.models import ExportJob
from fdm.exports.services import build_export_zip_file
from fdm.folders.models import Folder, Subfolder
from fdm.projects.models import Project
from fdm.uploads.models import UploadsDataset, UploadsVersion, UploadsVersionFile


def _create_published_file(folder, name, subfolder=None):
    dataset = UploadsDataset.objects.create(
        name=name,
        folder=folder,
        subfolder=subfolder,
    )
    version_file = UploadsVersionFile.objects.create(
        uploaded_file=SimpleUploadedFile(name, b"content", content_type="application/octet-stream"),
    )
    UploadsVersion.objects.create(
        dataset=dataset,
        version_file=version_file,
    )
    dataset.publish()
    return dataset


def _build_export(user, model, objects, include_metadata):
    export_job = ExportJob.objects.create(
        export_type=get_content_type_for_model(model),
        object_ids=[str(instance.pk) for instance in objects],
        include_uploads_version_file=True,
        include_uploads_version_file_metadata=include_metadata,
        created_by=user,
        last_modified_by=user,
    )
    return build_export_zip_file(export_job)


@pytest.mark.django_db
class TestSubfolderExports:
    @pytest.fixture(autouse=True)
    def _setup(self, settings, tmp_path, initial_users):
        settings.MEDIA_ROOT = tmp_path
        self.tmp_path = tmp_path
        self.user = initial_users["user_1"]
        set_request_for_user(self.user)

        self.project = Project.objects.create(name="Project")
        self.folder = Folder.objects.filter(project=self.project).first()
        self.folder.name = "NMR"
        self.folder.save()

        self.experiment = Subfolder.get_or_create_path(folder=self.folder, path="FH-027 / 5")
        self.pdata = Subfolder.get_or_create_path(folder=self.folder, path="pdata / 1", parent=self.experiment)

        _create_published_file(self.folder, "readme.txt")
        _create_published_file(self.folder, "acqus", self.experiment)
        _create_published_file(self.folder, "fid", self.experiment)
        _create_published_file(self.folder, "procs", self.pdata)

    def _names(self, zip_file_name):
        with zipfile.ZipFile(self.tmp_path / zip_file_name) as archive:
            return sorted(archive.namelist())

    def test_files_only_folder_export_mirrors_the_subfolder_structure(self):
        """
        Ensure a files-only folder export recreates the original directory structure.
        """
        names = self._names(_build_export(self.user, Folder, [self.folder], include_metadata=False))

        assert names == [
            "NMR/FH-027/5/acqus",
            "NMR/FH-027/5/fid",
            "NMR/FH-027/5/pdata/1/procs",
            "NMR/readme.txt",
        ]

    def test_folder_export_with_metadata_keeps_a_directory_per_file(self):
        """
        Ensure exports with metadata keep each file next to its metadata sidecars, now below its subfolders.
        """
        names = self._names(_build_export(self.user, Folder, [self.folder], include_metadata=True))

        assert "NMR/FH-027/5/pdata/1/procs/procs" in names
        assert "NMR/FH-027/5/pdata/1/procs/file-metadata.json" in names
        assert "NMR/readme.txt/readme.txt" in names

    def test_subfolder_export_contains_only_its_tree(self):
        """
        Ensure a subfolder can be exported on its own, starting at the subfolder.
        """
        names = self._names(_build_export(self.user, Subfolder, [self.experiment], include_metadata=False))

        assert names == [
            "5/acqus",
            "5/fid",
            "5/pdata/1/procs",
        ]

    def test_project_export_includes_subfolders(self):
        """
        Ensure project exports place files below their folder and subfolders.
        """
        names = self._names(_build_export(self.user, Project, [self.project], include_metadata=False))

        assert "Project/NMR/FH-027/5/pdata/1/procs" in names

    def test_subfolder_export_job_via_api(self, client, initial_users, mocker):
        """
        Ensure subfolder exports can be requested via the API and are restricted to folder members.
        """
        mocker.patch("fdm.exports.rest.views.build_export_zip.apply_async")
        mocker.patch("fdm.exports.rest.views.transaction.on_commit", side_effect=lambda callback: callback())
        payload = {
            "export_type": get_content_type_for_model(Subfolder),
            "uuids": [str(self.experiment.pk)],
            "include_uploads_version_file": True,
            "include_uploads_version_file_metadata": False,
        }

        response = client.post(reverse("export-list"), payload, format="json")
        assert response.status_code == status.HTTP_202_ACCEPTED

        auth_user(client, initial_users["regular_user"])
        response = client.post(reverse("export-list"), payload, format="json")
        assert response.status_code in [status.HTTP_400_BAD_REQUEST, status.HTTP_403_FORBIDDEN]
