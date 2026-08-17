import pytest
from conftest import auth_user
from django.urls import reverse
from rest_framework import status

from fdm.core.helpers import get_content_type_for_object, set_request_for_user
from fdm.file_parser.models import FileParser, FileParserConfiguration
from fdm.folders.models import Folder
from fdm.projects.models import Project
from fdm.uploads.models import UploadsDataset


@pytest.mark.django_db
class TestFileParserConfigurationAPI:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.project = Project.objects.create(
            name="Project 1",
        )

    def _config(self):
        return [
            {
                "row": 0,
                "column": 4,
                "key": "MY_CUSTOM_KEY",
            },
            {
                "row": 3,
                "column": 9,
                "key": {
                    "row": 3,
                    "column": 8,
                },
            },
        ]

    def _payload(self, assigned_to_content_object, config=None):
        return {
            "parser_type": FileParser.Type.CSV_VALUE,
            "assigned_to_content_type": get_content_type_for_object(assigned_to_content_object.get_content_type()),
            "assigned_to_object_id": assigned_to_content_object.pk,
            "config": self._config() if config is None else config,
        }

    def test_available_types(self, client):
        """
        Ensure globally available parser types can be discovered.
        """
        url = reverse("file-parser-configuration-available-types")

        response = client.get(url, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data[0]["parser_type"] == FileParser.Type.CSV_VALUE
        assert response.data[0]["config_schema"]["type"] == "array"
        assert response.data[0]["config_schema"]["items"]["required"] == [
            "row",
            "column",
            "key",
        ]
        assert response.data[0]["config_schema"]["items"]["properties"]["key"]["oneOf"][1]["required"] == [
            "row",
            "column",
        ]

    def test_create_csv_parser_configuration(self, client):
        """
        Ensure an authorized user can create a scoped CSV parser assignment with extraction rules.
        """
        url = reverse("file-parser-configuration-list")

        response = client.post(
            url,
            self._payload(self.project),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["config"] == [
            {
                "row": 0,
                "column": 4,
                "key": "MY_CUSTOM_KEY",
            },
            {
                "row": 3,
                "column": 9,
                "key": {
                    "row": 3,
                    "column": 8,
                },
            },
        ]
        assert FileParserConfiguration.objects.get(pk=response.data["pk"]).output_metadata_key is None

    def test_create_csv_parser_configuration_for_folder_scope(self, client):
        """
        Ensure an authorized folder admin can create a folder-scoped CSV parser assignment.
        """
        url = reverse("file-parser-configuration-list")
        folder = self.project.folder.first()

        response = client.post(
            url,
            self._payload(folder),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["assigned_to_object_id"] == str(folder.pk)

    def test_create_csv_parser_configuration_for_dataset_scope(self, client):
        """
        Ensure an authorized dataset creator can create a dataset-scoped CSV parser assignment.
        """
        url = reverse("file-parser-configuration-list")
        dataset = UploadsDataset.objects.create(name="Dataset 1")

        response = client.post(
            url,
            self._payload(dataset),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["assigned_to_object_id"] == str(dataset.pk)

    def test_list_csv_parser_configurations_can_be_filtered_by_scope_and_state(self, client):
        """
        Ensure parser assignments can be filtered through the API-facing content type strings.
        """
        folder = self.project.folder.first()
        dataset = UploadsDataset.objects.create(name="Dataset 1", folder=folder)
        project_config = FileParserConfiguration.objects.create(
            parser_type=FileParser.Type.CSV_VALUE,
            assigned_to_content_type=self.project.get_content_type(),
            assigned_to_object_id=self.project.pk,
            config=self._config(),
        )
        folder_config = FileParserConfiguration.objects.create(
            parser_type=FileParser.Type.CSV_VALUE,
            assigned_to_content_type=folder.get_content_type(),
            assigned_to_object_id=folder.pk,
            enabled=False,
            config=self._config(),
        )
        dataset_config = FileParserConfiguration.objects.create(
            parser_type=FileParser.Type.CSV_VALUE,
            assigned_to_content_type=dataset.get_content_type(),
            assigned_to_object_id=dataset.pk,
            config=self._config(),
        )
        url = reverse("file-parser-configuration-list")

        def result_ids(query):
            response = client.get(f"{url}?{query}", format="json")
            assert response.status_code == status.HTTP_200_OK
            return {item["pk"] for item in response.data["results"]}

        project_content_type = get_content_type_for_object(self.project.get_content_type())

        assert result_ids(f"assigned_to_content_type={project_content_type}") == {
            str(project_config.pk),
        }
        assert result_ids(f"assigned_to_content_type={get_content_type_for_object(folder.get_content_type())}") == {
            str(folder_config.pk),
        }
        assert result_ids(f"assigned_to_content_type={get_content_type_for_object(dataset.get_content_type())}") == {
            str(dataset_config.pk),
        }
        assert result_ids(f"assigned_to_object_id={dataset.pk}") == {
            str(dataset_config.pk),
        }
        assert result_ids("enabled=true") == {
            str(project_config.pk),
            str(dataset_config.pk),
        }
        assert result_ids(f"parser_type={FileParser.Type.CSV_VALUE}") == {
            str(project_config.pk),
            str(folder_config.pk),
            str(dataset_config.pk),
        }

    @pytest.mark.parametrize(
        "config",
        [
            {
                "mode": "coordinates",
                "row_index": 0,
            },
            [],
            [
                {
                    "column": 0,
                    "key": "SAMPLE_ID",
                },
            ],
            [
                {
                    "row": -1,
                    "column": 0,
                    "key": "SAMPLE_ID",
                },
            ],
            [
                {
                    "row": 0,
                    "column": 0,
                    "key": "",
                },
            ],
            [
                {
                    "row": 0,
                    "column": 0,
                    "key": {
                        "row": 1,
                    },
                },
            ],
        ],
    )
    def test_create_csv_parser_configuration_rejects_invalid_config(self, client, config):
        """
        Ensure CSV extraction rule config is validated through the API.
        """
        url = reverse("file-parser-configuration-list")

        response = client.post(
            url,
            self._payload(self.project, config=config),
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_csv_parser_configuration_requires_scope_permission(self, client, initial_users):
        """
        Ensure users cannot manage parser configs outside their project scope.
        """
        auth_user(client, initial_users["user_2"])
        url = reverse("file-parser-configuration-list")

        response = client.post(
            url,
            self._payload(self.project),
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_update_csv_parser_configuration_requires_scope_permission(self, client, initial_users):
        """
        Ensure users cannot update parser assignments outside their project scope.
        """
        url = reverse("file-parser-configuration-list")
        create_response = client.post(
            url,
            self._payload(self.project),
            format="json",
        )
        assert create_response.status_code == status.HTTP_201_CREATED

        auth_user(client, initial_users["user_2"])
        detail_url = reverse("file-parser-configuration-detail", kwargs={"pk": create_response.data["pk"]})
        response = client.patch(
            detail_url,
            {
                "enabled": False,
            },
            format="json",
        )

        assert response.status_code in [
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        ]

    def test_delete_csv_parser_configuration_requires_scope_permission(self, client, initial_users):
        """
        Ensure users cannot delete parser assignments outside their project scope.
        """
        url = reverse("file-parser-configuration-list")
        create_response = client.post(
            url,
            self._payload(self.project),
            format="json",
        )
        assert create_response.status_code == status.HTTP_201_CREATED

        auth_user(client, initial_users["user_2"])
        detail_url = reverse("file-parser-configuration-detail", kwargs={"pk": create_response.data["pk"]})
        response = client.delete(detail_url)

        assert response.status_code in [
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        ]
