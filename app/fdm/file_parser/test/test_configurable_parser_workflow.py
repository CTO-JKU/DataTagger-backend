import pytest
from conftest import auth_user
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status

from fdm.core.helpers import get_content_type_for_object, set_request_for_user
from fdm.file_parser.models import FileParser, FileParserConfiguration
from fdm.file_parser.tasks import check_configured_file_parsers, process_uploaded_file, run_configured_file_parsers
from fdm.metadata.helpers import set_metadata
from fdm.metadata.models import Metadata
from fdm.projects.models import Project
from fdm.uploads.helpers import create_uploads_version_with_new_file_for_dataset
from fdm.uploads.models import UploadsDataset, UploadsVersionFile


def _csv_file(name="sample.csv", content=b"Key,Value\nSAMPLE_ID,A-1\nSAMPLE_ID,A-2\n"):
    return SimpleUploadedFile(name, content, content_type="text/csv")


def _text_file():
    return SimpleUploadedFile("sample.txt", b"not,csv\n", content_type="text/plain")


def _set_stored_mime_type(file, mime_type):
    Metadata.objects.filter(
        assigned_to_content_type=file.get_content_type(),
        assigned_to_object_id=file.pk,
        custom_key="MIME_TYPE",
    ).delete()
    set_metadata(
        assigned_to_content_type=file.get_content_type(),
        assigned_to_object_id=file.pk,
        custom_key="MIME_TYPE",
        value=mime_type,
        read_only=True,
    )


@pytest.mark.django_db
class TestConfigurableCsvParserWorkflow:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])
        self.project = Project.objects.create(name="Project 1")
        self.folder = self.project.folder.first()
        self.dataset = UploadsDataset.objects.create(name="Dataset 1", folder=self.folder)

    def _payload(self, scope, config):
        return {
            "parser_type": FileParser.Type.CSV_VALUE,
            "assigned_to_content_type": get_content_type_for_object(scope.get_content_type()),
            "assigned_to_object_id": scope.pk,
            "config": config,
        }

    def test_future_csv_upload_is_processed_with_multiple_duplicate_metadata_rows(self, client, mocker):
        """
        Future CSV uploads run all applicable parser assignments and preserve duplicate metadata keys.
        """
        mocker.patch("fdm.file_parser.models.models.magic.from_file", return_value="text/csv")
        url = reverse("file-parser-configuration-list")
        for scope, value_column in [(self.project, 1), (self.folder, 1), (self.dataset, 1)]:
            response = client.post(
                url,
                self._payload(
                    scope,
                    [
                        {"row": 1, "column": value_column, "key": "sample_id"},
                        {"row": 2, "column": value_column, "key": {"row": 2, "column": 0}},
                    ],
                ),
                format="json",
            )
            assert response.status_code == status.HTTP_201_CREATED

        uploads_version = create_uploads_version_with_new_file_for_dataset(
            dataset=self.dataset,
            uploaded_file=_csv_file(),
        )
        process_uploaded_file(str(uploads_version.version_file.pk))

        processing_url = reverse(
            "uploads-version-file-parser-processing",
            kwargs={"pk": uploads_version.version_file.pk},
        )
        processing_response = client.get(processing_url, format="json")

        assert processing_response.status_code == status.HTTP_200_OK
        assert [item["status"] for item in processing_response.data] == [
            FileParser.Status.FINISHED,
            FileParser.Status.FINISHED,
            FileParser.Status.FINISHED,
        ]

        metadata = Metadata.objects.filter(
            assigned_to_content_type=uploads_version.version_file.get_content_type(),
            assigned_to_object_id=uploads_version.version_file.pk,
            custom_key="SAMPLE_ID",
        ).order_by("creation_date")
        assert metadata.count() == 6
        assert [item.get_value() for item in metadata] == ["A-1", "A-2", "A-1", "A-2", "A-1", "A-2"]
        assert all(item.read_only for item in metadata)

    def test_csv_parser_failure_is_atomic_and_does_not_fail_upload_file(self, client):
        """
        A broken extraction rule fails only its processing record and writes no partial metadata.
        """
        response = client.post(
            reverse("file-parser-configuration-list"),
            self._payload(
                self.dataset,
                [
                    {"row": 1, "column": 1, "key": "valid_key"},
                    {"row": 99, "column": 1, "key": "missing_value"},
                ],
            ),
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        uploads_version = create_uploads_version_with_new_file_for_dataset(
            dataset=self.dataset,
            uploaded_file=_csv_file(),
        )
        _set_stored_mime_type(uploads_version.version_file, "text/csv")
        check_configured_file_parsers(uploads_version.version_file)
        UploadsVersionFile.objects.filter(pk=uploads_version.version_file.pk).update(
            status=UploadsVersionFile.Status.FINISHED,
        )

        run_configured_file_parsers()
        uploads_version.version_file.refresh_from_db()

        assert uploads_version.version_file.status == UploadsVersionFile.Status.FINISHED
        assert (
            FileParser.objects.get(file=uploads_version.version_file, configuration__isnull=False).status
            == FileParser.Status.ERROR
        )
        assert not Metadata.objects.filter(
            assigned_to_content_type=uploads_version.version_file.get_content_type(),
            assigned_to_object_id=uploads_version.version_file.pk,
            custom_key__in=["VALID_KEY", "MISSING_VALUE"],
        ).exists()

    def test_dataset_assignment_backfills_existing_csv_files_only(self, client):
        """
        Dataset-level assignments schedule existing eligible dataset files, unlike project/folder assignments.
        """
        csv_version = create_uploads_version_with_new_file_for_dataset(dataset=self.dataset, uploaded_file=_csv_file())
        text_version = create_uploads_version_with_new_file_for_dataset(
            dataset=self.dataset, uploaded_file=_text_file()
        )
        _set_stored_mime_type(csv_version.version_file, "text/csv")
        _set_stored_mime_type(text_version.version_file, "text/plain")

        response = client.post(
            reverse("file-parser-configuration-list"),
            self._payload(self.project, [{"row": 1, "column": 1, "key": "PROJECT_KEY"}]),
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert not FileParser.objects.filter(parser_type=FileParser.Type.CSV_VALUE).exists()

        response = client.post(
            reverse("file-parser-configuration-list"),
            self._payload(self.dataset, [{"row": 1, "column": 1, "key": "DATASET_KEY"}]),
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        assert (
            FileParser.objects.filter(file=csv_version.version_file, parser_type=FileParser.Type.CSV_VALUE).count() == 1
        )
        assert not FileParser.objects.filter(
            file=text_version.version_file, parser_type=FileParser.Type.CSV_VALUE
        ).exists()

    def test_retrigger_schedules_missing_and_errored_processing_but_not_finished_or_deleted(self, client):
        """
        File-level re-trigger recovers missing/errored work without duplicating completed or deleted assignments.
        """
        config = FileParserConfiguration.objects.create(
            parser_type=FileParser.Type.CSV_VALUE,
            assigned_to_content_type=self.dataset.get_content_type(),
            assigned_to_object_id=self.dataset.pk,
            config=[{"row": 99, "column": 1, "key": "BROKEN"}],
        )
        uploads_version = create_uploads_version_with_new_file_for_dataset(
            dataset=self.dataset,
            uploaded_file=_csv_file(),
        )
        _set_stored_mime_type(uploads_version.version_file, "text/csv")
        check_configured_file_parsers(uploads_version.version_file)
        run_configured_file_parsers()
        assert (
            FileParser.objects.get(file=uploads_version.version_file, configuration=config).status
            == FileParser.Status.ERROR
        )

        config.config = [{"row": 1, "column": 1, "key": "FIXED"}]
        config.save()

        retry_url = reverse(
            "uploads-version-file-retrigger-parser-processing",
            kwargs={"pk": uploads_version.version_file.pk},
        )
        retry_response = client.post(retry_url, format="json")
        assert retry_response.status_code == status.HTTP_200_OK
        assert retry_response.data[0]["action"] == "retried"

        run_configured_file_parsers()
        assert (
            Metadata.objects.filter(
                assigned_to_content_type=uploads_version.version_file.get_content_type(),
                assigned_to_object_id=uploads_version.version_file.pk,
                custom_key="FIXED",
            ).count()
            == 1
        )

        retry_response = client.post(retry_url, format="json")
        assert retry_response.status_code == status.HTTP_200_OK
        assert retry_response.data[0]["action"] == "already_finished"

        config.delete()
        retry_response = client.post(retry_url, format="json")
        assert retry_response.status_code == status.HTTP_200_OK
        assert retry_response.data == []
        assert (
            Metadata.objects.filter(
                assigned_to_content_type=uploads_version.version_file.get_content_type(),
                assigned_to_object_id=uploads_version.version_file.pk,
                custom_key="FIXED",
            ).count()
            == 1
        )

    def test_parser_processing_actions_require_file_scope_permission(self, client, initial_users):
        """
        Users outside the file's folder cannot inspect or re-trigger parser processing.
        """
        uploads_version = create_uploads_version_with_new_file_for_dataset(
            dataset=self.dataset,
            uploaded_file=_csv_file(),
        )
        auth_user(client, initial_users["user_2"])

        processing_url = reverse(
            "uploads-version-file-parser-processing",
            kwargs={"pk": uploads_version.version_file.pk},
        )
        retry_url = reverse(
            "uploads-version-file-retrigger-parser-processing",
            kwargs={"pk": uploads_version.version_file.pk},
        )
        complete_retry_url = reverse(
            "uploads-version-file-retry-processing",
            kwargs={"pk": uploads_version.version_file.pk},
        )

        assert client.get(processing_url, format="json").status_code == status.HTTP_403_FORBIDDEN
        assert client.post(retry_url, format="json").status_code == status.HTTP_403_FORBIDDEN
        assert client.post(complete_retry_url, format="json").status_code == status.HTTP_403_FORBIDDEN
