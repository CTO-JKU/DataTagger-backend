import logging

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from fdm.core.helpers import set_request_for_user
from fdm.projects.models import Project
from fdm.rest_framework_tus.models import Upload
from fdm.uploads.handlers import save_finished_tus_upload_as_uploads_version_file
from fdm.uploads.models import UploadsDataset

sample_file = SimpleUploadedFile(
    "tus-upload.txt",
    b"payload",
    content_type="text/plain",
)


@pytest.mark.django_db
class TestUploadsHandlers:
    def test_save_finished_tus_upload_sets_error_and_unlocks_on_publish_failure(
        self,
        initial_users,
        monkeypatch,
        caplog,
    ):
        set_request_for_user(initial_users["user_1"])

        project = Project.objects.create(
            name="Tus upload failure project",
        )
        dataset = UploadsDataset.objects.create(
            name="Tus upload failure dataset",
            folder=project.folders.first(),
        )
        dataset.lock()
        assert dataset.locked is True

        upload = Upload.objects.create(
            dataset=dataset,
            upload_metadata={
                "filename": "tus-upload.txt",
                "filepath": "/tmp/tus-upload.txt",
            },
            uploaded_file=sample_file,
        )

        def mock_publish(*args, **kwargs):
            raise RuntimeError("Forced publish failure")

        monkeypatch.setattr(UploadsDataset, "publish", mock_publish)

        with caplog.at_level(logging.ERROR):
            save_finished_tus_upload_as_uploads_version_file(
                sender=Upload,
                instance=upload,
            )

        dataset.refresh_from_db()
        assert dataset.status == UploadsDataset.Status.ERROR
        assert dataset.locked is False
        assert not Upload.objects.filter(pk=upload.pk).exists()
        assert any("Failed to complete file upload" in record.getMessage() for record in caplog.records)
