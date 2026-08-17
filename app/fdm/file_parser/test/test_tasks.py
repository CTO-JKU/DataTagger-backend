import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from fdm.core.helpers import set_request_for_user
from fdm.file_parser.models import FileParser, FileParserConfiguration
from fdm.file_parser.tasks import check_configured_file_parsers, run_configured_file_parsers
from fdm.metadata.helpers import set_metadata
from fdm.metadata.models import Metadata
from fdm.projects.models import Project
from fdm.uploads.models import UploadsDataset, UploadsVersion, UploadsVersionFile


@pytest.mark.django_db
class TestConfiguredFileParserTasks:
    def test_csv_parser_task_preserves_duplicate_metadata_key_configs(self, initial_users):
        """
        Ensure configured parser scheduling keeps duplicate output keys from every applicable scope.
        """
        set_request_for_user(initial_users["user_1"])

        project = Project.objects.create(
            name="Project 1",
        )
        folder = project.folder.first()
        dataset = UploadsDataset.objects.create(
            name="Dataset 1",
            folder=folder,
        )
        version_file = UploadsVersionFile.objects.create(
            uploaded_file=SimpleUploadedFile(
                "sample.csv",
                b"Project,Folder,Dataset\nproject-value,folder-value,dataset-value\n",
                content_type="text/csv",
            ),
        )
        UploadsVersion.objects.create(
            dataset=dataset,
            version_file=version_file,
        )
        set_metadata(
            assigned_to_content_type=version_file.get_content_type(),
            assigned_to_object_id=version_file.pk,
            custom_key="MIME_TYPE",
            value="text/csv",
            read_only=True,
        )

        FileParserConfiguration.objects.create(
            parser_type=FileParser.Type.CSV_VALUE,
            assigned_to_content_type=project.get_content_type(),
            assigned_to_object_id=project.pk,
            config=[{"row": 1, "column": 0, "key": "shared_key"}],
        )
        FileParserConfiguration.objects.create(
            parser_type=FileParser.Type.CSV_VALUE,
            assigned_to_content_type=folder.get_content_type(),
            assigned_to_object_id=folder.pk,
            config=[{"row": 1, "column": 1, "key": "shared_key"}],
        )
        FileParserConfiguration.objects.create(
            parser_type=FileParser.Type.CSV_VALUE,
            assigned_to_content_type=dataset.get_content_type(),
            assigned_to_object_id=dataset.pk,
            config=[{"row": 1, "column": 2, "key": "shared_key"}],
        )

        check_configured_file_parsers(version_file)

        tasks = FileParser.objects.filter(
            file=version_file,
            parser_type=FileParser.Type.CSV_VALUE,
        )
        assert tasks.count() == 3

        run_configured_file_parsers()

        assert list(tasks.order_by("creation_date").values_list("status", flat=True)) == [
            FileParser.Status.FINISHED,
            FileParser.Status.FINISHED,
            FileParser.Status.FINISHED,
        ]

        metadata = Metadata.objects.filter(
            assigned_to_content_type=version_file.get_content_type(),
            assigned_to_object_id=version_file.pk,
            custom_key="SHARED_KEY",
        ).order_by("creation_date")
        assert [item.get_value() for item in metadata] == [
            "project-value",
            "folder-value",
            "dataset-value",
        ]
        assert all(item.read_only is True for item in metadata)
