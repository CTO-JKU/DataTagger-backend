import json
import zipfile

import pytest
from conftest import auth_user
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from fdm.core.helpers import get_content_type_for_model, set_request_for_user
from fdm.dbsettings.functions import set_dbsettings_value
from fdm.exports.models import ExportJob
from fdm.exports.services import build_export_zip_file, cleanup_expired_export_jobs
from fdm.exports.tasks import build_export_zip
from fdm.folders.models import Folder, FolderPermission
from fdm.metadata.helpers import set_metadata
from fdm.projects.models import Project, ProjectMembership
from fdm.uploads.models import UploadsDataset, UploadsVersion, UploadsVersionFile


def _create_uploads_version(user, dataset_name="Dataset", uploaded_name="source.jpg", content=b"content"):
    set_request_for_user(user)
    dataset = UploadsDataset.objects.create(
        name=dataset_name,
    )
    version_file = UploadsVersionFile.objects.create(
        uploaded_file=SimpleUploadedFile(
            uploaded_name,
            content,
            content_type="image/jpeg",
        ),
    )
    version = UploadsVersion.objects.create(
        dataset=dataset,
        version_file=version_file,
    )
    return dataset, version, version_file


def _set_file_name_metadata(version_file, key, value):
    set_metadata(
        custom_key=key,
        value=value,
        assigned_to_content_type=version_file.get_content_type(),
        assigned_to_object_id=version_file.pk,
    )


def _set_uploads_version_metadata(version, key, value):
    set_metadata(
        custom_key=key,
        value=value,
        assigned_to_content_type=version.get_content_type(),
        assigned_to_object_id=version.pk,
    )


def _response_results(response):
    return response.data["results"] if isinstance(response.data, dict) and "results" in response.data else response.data


def _add_zip_file(settings, tmp_path, export_job, name="export.zip"):
    settings.MEDIA_ROOT = tmp_path
    zip_path = tmp_path / "exports" / str(export_job.pk) / name
    zip_path.parent.mkdir(parents=True)
    zip_path.write_bytes(b"zip-content")
    export_job.zip_file.name = f"exports/{export_job.pk}/{name}"
    export_job.save(update_fields=["zip_file"])
    return zip_path


def _create_shared_dataset(user_1, user_2):
    set_request_for_user(user_1)
    project = Project.objects.create(name="Project")
    folder = Folder.objects.create(project=project, name="Folder")
    project_membership = ProjectMembership.objects.create(project=project, member=user_2)
    FolderPermission.objects.get_or_create(folder=folder, project_membership=project_membership)
    dataset, version, version_file = _create_uploads_version(
        user_1,
        dataset_name="Dataset",
        uploaded_name="source.jpg",
    )
    dataset.folder = folder
    dataset.publication_date = timezone.now()
    dataset.save()
    return dataset, version, version_file


@pytest.mark.django_db
def test_create_export_job_accepts_content_type_and_schedules_task(client, initial_users, mocker):
    user = initial_users["user_1"]
    dataset, version, _ = _create_uploads_version(user)
    task_mock = mocker.patch("fdm.exports.rest.views.build_export_zip.apply_async")
    mocker.patch("fdm.exports.rest.views.transaction.on_commit", side_effect=lambda callback: callback())
    url = reverse("export-list")

    response = client.post(
        url,
        {
            "export_type": get_content_type_for_model(UploadsDataset),
            "uuids": [str(dataset.pk)],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_202_ACCEPTED
    assert response.data["export_type"] == "uploads.uploadsdataset"
    assert response.data["metadata_format"] == ExportJob.MetadataFormat.JSON
    assert response.data["include_uploads_version_file"] is True
    assert response.data["include_uploads_version_file_metadata"] is True
    assert response.data["status"] == ExportJob.Status.SCHEDULED
    task_mock.assert_called_once()
    export_job = ExportJob.objects.get(pk=response.data["pk"])
    assert export_job.exported_uploads_version_ids == [str(version.pk)]


@pytest.mark.django_db
def test_create_export_job_reuses_finished_export_with_new_user_owned_row(
    settings,
    tmp_path,
    client,
    initial_users,
    mocker,
):
    user_1 = initial_users["user_1"]
    user_2 = initial_users["user_2"]
    dataset, version, _ = _create_shared_dataset(user_1, user_2)
    old_expires_at = timezone.now() + timezone.timedelta(hours=1)
    source_job = ExportJob.objects.create(
        export_type=get_content_type_for_model(UploadsDataset),
        object_ids=[str(dataset.pk)],
        exported_uploads_version_ids=[str(version.pk)],
        metadata_format=ExportJob.MetadataFormat.JSON,
        include_uploads_version_file=True,
        include_uploads_version_file_metadata=True,
        status=ExportJob.Status.FINISHED,
        expires_at=old_expires_at,
        finished_at=timezone.now(),
        created_by=user_1,
        last_modified_by=user_1,
    )
    _add_zip_file(settings, tmp_path, source_job)
    task_mock = mocker.patch("fdm.exports.rest.views.build_export_zip.apply_async")
    on_commit_mock = mocker.patch(
        "fdm.exports.rest.views.transaction.on_commit", side_effect=lambda callback: callback()
    )
    auth_user(client, user_2)

    response = client.post(
        reverse("export-list"),
        {
            "export_type": get_content_type_for_model(UploadsDataset),
            "uuids": [str(dataset.pk)],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_202_ACCEPTED
    reused_job = ExportJob.objects.get(pk=response.data["pk"])
    source_job.refresh_from_db()
    assert reused_job.pk != source_job.pk
    assert reused_job.created_by == user_2
    assert reused_job.source_export_job == source_job
    assert reused_job.status == ExportJob.Status.FINISHED
    assert reused_job.zip_file.name == source_job.zip_file.name
    assert reused_job.exported_uploads_version_ids == [str(version.pk)]
    assert reused_job.expires_at > old_expires_at
    assert source_job.expires_at == old_expires_at
    assert reused_job.check_download_token("not-the-token") is False
    task_mock.assert_not_called()
    on_commit_mock.assert_not_called()


@pytest.mark.django_db
def test_create_export_job_does_not_reuse_when_settings_or_latest_version_differ(
    settings,
    tmp_path,
    client,
    initial_users,
    mocker,
):
    user = initial_users["user_1"]
    dataset, version, _ = _create_uploads_version(user)
    source_job = ExportJob.objects.create(
        export_type=get_content_type_for_model(UploadsDataset),
        object_ids=[str(dataset.pk)],
        exported_uploads_version_ids=[str(version.pk)],
        metadata_format=ExportJob.MetadataFormat.JSON,
        include_uploads_version_file=True,
        include_uploads_version_file_metadata=True,
        status=ExportJob.Status.FINISHED,
        expires_at=timezone.now() + timezone.timedelta(hours=1),
        finished_at=timezone.now(),
        created_by=user,
        last_modified_by=user,
    )
    _add_zip_file(settings, tmp_path, source_job)
    task_mock = mocker.patch("fdm.exports.rest.views.build_export_zip.apply_async")
    mocker.patch("fdm.exports.rest.views.transaction.on_commit", side_effect=lambda callback: callback())

    response = client.post(
        reverse("export-list"),
        {
            "export_type": get_content_type_for_model(UploadsDataset),
            "uuids": [str(dataset.pk)],
            "metadata_format": ExportJob.MetadataFormat.CSV,
        },
        format="json",
    )

    assert response.status_code == status.HTTP_202_ACCEPTED
    csv_job = ExportJob.objects.get(pk=response.data["pk"])
    assert csv_job.source_export_job is None
    assert csv_job.status == ExportJob.Status.SCHEDULED
    assert csv_job.exported_uploads_version_ids == [str(version.pk)]

    version_file = UploadsVersionFile.objects.create(
        uploaded_file=SimpleUploadedFile(
            "new-source.jpg",
            b"new-content",
            content_type="image/jpeg",
        ),
    )
    new_version = UploadsVersion.objects.create(
        dataset=dataset,
        version_file=version_file,
    )
    response = client.post(
        reverse("export-list"),
        {
            "export_type": get_content_type_for_model(UploadsDataset),
            "uuids": [str(dataset.pk)],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_202_ACCEPTED
    new_version_job = ExportJob.objects.get(pk=response.data["pk"])
    assert new_version_job.source_export_job is None
    assert new_version_job.status == ExportJob.Status.SCHEDULED
    assert new_version_job.exported_uploads_version_ids == [str(new_version.pk)]
    assert task_mock.call_count == 2


@pytest.mark.django_db
def test_create_export_job_stores_requested_uploads_version_ids(client, initial_users, mocker):
    user = initial_users["user_1"]
    _, version_1, _ = _create_uploads_version(user, dataset_name="Dataset 1")
    _, version_2, _ = _create_uploads_version(user, dataset_name="Dataset 2")
    task_mock = mocker.patch("fdm.exports.rest.views.build_export_zip.apply_async")
    mocker.patch("fdm.exports.rest.views.transaction.on_commit", side_effect=lambda callback: callback())

    response = client.post(
        reverse("export-list"),
        {
            "export_type": get_content_type_for_model(UploadsVersion),
            "uuids": [str(version_1.pk), str(version_2.pk)],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_202_ACCEPTED
    export_job = ExportJob.objects.get(pk=response.data["pk"])
    assert export_job.exported_uploads_version_ids == [str(version_1.pk), str(version_2.pk)]
    task_mock.assert_called_once()


@pytest.mark.django_db
def test_create_export_job_reuses_pending_source_job(client, initial_users, mocker):
    user_1 = initial_users["user_1"]
    user_2 = initial_users["user_2"]
    dataset, version, _ = _create_shared_dataset(user_1, user_2)
    source_job = ExportJob.objects.create(
        export_type=get_content_type_for_model(UploadsDataset),
        object_ids=[str(dataset.pk)],
        exported_uploads_version_ids=[str(version.pk)],
        status=ExportJob.Status.SCHEDULED,
        created_by=user_1,
        last_modified_by=user_1,
    )
    task_mock = mocker.patch("fdm.exports.rest.views.build_export_zip.apply_async")
    on_commit_mock = mocker.patch(
        "fdm.exports.rest.views.transaction.on_commit", side_effect=lambda callback: callback()
    )
    auth_user(client, user_2)

    response = client.post(
        reverse("export-list"),
        {
            "export_type": get_content_type_for_model(UploadsDataset),
            "uuids": [str(dataset.pk)],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_202_ACCEPTED
    reused_job = ExportJob.objects.get(pk=response.data["pk"])
    assert reused_job.pk != source_job.pk
    assert reused_job.created_by == user_2
    assert reused_job.source_export_job == source_job
    assert reused_job.status == ExportJob.Status.SCHEDULED
    task_mock.assert_not_called()
    on_commit_mock.assert_not_called()


@pytest.mark.django_db
def test_create_export_job_rejects_unsupported_content_type(client):
    url = reverse("export-list")

    response = client.post(
        url,
        {
            "export_type": "users.user",
            "uuids": ["7a49c23c-fa4d-4cb2-a619-abc4c2b3bbb1"],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_create_export_job_requires_at_least_one_export_part(client, initial_users, mocker):
    user = initial_users["user_1"]
    dataset, _, _ = _create_uploads_version(user)
    task_mock = mocker.patch("fdm.exports.rest.views.build_export_zip.apply_async")
    url = reverse("export-list")

    response = client.post(
        url,
        {
            "export_type": get_content_type_for_model(UploadsDataset),
            "uuids": [str(dataset.pk)],
            "include_uploads_version_file": False,
            "include_uploads_version_file_metadata": False,
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    task_mock.assert_not_called()


@pytest.mark.django_db
def test_list_export_jobs_can_filter_owner_jobs(client, initial_users):
    user = initial_users["user_1"]
    other_user = initial_users["user_2"]
    matching_job = ExportJob.objects.create(
        export_type=get_content_type_for_model(UploadsDataset),
        object_ids=["7a49c23c-fa4d-4cb2-a619-abc4c2b3bbb1"],
        metadata_format=ExportJob.MetadataFormat.JSON,
        status=ExportJob.Status.FINISHED,
        include_uploads_version_file=True,
        include_uploads_version_file_metadata=False,
        created_by=user,
        last_modified_by=user,
    )
    ExportJob.objects.create(
        export_type=get_content_type_for_model(UploadsDataset),
        object_ids=["7a49c23c-fa4d-4cb2-a619-abc4c2b3bbb2"],
        metadata_format=ExportJob.MetadataFormat.JSON,
        status=ExportJob.Status.FINISHED,
        include_uploads_version_file=True,
        include_uploads_version_file_metadata=False,
        created_by=other_user,
        last_modified_by=other_user,
    )
    ExportJob.objects.create(
        export_type=get_content_type_for_model(Project),
        object_ids=["7a49c23c-fa4d-4cb2-a619-abc4c2b3bbb3"],
        metadata_format=ExportJob.MetadataFormat.CSV,
        status=ExportJob.Status.ERROR,
        include_uploads_version_file=False,
        include_uploads_version_file_metadata=True,
        created_by=user,
        last_modified_by=user,
    )

    response = client.get(
        reverse("export-list"),
        {
            "status": ExportJob.Status.FINISHED,
            "export_type": get_content_type_for_model(UploadsDataset),
            "metadata_format": ExportJob.MetadataFormat.JSON,
            "include_uploads_version_file": "true",
            "include_uploads_version_file_metadata": "false",
        },
    )

    assert response.status_code == status.HTTP_200_OK
    assert [item["pk"] for item in _response_results(response)] == [str(matching_job.pk)]


@pytest.mark.django_db
def test_list_export_jobs_can_filter_by_exported_object_id(client, initial_users):
    user = initial_users["user_1"]
    other_user = initial_users["user_2"]
    dataset_id = "7a49c23c-fa4d-4cb2-a619-abc4c2b3bbb1"
    matching_job = ExportJob.objects.create(
        export_type=get_content_type_for_model(UploadsDataset),
        object_ids=[dataset_id],
        created_by=user,
        last_modified_by=user,
    )
    ExportJob.objects.create(
        export_type=get_content_type_for_model(UploadsDataset),
        object_ids=["7a49c23c-fa4d-4cb2-a619-abc4c2b3bbb2"],
        created_by=user,
        last_modified_by=user,
    )
    ExportJob.objects.create(
        export_type=get_content_type_for_model(UploadsDataset),
        object_ids=[dataset_id],
        created_by=other_user,
        last_modified_by=other_user,
    )

    response = client.get(
        reverse("export-list"),
        {
            "export_type": get_content_type_for_model(UploadsDataset),
            "object_id": dataset_id,
        },
    )

    assert response.status_code == status.HTTP_200_OK
    assert [item["pk"] for item in _response_results(response)] == [str(matching_job.pk)]


@pytest.mark.django_db
def test_list_export_jobs_can_order_by_finished_at_and_expires_at(client, initial_users):
    user = initial_users["user_1"]
    first_job = ExportJob.objects.create(
        export_type=get_content_type_for_model(UploadsDataset),
        object_ids=["7a49c23c-fa4d-4cb2-a619-abc4c2b3bbb1"],
        finished_at=timezone.now() + timezone.timedelta(hours=1),
        expires_at=timezone.now() + timezone.timedelta(hours=3),
        created_by=user,
        last_modified_by=user,
    )
    second_job = ExportJob.objects.create(
        export_type=get_content_type_for_model(UploadsDataset),
        object_ids=["7a49c23c-fa4d-4cb2-a619-abc4c2b3bbb2"],
        finished_at=timezone.now() + timezone.timedelta(hours=2),
        expires_at=timezone.now() + timezone.timedelta(hours=2),
        created_by=user,
        last_modified_by=user,
    )

    response = client.get(reverse("export-list"), {"ordering": "finished_at"})

    assert response.status_code == status.HTTP_200_OK
    assert [item["pk"] for item in _response_results(response)] == [str(first_job.pk), str(second_job.pk)]

    response = client.get(reverse("export-list"), {"ordering": "-expires_at"})

    assert response.status_code == status.HTTP_200_OK
    assert [item["pk"] for item in _response_results(response)] == [str(first_job.pk), str(second_job.pk)]


@pytest.mark.django_db
def test_build_version_export_uses_original_file_name_and_numeric_collision_suffix(settings, tmp_path, initial_users):
    settings.MEDIA_ROOT = tmp_path
    set_dbsettings_value("APP_TITLE", "My FDM")
    user = initial_users["user_1"]
    _, version_1, version_file_1 = _create_uploads_version(user, uploaded_name="stored-1.jpg", content=b"one")
    _, version_2, version_file_2 = _create_uploads_version(user, uploaded_name="stored-2.jpg", content=b"two")
    _set_file_name_metadata(version_file_1, "ORIGINAL_FILE_NAME", "my dataset.jpg")
    _set_file_name_metadata(version_file_2, "ORIGINAL_FILE_NAME", "my dataset.jpg")
    export_job = ExportJob.objects.create(
        export_type=get_content_type_for_model(UploadsVersion),
        object_ids=[str(version_1.pk), str(version_2.pk)],
        created_by=user,
        last_modified_by=user,
    )

    zip_file_name = build_export_zip_file(export_job)

    assert zip_file_name.startswith(f"exports/{export_job.pk}/")
    assert zip_file_name.endswith(
        f"My FDM UploadsVersion export from {timezone.localtime(export_job.creation_date).strftime('%Y-%m-%d %H-%M-%S')}.zip",
    )
    with zipfile.ZipFile(tmp_path / zip_file_name) as archive:
        assert sorted(archive.namelist()) == [
            "additional-metadata (1).json",
            "additional-metadata.json",
            "file-metadata (1).json",
            "file-metadata.json",
            "my dataset (1).jpg",
            "my dataset.jpg",
        ]


@pytest.mark.django_db
def test_build_dataset_export_uses_dataset_name_for_file_name(settings, tmp_path, initial_users):
    settings.MEDIA_ROOT = tmp_path
    user = initial_users["user_1"]
    dataset, version, version_file = _create_uploads_version(
        user,
        dataset_name="Dataset Name",
        uploaded_name="source.jpg",
    )
    _set_file_name_metadata(version_file, "ORIGINAL_FILE_NAME", "original.jpg")
    _set_uploads_version_metadata(version, "VERSION_KEY", "version value")
    export_job = ExportJob.objects.create(
        export_type=get_content_type_for_model(UploadsDataset),
        object_ids=[str(dataset.pk)],
        created_by=user,
        last_modified_by=user,
    )

    zip_file_name = build_export_zip_file(export_job)

    with zipfile.ZipFile(tmp_path / zip_file_name) as archive:
        assert "Dataset Name/Dataset Name.jpg" in archive.namelist()
        assert "Dataset Name/file-metadata.json" in archive.namelist()
        assert "Dataset Name/additional-metadata.json" in archive.namelist()
        assert "Dataset Name/original.jpg" not in archive.namelist()
        file_metadata = json.loads(archive.read("Dataset Name/file-metadata.json"))
        version_metadata = json.loads(archive.read("Dataset Name/additional-metadata.json"))
        assert list(file_metadata.keys()) == ["metadata"]
        assert list(version_metadata.keys()) == ["metadata"]
        assert [item["key"] for item in file_metadata["metadata"]] == ["ORIGINAL_FILE_NAME"]
        assert [item["key"] for item in version_metadata["metadata"]] == ["VERSION_KEY"]


@pytest.mark.django_db
def test_build_dataset_export_persists_generated_zip_path_longer_than_100_characters(
    settings,
    tmp_path,
    initial_users,
    mocker,
):
    settings.MEDIA_ROOT = tmp_path
    set_dbsettings_value("APP_TITLE", "Research Data Management")
    user = initial_users["user_1"]
    dataset, _, _ = _create_uploads_version(user)
    export_job = ExportJob.objects.create(
        export_type=get_content_type_for_model(UploadsDataset),
        object_ids=[str(dataset.pk)],
        created_by=user,
        last_modified_by=user,
    )
    mocker.patch("fdm.users.models.User.send_mail")

    build_export_zip(str(export_job.pk))

    export_job.refresh_from_db()
    assert len(export_job.zip_file.name) > 100
    assert export_job.status == ExportJob.Status.FINISHED
    assert (tmp_path / export_job.zip_file.name).exists()


@pytest.mark.django_db
def test_build_dataset_export_can_include_only_uploaded_file(settings, tmp_path, initial_users):
    settings.MEDIA_ROOT = tmp_path
    user = initial_users["user_1"]
    dataset, version, version_file = _create_uploads_version(
        user,
        dataset_name="Dataset Name",
        uploaded_name="source.jpg",
    )
    _set_file_name_metadata(version_file, "ORIGINAL_FILE_NAME", "original.jpg")
    _set_uploads_version_metadata(version, "VERSION_KEY", "version value")
    export_job = ExportJob.objects.create(
        export_type=get_content_type_for_model(UploadsDataset),
        object_ids=[str(dataset.pk)],
        include_uploads_version_file=True,
        include_uploads_version_file_metadata=False,
        created_by=user,
        last_modified_by=user,
    )

    zip_file_name = build_export_zip_file(export_job)

    with zipfile.ZipFile(tmp_path / zip_file_name) as archive:
        assert sorted(archive.namelist()) == ["Dataset Name/Dataset Name.jpg"]


@pytest.mark.django_db
def test_build_dataset_export_can_include_only_metadata(settings, tmp_path, initial_users):
    settings.MEDIA_ROOT = tmp_path
    user = initial_users["user_1"]
    dataset, version, version_file = _create_uploads_version(
        user,
        dataset_name="Dataset Name",
        uploaded_name="source.jpg",
    )
    _set_file_name_metadata(version_file, "ORIGINAL_FILE_NAME", "original.jpg")
    _set_uploads_version_metadata(version, "VERSION_KEY", "version value")
    export_job = ExportJob.objects.create(
        export_type=get_content_type_for_model(UploadsDataset),
        object_ids=[str(dataset.pk)],
        include_uploads_version_file=False,
        include_uploads_version_file_metadata=True,
        created_by=user,
        last_modified_by=user,
    )

    zip_file_name = build_export_zip_file(export_job)

    with zipfile.ZipFile(tmp_path / zip_file_name) as archive:
        assert sorted(archive.namelist()) == [
            "Dataset Name/additional-metadata.json",
            "Dataset Name/file-metadata.json",
        ]
        file_metadata = json.loads(archive.read("Dataset Name/file-metadata.json"))
        version_metadata = json.loads(archive.read("Dataset Name/additional-metadata.json"))
        assert [item["key"] for item in file_metadata["metadata"]] == ["ORIGINAL_FILE_NAME"]
        assert [item["key"] for item in version_metadata["metadata"]] == ["VERSION_KEY"]


@pytest.mark.django_db
def test_project_export_reuses_project_and_folder_directories(settings, tmp_path, initial_users):
    settings.MEDIA_ROOT = tmp_path
    user = initial_users["user_1"]
    set_request_for_user(user)
    project = Project.objects.create(name="Project")
    project_membership = ProjectMembership.objects.get(
        project=project,
        member=user,
    )
    folder = Folder.objects.create(
        project=project,
        name="Folder",
    )
    FolderPermission.objects.get(
        folder=folder,
        project_membership=project_membership,
    )

    dataset_1, _, _ = _create_uploads_version(user, dataset_name="Dataset 1", uploaded_name="source-1.jpg")
    dataset_1.folder = folder
    dataset_1.publication_date = timezone.now()
    dataset_1.save()

    dataset_2, _, _ = _create_uploads_version(user, dataset_name="Dataset 2", uploaded_name="source-2.jpg")
    dataset_2.folder = folder
    dataset_2.publication_date = timezone.now()
    dataset_2.save()

    export_job = ExportJob.objects.create(
        export_type=get_content_type_for_model(Project),
        object_ids=[str(project.pk)],
        created_by=user,
        last_modified_by=user,
    )

    zip_file_name = build_export_zip_file(export_job)

    with zipfile.ZipFile(tmp_path / zip_file_name) as archive:
        names = archive.namelist()
        assert "Project/Folder/Dataset 1/Dataset 1.jpg" in names
        assert "Project/Folder/Dataset 2/Dataset 2.jpg" in names
        assert not any(name.startswith("Project (1)/") for name in names)
        assert not any("/Folder (1)/" in name for name in names)


@pytest.mark.django_db
def test_build_export_zip_finishes_reused_pending_jobs(settings, tmp_path, client, initial_users, mocker):
    settings.MEDIA_ROOT = tmp_path
    user_1 = initial_users["user_1"]
    user_2 = initial_users["user_2"]
    dataset, version, _ = _create_shared_dataset(user_1, user_2)
    source_job = ExportJob.objects.create(
        export_type=get_content_type_for_model(UploadsDataset),
        object_ids=[str(dataset.pk)],
        exported_uploads_version_ids=[str(version.pk)],
        status=ExportJob.Status.SCHEDULED,
        created_by=user_1,
        last_modified_by=user_1,
    )
    reused_job = ExportJob.objects.create(
        export_type=source_job.export_type,
        object_ids=source_job.object_ids,
        exported_uploads_version_ids=source_job.exported_uploads_version_ids,
        source_export_job=source_job,
        status=ExportJob.Status.SCHEDULED,
        download_base_url=f"http://testserver{reverse('export-download', kwargs={'pk': source_job.pk})}",
        created_by=user_2,
        last_modified_by=user_2,
    )
    send_mail_mock = mocker.patch("fdm.users.models.User.send_mail")

    build_export_zip(str(source_job.pk))

    source_job.refresh_from_db()
    reused_job.refresh_from_db()
    assert source_job.status == ExportJob.Status.FINISHED
    assert reused_job.status == ExportJob.Status.FINISHED
    assert reused_job.zip_file.name == source_job.zip_file.name
    assert source_job.download_token_hash
    assert reused_job.download_token_hash
    assert source_job.download_token_hash != reused_job.download_token_hash
    assert reused_job.expires_at >= source_job.finished_at
    assert send_mail_mock.call_count == 2

    auth_user(client, user_2)
    response = client.get(reverse("export-download", kwargs={"pk": reused_job.pk}))

    assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_build_export_zip_marks_reused_pending_jobs_as_error(initial_users, mocker):
    user = initial_users["user_1"]
    dataset, version, _ = _create_uploads_version(user)
    source_job = ExportJob.objects.create(
        export_type=get_content_type_for_model(UploadsDataset),
        object_ids=[str(dataset.pk)],
        exported_uploads_version_ids=[str(version.pk)],
        status=ExportJob.Status.SCHEDULED,
        created_by=user,
        last_modified_by=user,
    )
    reused_job = ExportJob.objects.create(
        export_type=source_job.export_type,
        object_ids=source_job.object_ids,
        exported_uploads_version_ids=source_job.exported_uploads_version_ids,
        source_export_job=source_job,
        status=ExportJob.Status.SCHEDULED,
        created_by=user,
        last_modified_by=user,
    )
    mocker.patch("fdm.exports.tasks.build_export_zip_file", side_effect=ValidationError("boom"))

    build_export_zip(str(source_job.pk))

    source_job.refresh_from_db()
    reused_job.refresh_from_db()
    assert source_job.status == ExportJob.Status.ERROR
    assert reused_job.status == ExportJob.Status.ERROR
    assert "boom" in source_job.error_message
    assert reused_job.error_message == source_job.error_message


@pytest.mark.django_db
def test_owner_can_inspect_finished_job_with_download_url(settings, tmp_path, client, initial_users):
    settings.MEDIA_ROOT = tmp_path
    user = initial_users["user_1"]
    set_request_for_user(user)
    export_job = ExportJob.objects.create(
        export_type=get_content_type_for_model(UploadsDataset),
        object_ids=["7a49c23c-fa4d-4cb2-a619-abc4c2b3bbb1"],
        status=ExportJob.Status.FINISHED,
        expires_at=timezone.now() + timezone.timedelta(hours=1),
        created_by=user,
        last_modified_by=user,
    )
    export_job.zip_file.name = f"exports/{export_job.pk}/export.zip"
    export_job.save()

    response = client.get(reverse("export-detail", kwargs={"pk": export_job.pk}))

    assert response.status_code == status.HTTP_200_OK
    assert response.data["status"] == ExportJob.Status.FINISHED
    assert response.data["download_url"] == (
        f"http://testserver{reverse('export-download', kwargs={'pk': export_job.pk})}"
    )

    auth_user(client, initial_users["user_2"])
    response = client.get(reverse("export-detail", kwargs={"pk": export_job.pk}))

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_owner_can_download_finished_export_without_token(settings, tmp_path, client, initial_users):
    settings.MEDIA_ROOT = tmp_path
    user = initial_users["user_1"]
    set_request_for_user(user)
    export_job = ExportJob.objects.create(
        export_type=get_content_type_for_model(UploadsDataset),
        object_ids=["7a49c23c-fa4d-4cb2-a619-abc4c2b3bbb1"],
        status=ExportJob.Status.FINISHED,
        expires_at=timezone.now() + timezone.timedelta(hours=1),
        created_by=user,
        last_modified_by=user,
    )
    export_path = tmp_path / "exports" / str(export_job.pk) / "export.zip"
    export_path.parent.mkdir(parents=True)
    export_path.write_bytes(b"zip-content")
    export_job.zip_file.name = f"exports/{export_job.pk}/export.zip"
    export_job.save()

    response = client.get(reverse("export-download", kwargs={"pk": export_job.pk}))

    assert response.status_code == status.HTTP_200_OK

    auth_user(client, initial_users["user_2"])
    response = client.get(reverse("export-download", kwargs={"pk": export_job.pk}))

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_token_only_download_and_cleanup(settings, tmp_path, client, initial_users):
    settings.MEDIA_ROOT = tmp_path
    user = initial_users["user_1"]
    set_request_for_user(user)
    export_job = ExportJob.objects.create(
        export_type=get_content_type_for_model(UploadsDataset),
        object_ids=["7a49c23c-fa4d-4cb2-a619-abc4c2b3bbb1"],
        status=ExportJob.Status.FINISHED,
        expires_at=timezone.now() + timezone.timedelta(hours=1),
        created_by=user,
        last_modified_by=user,
    )
    export_job.set_download_token("token")
    export_path = tmp_path / "exports" / str(export_job.pk) / "export.zip"
    export_path.parent.mkdir(parents=True)
    export_path.write_bytes(b"zip-content")
    export_job.zip_file.name = f"exports/{export_job.pk}/export.zip"
    export_job.save()
    client.credentials()

    response = client.get(
        reverse("export-download", kwargs={"pk": export_job.pk}),
        {"token": "token"},
    )

    assert response.status_code == status.HTTP_200_OK

    export_job.expires_at = timezone.now() - timezone.timedelta(seconds=1)
    export_job.save()
    assert cleanup_expired_export_jobs() == 1
    assert not export_path.exists()
    assert not ExportJob.objects.filter(pk=export_job.pk).exists()


@pytest.mark.django_db
def test_cleanup_expired_export_jobs_preserves_shared_zip_until_last_reference(settings, tmp_path, initial_users):
    user_1 = initial_users["user_1"]
    user_2 = initial_users["user_2"]
    source_job = ExportJob.objects.create(
        export_type=get_content_type_for_model(UploadsDataset),
        object_ids=["7a49c23c-fa4d-4cb2-a619-abc4c2b3bbb1"],
        status=ExportJob.Status.FINISHED,
        expires_at=timezone.now() - timezone.timedelta(seconds=1),
        created_by=user_1,
        last_modified_by=user_1,
    )
    export_path = _add_zip_file(settings, tmp_path, source_job)
    reused_job = ExportJob.objects.create(
        export_type=source_job.export_type,
        object_ids=source_job.object_ids,
        source_export_job=source_job,
        status=ExportJob.Status.FINISHED,
        zip_file=source_job.zip_file.name,
        expires_at=timezone.now() + timezone.timedelta(hours=1),
        created_by=user_2,
        last_modified_by=user_2,
    )

    assert cleanup_expired_export_jobs() == 1

    assert export_path.exists()
    assert not ExportJob.objects.filter(pk=source_job.pk).exists()
    assert ExportJob.objects.filter(pk=reused_job.pk).exists()

    reused_job.refresh_from_db()
    reused_job.expires_at = timezone.now() - timezone.timedelta(seconds=1)
    reused_job.save(update_fields=["expires_at"])

    assert cleanup_expired_export_jobs() == 1
    assert not export_path.exists()
    assert not ExportJob.objects.filter(pk=reused_job.pk).exists()
