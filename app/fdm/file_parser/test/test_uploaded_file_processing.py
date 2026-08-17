import uuid

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from fdm.core.helpers import set_request_for_user
from fdm.file_parser.models import FileParser, FileParserConfiguration
from fdm.file_parser.tasks import process_uploaded_file, reconcile_uploaded_file_processing
from fdm.metadata.models import Metadata
from fdm.projects.models import Project
from fdm.uploads.helpers import create_uploads_version_with_new_file_for_dataset
from fdm.uploads.models import UploadsDataset, UploadsVersionFile


@pytest.mark.django_db
def test_uploaded_file_processing_finishes_code_owned_metadata(initial_users):
    version_file = UploadsVersionFile.objects.create(
        uploaded_file=SimpleUploadedFile("sample.txt", b"research data", content_type="text/plain"),
    )

    process_uploaded_file(str(version_file.pk))

    version_file.refresh_from_db()
    assert version_file.status == UploadsVersionFile.Status.FINISHED
    assert set(
        Metadata.objects.filter(
            assigned_to_content_type=version_file.get_content_type(),
            assigned_to_object_id=version_file.pk,
        ).values_list("custom_key", flat=True),
    ) >= {"MIME_TYPE", "CHECKSUM_SHA256", "FILE_NAME", "FILE_SIZE", "FILE_RELATIVE_PATH"}
    assert set(version_file.file_parser.values_list("parser_type", "status")) == {
        (FileParser.Type.MIME_TYPE, FileParser.Status.FINISHED),
        (FileParser.Type.CHECKSUM_SHA256, FileParser.Status.FINISHED),
        (FileParser.Type.FILE_INFORMATION, FileParser.Status.FINISHED),
    }


@pytest.mark.django_db
def test_uploaded_file_processing_continues_after_parser_failure(initial_users, mocker):
    version_file = UploadsVersionFile.objects.create(
        uploaded_file=SimpleUploadedFile("sample.txt", b"research data", content_type="text/plain"),
    )
    mocker.patch(
        "fdm.file_parser.models.models.magic.from_file",
        side_effect=[RuntimeError("MIME extraction failed"), "text/plain"],
    )

    process_uploaded_file(str(version_file.pk))

    version_file.refresh_from_db()
    assert version_file.status == UploadsVersionFile.Status.ERROR
    assert set(version_file.file_parser.values_list("parser_type", "status")) == {
        (FileParser.Type.MIME_TYPE, FileParser.Status.ERROR),
        (FileParser.Type.CHECKSUM_SHA256, FileParser.Status.FINISHED),
        (FileParser.Type.FILE_INFORMATION, FileParser.Status.FINISHED),
    }


@pytest.mark.django_db
def test_uploaded_file_processing_ignores_duplicate_delivery_with_active_lease(initial_users):
    version_file = UploadsVersionFile.objects.create(
        uploaded_file=SimpleUploadedFile("sample.txt", b"research data", content_type="text/plain"),
        status=UploadsVersionFile.Status.IN_PROGRESS,
        processing_lease_token=uuid.uuid4(),
        processing_lease_expires_at=timezone.now() + timezone.timedelta(minutes=10),
    )

    process_uploaded_file(str(version_file.pk))

    version_file.refresh_from_db()
    assert version_file.status == UploadsVersionFile.Status.IN_PROGRESS
    assert not version_file.file_parser.exists()


@pytest.mark.django_db
def test_uploaded_file_processing_uses_configured_parser_snapshot(initial_users, mocker):
    set_request_for_user(initial_users["user_1"])
    mocker.patch("fdm.file_parser.models.models.magic.from_file", return_value="text/csv")
    project = Project.objects.create(name="Project")
    dataset = UploadsDataset.objects.create(name="Dataset", folder=project.folder.first())
    configuration = FileParserConfiguration.objects.create(
        parser_type=FileParser.Type.CSV_VALUE,
        assigned_to_content_type=dataset.get_content_type(),
        assigned_to_object_id=dataset.pk,
        config=[{"row": 1, "column": 1, "key": "sample_id"}],
    )
    version = create_uploads_version_with_new_file_for_dataset(
        dataset=dataset,
        uploaded_file=SimpleUploadedFile(
            "sample.csv",
            b"Key,Value\nSAMPLE_ID,original\n",
            content_type="text/csv",
        ),
    )
    configuration.config = [{"row": 99, "column": 99, "key": "changed"}]
    configuration.save(update_fields=["config"])
    configuration.delete()

    process_uploaded_file(str(version.version_file.pk))

    version.version_file.refresh_from_db()
    configured_task = version.version_file.file_parser.get(parser_type=FileParser.Type.CSV_VALUE)
    assert configured_task.status == FileParser.Status.FINISHED
    assert (
        Metadata.objects.get(
            assigned_to_content_type=version.version_file.get_content_type(),
            assigned_to_object_id=version.version_file.pk,
            custom_key="SAMPLE_ID",
        ).get_value()
        == "original"
    )


@pytest.mark.django_db
def test_configured_parser_failure_does_not_stop_other_uploaded_file_processing(initial_users, mocker):
    set_request_for_user(initial_users["user_1"])
    mocker.patch("fdm.file_parser.models.models.magic.from_file", return_value="text/csv")
    project = Project.objects.create(name="Project")
    dataset = UploadsDataset.objects.create(name="Dataset", folder=project.folder.first())
    configuration = FileParserConfiguration.objects.create(
        parser_type=FileParser.Type.CSV_VALUE,
        assigned_to_content_type=dataset.get_content_type(),
        assigned_to_object_id=dataset.pk,
        config=[{"row": 99, "column": 1, "key": "missing"}],
    )
    version = create_uploads_version_with_new_file_for_dataset(
        dataset=dataset,
        uploaded_file=SimpleUploadedFile("sample.csv", b"Key,Value\nSAMPLE_ID,value\n", content_type="text/csv"),
    )

    process_uploaded_file(str(version.version_file.pk))

    version.version_file.refresh_from_db()
    assert version.version_file.status == UploadsVersionFile.Status.ERROR
    assert version.version_file.file_parser.get(configuration=configuration).status == FileParser.Status.ERROR
    assert (
        version.version_file.file_parser.get(
            parser_type=FileParser.Type.CHECKSUM_SHA256,
            configuration=None,
        ).status
        == FileParser.Status.FINISHED
    )


@pytest.mark.django_db(transaction=True)
def test_upload_completion_dispatches_processing_after_commit(initial_users, mocker):
    set_request_for_user(initial_users["user_1"])
    dispatch = mocker.patch("fdm.file_parser.tasks.process_uploaded_file.apply_async")

    version = create_uploads_version_with_new_file_for_dataset(
        uploaded_file=SimpleUploadedFile("sample.txt", b"research data", content_type="text/plain"),
    )

    dispatch.assert_called_once_with((str(version.version_file.pk),))


@pytest.mark.django_db(transaction=True)
def test_rolled_back_upload_does_not_dispatch_processing(initial_users, mocker):
    set_request_for_user(initial_users["user_1"])
    dispatch = mocker.patch("fdm.file_parser.tasks.process_uploaded_file.apply_async")

    with pytest.raises(RuntimeError), transaction.atomic():
        create_uploads_version_with_new_file_for_dataset(
            uploaded_file=SimpleUploadedFile("sample.txt", b"research data", content_type="text/plain"),
        )
        raise RuntimeError("roll back upload")

    dispatch.assert_not_called()
    assert not UploadsVersionFile.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_upload_completion_survives_processing_dispatch_failure(initial_users, mocker):
    set_request_for_user(initial_users["user_1"])
    mocker.patch(
        "fdm.file_parser.tasks.process_uploaded_file.apply_async",
        side_effect=RuntimeError("broker unavailable"),
    )

    version = create_uploads_version_with_new_file_for_dataset(
        uploaded_file=SimpleUploadedFile("sample.txt", b"research data", content_type="text/plain"),
    )

    version.version_file.refresh_from_db()
    assert version.version_file.status == UploadsVersionFile.Status.SCHEDULED
    assert set(version.version_file.file_parser.values_list("parser_type", flat=True)) >= {
        FileParser.Type.MIME_TYPE,
        FileParser.Type.CHECKSUM_SHA256,
        FileParser.Type.FILE_INFORMATION,
    }


@pytest.mark.django_db
def test_reconciliation_dispatches_scheduled_processing(initial_users, mocker):
    version_file = UploadsVersionFile.objects.create(
        uploaded_file=SimpleUploadedFile("sample.txt", b"research data", content_type="text/plain"),
    )
    dispatch = mocker.patch("fdm.file_parser.tasks.process_uploaded_file.apply_async")

    reconcile_uploaded_file_processing()

    dispatch.assert_called_once_with((str(version_file.pk),))


@pytest.mark.django_db
def test_reconciliation_dispatches_only_abandoned_nonterminal_processing(initial_users, mocker):
    now = timezone.now()
    active_file = UploadsVersionFile.objects.create(
        uploaded_file=SimpleUploadedFile("active.txt", b"active"),
        status=UploadsVersionFile.Status.IN_PROGRESS,
        processing_lease_token=uuid.uuid4(),
        processing_lease_expires_at=now + timezone.timedelta(minutes=10),
    )
    abandoned_file = UploadsVersionFile.objects.create(
        uploaded_file=SimpleUploadedFile("abandoned.txt", b"abandoned"),
        status=UploadsVersionFile.Status.IN_PROGRESS,
        processing_lease_token=uuid.uuid4(),
        processing_lease_expires_at=now - timezone.timedelta(minutes=1),
    )
    legacy_file = UploadsVersionFile.objects.create(
        uploaded_file=SimpleUploadedFile("legacy.txt", b"legacy"),
        status=UploadsVersionFile.Status.IN_PROGRESS,
    )
    UploadsVersionFile.objects.filter(pk=legacy_file.pk).update(
        last_modification_date=now - timezone.timedelta(minutes=10),
    )
    UploadsVersionFile.objects.create(status=UploadsVersionFile.Status.ERROR)
    UploadsVersionFile.objects.create(status=UploadsVersionFile.Status.FINISHED)
    dispatch = mocker.patch("fdm.file_parser.tasks.process_uploaded_file.apply_async")

    reconcile_uploaded_file_processing()

    assert {invocation.args[0][0] for invocation in dispatch.call_args_list} == {
        str(abandoned_file.pk),
        str(legacy_file.pk),
    }
    active_file.refresh_from_db()
    assert active_file.status == UploadsVersionFile.Status.IN_PROGRESS


@pytest.mark.django_db
def test_authorized_user_retries_failed_uploaded_file_processing(
    initial_users,
    client,
    mocker,
    django_capture_on_commit_callbacks,
):
    set_request_for_user(initial_users["user_1"])
    version_file = UploadsVersionFile.objects.create(
        uploaded_file=SimpleUploadedFile("sample.txt", b"research data", content_type="text/plain"),
        status=UploadsVersionFile.Status.ERROR,
    )
    finished_task = FileParser.objects.create(
        file=version_file,
        parser_type=FileParser.Type.MIME_TYPE,
        status=FileParser.Status.FINISHED,
    )
    failed_task = FileParser.objects.create(
        file=version_file,
        parser_type=FileParser.Type.CHECKSUM_SHA256,
        status=FileParser.Status.ERROR,
    )
    dispatch = mocker.patch("fdm.file_parser.tasks.process_uploaded_file.apply_async")

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            reverse("uploads-version-file-retry-processing", kwargs={"pk": version_file.pk}),
            format="json",
        )

    assert response.status_code == status.HTTP_202_ACCEPTED
    version_file.refresh_from_db()
    finished_task.refresh_from_db()
    failed_task.refresh_from_db()
    assert version_file.status == UploadsVersionFile.Status.SCHEDULED
    assert finished_task.status == FileParser.Status.FINISHED
    assert failed_task.status == FileParser.Status.SCHEDULED
    dispatch.assert_called_once_with((str(version_file.pk),))

    duplicate_response = client.post(
        reverse("uploads-version-file-retry-processing", kwargs={"pk": version_file.pk}),
        format="json",
    )
    assert duplicate_response.status_code == status.HTTP_202_ACCEPTED
    dispatch.assert_called_once_with((str(version_file.pk),))
