import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from fdm.core.helpers import set_request_for_user
from fdm.folders.models import FolderPermission
from fdm.metadata.enums import MetadataFieldType
from fdm.metadata.helpers import set_metadata
from fdm.metadata.models import MetadataTemplate, MetadataTemplateContainer, MetadataTemplateField
from fdm.projects.models import Project, ProjectMembership
from fdm.uploads.models import UploadsDataset, UploadsVersion, UploadsVersionFile

sample_file = SimpleUploadedFile(
    "report-buechler.pdf",
    b"test",
    content_type="application/pdf",
)


@pytest.mark.django_db
class TestSearchAPI:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        self.user_1 = initial_users["user_1"]
        self.user_2 = initial_users["user_2"]
        set_request_for_user(self.user_1)

        self.project_admin = Project.objects.create(
            name="Horizon 2.5 Project",
            description={"text": "Horizon 2.5 description"},
        )
        self.admin_folder = self.project_admin.folders.first()
        self.admin_folder.name = "Research Folder"
        self.admin_folder.description = {"text": "Research folder description"}
        self.admin_folder.save()

        set_metadata(
            assigned_to_content_type=self.project_admin.get_content_type(),
            assigned_to_object_id=self.project_admin.pk,
            custom_key="Author",
            value="alice",
        )
        set_metadata(
            assigned_to_content_type=self.project_admin.get_content_type(),
            assigned_to_object_id=self.project_admin.pk,
            custom_key="Team",
            value="nbuechler",
        )

        self.dataset = UploadsDataset.objects.create(
            name="Experiment Dataset",
            folder=self.admin_folder,
        )
        self.version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )
        self.version = UploadsVersion.objects.create(
            name="Results Version",
            dataset=self.dataset,
            version_file=self.version_file,
            status=UploadsVersion.Status.FINISHED,
            metadata_is_complete=True,
            publication_date=timezone.now(),
        )
        self.version_file.status = UploadsVersionFile.Status.FINISHED
        self.version_file.publication_date = timezone.now()
        self.version_file.save()
        self.dataset.status = UploadsDataset.Status.FINISHED
        self.dataset.publication_date = timezone.now()
        self.dataset.expiry_date = timezone.now() + timezone.timedelta(days=14)
        self.dataset.save()
        set_metadata(
            assigned_to_content_type=self.version.get_content_type(),
            assigned_to_object_id=self.version.pk,
            custom_key="Author",
            value="nbuechler",
        )
        set_metadata(
            assigned_to_content_type=self.version.get_content_type(),
            assigned_to_object_id=self.version.pk,
            custom_key="SampleCount",
            value="150",
            field_type=MetadataFieldType.INTEGER,
        )
        set_metadata(
            assigned_to_content_type=self.version_file.get_content_type(),
            assigned_to_object_id=self.version_file.pk,
            custom_key="DocumentType",
            value="report",
        )
        self.version_template_container = MetadataTemplateContainer.objects.create(
            name="Global Version Search Template",
        )
        self.version_metadata_template = MetadataTemplate.objects.create(
            metadata_template_container=self.version_template_container,
            name="Global Version Search Template",
        )
        self.version_template_field = MetadataTemplateField.objects.create(
            metadata_template=self.version_metadata_template,
            custom_key="integer",
            field_type=MetadataFieldType.INTEGER,
            value={"value": "1"},
        )
        self.version.metadata_templates.add(self.version_metadata_template)
        self.version_template_metadata = set_metadata(
            assigned_to_content_type=self.version.get_content_type(),
            assigned_to_object_id=self.version.pk,
            custom_key="integer",
            value="1",
            field_type=MetadataFieldType.INTEGER,
            metadata_template_field=self.version_template_field,
        )
        self.project_template_container = MetadataTemplateContainer.objects.create(
            name="Project Search Template",
            assigned_to_content_type=self.project_admin.get_content_type(),
            assigned_to_object_id=self.project_admin.pk,
        )
        self.project_template = MetadataTemplate.objects.create(
            metadata_template_container=self.project_template_container,
            name="Project Search Template",
        )
        MetadataTemplateField.objects.create(
            metadata_template=self.project_template,
            custom_key="project_text",
            field_type=MetadataFieldType.TEXT,
            value={"value": "project-default"},
        )
        self.folder_template_container = MetadataTemplateContainer.objects.create(
            name="Folder Search Template",
            assigned_to_content_type=self.admin_folder.get_content_type(),
            assigned_to_object_id=self.admin_folder.pk,
        )
        self.folder_template = MetadataTemplate.objects.create(
            metadata_template_container=self.folder_template_container,
            name="Folder Search Template v1",
        )
        MetadataTemplateField.objects.create(
            metadata_template=self.folder_template,
            custom_key="folder_integer",
            field_type=MetadataFieldType.INTEGER,
            value={"value": "2"},
        )
        self.folder_template_latest = MetadataTemplate.objects.create(
            metadata_template_container=self.folder_template_container,
            name="Folder Search Template v2",
        )
        MetadataTemplateField.objects.create(
            metadata_template=self.folder_template_latest,
            custom_key="folder_text",
            field_type=MetadataFieldType.TEXT,
            value={"value": "latest-text"},
        )
        self.dataset_template_container = MetadataTemplateContainer.objects.create(
            name="Dataset Search Template",
            assigned_to_content_type=self.dataset.get_content_type(),
            assigned_to_object_id=self.dataset.pk,
        )
        self.dataset_template = MetadataTemplate.objects.create(
            metadata_template_container=self.dataset_template_container,
            name="Dataset Search Template",
        )
        MetadataTemplateField.objects.create(
            metadata_template=self.dataset_template,
            custom_key="dataset_date",
            field_type=MetadataFieldType.DATE,
            value={"value": "2026-03-10"},
        )
        self.unpublished_dataset = UploadsDataset.objects.create(
            name="Draft Dataset",
            folder=self.admin_folder,
            status=UploadsDataset.Status.ERROR,
        )
        self.version_without_file = UploadsVersion.objects.create(
            name="Draft Version",
            dataset=self.unpublished_dataset,
            status=UploadsVersion.Status.ERROR,
            metadata_is_complete=False,
        )
        self.history_dataset = UploadsDataset.objects.create(
            name="History Dataset",
            folder=self.admin_folder,
            status=UploadsDataset.Status.FINISHED,
        )
        self.history_old_version_file = UploadsVersionFile.objects.create(
            uploaded_file=SimpleUploadedFile(
                "legacy-history-match.csv",
                b"legacy",
                content_type="text/csv",
            ),
            status=UploadsVersionFile.Status.FINISHED,
        )
        self.history_old_version = UploadsVersion.objects.create(
            name="Legacy History Match",
            dataset=self.history_dataset,
            version_file=self.history_old_version_file,
            status=UploadsVersion.Status.FINISHED,
            metadata_is_complete=True,
        )
        self.history_template_container = MetadataTemplateContainer.objects.create(
            name="History Dataset Template",
        )
        self.history_template = MetadataTemplate.objects.create(
            metadata_template_container=self.history_template_container,
            name="History Dataset Template v1",
        )
        self.history_template_field = MetadataTemplateField.objects.create(
            metadata_template=self.history_template,
            custom_key="legacy_integer",
            field_type=MetadataFieldType.INTEGER,
            value={"value": "9"},
        )
        self.history_old_version.metadata_templates.add(self.history_template)
        set_metadata(
            assigned_to_content_type=self.history_old_version.get_content_type(),
            assigned_to_object_id=self.history_old_version.pk,
            custom_key="legacy_integer",
            value="9",
            field_type=MetadataFieldType.INTEGER,
            metadata_template_field=self.history_template_field,
        )
        self.history_latest_version = UploadsVersion.objects.create(
            name="Current History Version",
            dataset=self.history_dataset,
            status=UploadsVersion.Status.FINISHED,
            metadata_is_complete=True,
        )
        set_metadata(
            assigned_to_content_type=self.history_latest_version.get_content_type(),
            assigned_to_object_id=self.history_latest_version.pk,
            custom_key="current_key",
            value="current-value",
        )

        self.project_metadata_match = Project.objects.create(
            name="Correlation Match",
        )
        set_metadata(
            assigned_to_content_type=self.project_metadata_match.get_content_type(),
            assigned_to_object_id=self.project_metadata_match.pk,
            custom_key="Author",
            value="nbuechler",
        )

        set_request_for_user(self.user_2)
        self.shared_member_project = Project.objects.create(
            name="Shared Member Project",
        )
        self.shared_member_folder = self.shared_member_project.folders.first()
        shared_member_membership = ProjectMembership.objects.create(
            project=self.shared_member_project,
            member=self.user_1,
            is_project_admin=False,
            can_create_folders=False,
            is_metadata_template_admin=False,
        )
        FolderPermission.objects.create(
            folder=self.shared_member_folder,
            project_membership=shared_member_membership,
            is_folder_admin=False,
            is_metadata_template_admin=False,
            can_edit=False,
        )

        self.shared_editor_project = Project.objects.create(
            name="Editor Project",
        )
        self.shared_editor_folder = self.shared_editor_project.folders.first()
        shared_editor_membership = ProjectMembership.objects.create(
            project=self.shared_editor_project,
            member=self.user_1,
            is_project_admin=False,
            can_create_folders=False,
            is_metadata_template_admin=False,
        )
        FolderPermission.objects.create(
            folder=self.shared_editor_folder,
            project_membership=shared_editor_membership,
            is_folder_admin=False,
            is_metadata_template_admin=False,
            can_edit=True,
        )

        self.hidden_project = Project.objects.create(
            name="Hidden Secret Project",
        )
        self.hidden_template_container = MetadataTemplateContainer.objects.create(
            name="Hidden Search Template",
            assigned_to_content_type=self.hidden_project.get_content_type(),
            assigned_to_object_id=self.hidden_project.pk,
        )
        self.hidden_template = MetadataTemplate.objects.create(
            metadata_template_container=self.hidden_template_container,
            name="Hidden Search Template",
        )
        MetadataTemplateField.objects.create(
            metadata_template=self.hidden_template,
            custom_key="hidden_integer",
            field_type=MetadataFieldType.INTEGER,
            value={"value": "7"},
        )

        set_request_for_user(self.user_1)

    def test_requires_search_text_or_group(self, client):
        url = reverse("search-global")

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

        response = client.post(url, {}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Either search_text or group is required." in str(response.data)

    def test_single_condition_group_defaults_operator_to_and(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": [
                    "dataset_version",
                ],
                "group": {
                    "conditions": [
                        {
                            "field": "metadata.key",
                            "operator": "is",
                            "value": "integer",
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["item"]["name"] == self.version.name

    def test_multi_condition_group_requires_operator(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": [
                    "dataset_version",
                ],
                "group": {
                    "conditions": [
                        {
                            "field": "metadata.key",
                            "operator": "is",
                            "value": "integer",
                        },
                        {
                            "field": "metadata.value",
                            "operator": "is",
                            "value": 1,
                            "field_type": MetadataFieldType.INTEGER,
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Group operator is required when a condition group contains more than one condition." in str(
            response.data,
        )

    def test_search_text_returns_unified_results(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "search_text": "buechler",
                "result_types": [
                    "project",
                    "dataset_version",
                    "file",
                ],
                "limit": 10,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] >= 3
        assert {result["result_type"] for result in response.data["results"]} >= {
            "project",
            "dataset_version",
            "file",
        }
        file_result = next(result for result in response.data["results"] if result["result_type"] == "file")
        assert file_result["item"]["name"].startswith("report-buechler")
        assert file_result["item"]["name"].endswith(".pdf")

    def test_scope_filters_and_compact_mode(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": [
                    "dataset_version",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "dataset_id",
                            "operator": "is",
                            "value": str(self.dataset.pk),
                        },
                    ],
                },
                "response_mode": "compact",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        item = response.data["results"][0]["item"]
        assert item["pk"] == str(self.version.pk)
        assert item["dataset"]["pk"] == str(self.dataset.pk)
        assert "creation_date" not in item

    def test_compact_mode_omits_operational_status(self, client):
        url = reverse("search-global")

        cases = [
            (
                "dataset",
                {
                    "field": "dataset_id",
                    "operator": "is",
                    "value": str(self.dataset.pk),
                },
            ),
            (
                "dataset_version",
                {
                    "field": "dataset_version_id",
                    "operator": "is",
                    "value": str(self.version.pk),
                },
            ),
            (
                "file",
                {
                    "field": "dataset_version_id",
                    "operator": "is",
                    "value": str(self.version.pk),
                },
            ),
        ]

        for result_type, rule in cases:
            response = client.post(
                url,
                {
                    "result_types": [result_type],
                    "group": {
                        "operator": "AND",
                        "conditions": [rule],
                    },
                    "response_mode": "compact",
                },
                format="json",
            )

            assert response.status_code == status.HTTP_200_OK
            assert response.data["count"] == 1
            item = response.data["results"][0]["item"]
            assert "status" not in item

        dataset_item = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "dataset_id",
                            "operator": "is",
                            "value": str(self.dataset.pk),
                        },
                    ],
                },
                "response_mode": "compact",
            },
            format="json",
        ).data["results"][0]["item"]
        assert dataset_item["metadata_is_complete"] is True
        assert dataset_item["has_upgradeable_metadata_templates"] is False

    def test_operational_state_filters(self, client):
        url = reverse("search-global")

        published_dataset_response = client.post(
            url,
            {
                "result_types": [
                    "dataset",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "is_published",
                            "operator": "is",
                            "value": True,
                        },
                    ],
                },
            },
            format="json",
        )

        assert published_dataset_response.status_code == status.HTTP_200_OK
        assert {result["item"]["pk"] for result in published_dataset_response.data["results"]} == {
            str(self.dataset.pk),
        }

        missing_file_response = client.post(
            url,
            {
                "result_types": [
                    "dataset_version",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "has_file",
                            "operator": "is",
                            "value": False,
                        },
                    ],
                },
            },
            format="json",
        )

        assert missing_file_response.status_code == status.HTTP_200_OK
        assert {result["item"]["pk"] for result in missing_file_response.data["results"]} == {
            str(self.version_without_file.pk),
            str(self.history_latest_version.pk),
        }

        incomplete_metadata_response = client.post(
            url,
            {
                "result_types": [
                    "dataset_version",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata_is_complete",
                            "operator": "is",
                            "value": False,
                        },
                    ],
                },
            },
            format="json",
        )

        assert incomplete_metadata_response.status_code == status.HTTP_200_OK
        assert {result["item"]["pk"] for result in incomplete_metadata_response.data["results"]} == {
            str(self.version_without_file.pk),
        }

    def test_dataset_metadata_health_filters_combine_with_and(self, client):
        url = reverse("search-global")

        # Two datasets need attention: incomplete latest version AND outdated template.
        self.history_latest_version.metadata_is_complete = False
        self.history_latest_version.save(update_fields=["metadata_is_complete"])
        self.history_dataset.has_upgradeable_metadata_templates = True
        self.history_dataset.save(update_fields=["has_upgradeable_metadata_templates"])

        # Distractor 1: incomplete but template is fresh.
        # unpublished_dataset already has incomplete latest version and default has_upgradeable=False.

        # Distractor 2: outdated template but complete metadata.
        self.dataset.has_upgradeable_metadata_templates = True
        self.dataset.save(update_fields=["has_upgradeable_metadata_templates"])
        # self.dataset's latest version stays metadata_is_complete=True.

        response = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata_is_complete",
                            "operator": "is",
                            "value": False,
                        },
                        {
                            "field": "has_upgradeable_metadata_templates",
                            "operator": "is",
                            "value": True,
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert {result["item"]["pk"] for result in response.data["results"]} == {
            str(self.history_dataset.pk),
        }

    def test_dataset_with_no_versions_is_excluded_from_metadata_is_complete_filter(self, client):
        url = reverse("search-global")

        empty_dataset = UploadsDataset.objects.create(
            name="Empty Dataset",
            folder=self.admin_folder,
        )

        for value in (True, False):
            response = client.post(
                url,
                {
                    "result_types": ["dataset"],
                    "group": {
                        "operator": "AND",
                        "conditions": [
                            {
                                "field": "metadata_is_complete",
                                "operator": "is",
                                "value": value,
                            },
                        ],
                    },
                },
                format="json",
            )

            assert response.status_code == status.HTTP_200_OK
            pks = {result["item"]["pk"] for result in response.data["results"]}
            assert str(empty_dataset.pk) not in pks, f"empty dataset must not match `metadata_is_complete = {value}`"

    def test_dataset_metadata_is_complete_uses_latest_version_only(self, client):
        url = reverse("search-global")

        # self.dataset: single version, metadata_is_complete=True (latest = complete)
        # self.history_dataset: two versions, both complete by default in fixture.
        # Make the latest incomplete and the older one complete to prove the cascade.
        self.history_latest_version.metadata_is_complete = False
        self.history_latest_version.save(update_fields=["metadata_is_complete"])
        # self.unpublished_dataset: single version (version_without_file) with metadata_is_complete=False.
        # self.history_old_version stays metadata_is_complete=True.

        complete_response = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata_is_complete",
                            "operator": "is",
                            "value": True,
                        },
                    ],
                },
            },
            format="json",
        )

        assert complete_response.status_code == status.HTTP_200_OK
        complete_pks = {result["item"]["pk"] for result in complete_response.data["results"]}
        assert str(self.dataset.pk) in complete_pks
        assert str(self.history_dataset.pk) not in complete_pks, (
            "history_dataset's latest version is incomplete; the dataset should not appear under is=true"
        )
        assert str(self.unpublished_dataset.pk) not in complete_pks

        incomplete_response = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata_is_complete",
                            "operator": "is",
                            "value": False,
                        },
                    ],
                },
            },
            format="json",
        )

        assert incomplete_response.status_code == status.HTTP_200_OK
        incomplete_pks = {result["item"]["pk"] for result in incomplete_response.data["results"]}
        assert str(self.history_dataset.pk) in incomplete_pks
        assert str(self.unpublished_dataset.pk) in incomplete_pks
        assert str(self.dataset.pk) not in incomplete_pks

    def test_dataset_metadata_is_complete_is_not_includes_versionless_dataset(self, client):
        url = reverse("search-global")

        empty_dataset = UploadsDataset.objects.create(
            name="Empty Dataset",
            folder=self.admin_folder,
        )
        # Make history_dataset's latest version incomplete so we have a definite
        # `False` row alongside the versionless dataset's NULL row.
        self.history_latest_version.metadata_is_complete = False
        self.history_latest_version.save(update_fields=["metadata_is_complete"])

        # `is_not True` should match: dataset (complement of incomplete) AND NULL.
        # Concretely: every dataset whose latest version is incomplete OR has no versions.
        is_not_true_response = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata_is_complete",
                            "operator": "is_not",
                            "value": True,
                        },
                    ],
                },
            },
            format="json",
        )

        assert is_not_true_response.status_code == status.HTTP_200_OK
        is_not_true_pks = {result["item"]["pk"] for result in is_not_true_response.data["results"]}
        assert str(empty_dataset.pk) in is_not_true_pks, (
            "versionless datasets must appear in `is_not true` (three-valued logic)"
        )
        assert str(self.history_dataset.pk) in is_not_true_pks
        assert str(self.unpublished_dataset.pk) in is_not_true_pks
        assert str(self.dataset.pk) not in is_not_true_pks

        # `is_not False` should match: complete-latest datasets AND NULL.
        is_not_false_response = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata_is_complete",
                            "operator": "is_not",
                            "value": False,
                        },
                    ],
                },
            },
            format="json",
        )

        assert is_not_false_response.status_code == status.HTTP_200_OK
        is_not_false_pks = {result["item"]["pk"] for result in is_not_false_response.data["results"]}
        assert str(empty_dataset.pk) in is_not_false_pks, (
            "versionless datasets must appear in `is_not false` (three-valued logic)"
        )
        assert str(self.dataset.pk) in is_not_false_pks
        assert str(self.history_dataset.pk) not in is_not_false_pks
        assert str(self.unpublished_dataset.pk) not in is_not_false_pks

    def test_dataset_version_metadata_is_complete_is_not(self, client):
        url = reverse("search-global")

        # version_without_file is metadata_is_complete=False; the rest default to True.
        response = client.post(
            url,
            {
                "result_types": ["dataset_version"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata_is_complete",
                            "operator": "is_not",
                            "value": True,
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        pks = {result["item"]["pk"] for result in response.data["results"]}
        assert str(self.version_without_file.pk) in pks
        assert str(self.history_latest_version.pk) not in pks
        assert str(self.history_old_version.pk) not in pks

    def test_has_upgradeable_metadata_templates_filter_matches_cached_flag(self, client):
        url = reverse("search-global")

        self.dataset.has_upgradeable_metadata_templates = True
        self.dataset.save(update_fields=["has_upgradeable_metadata_templates"])
        # self.history_dataset and self.unpublished_dataset keep the default False.

        upgradeable_response = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "has_upgradeable_metadata_templates",
                            "operator": "is",
                            "value": True,
                        },
                    ],
                },
            },
            format="json",
        )

        assert upgradeable_response.status_code == status.HTTP_200_OK
        assert {result["item"]["pk"] for result in upgradeable_response.data["results"]} == {
            str(self.dataset.pk),
        }

        not_upgradeable_response = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "has_upgradeable_metadata_templates",
                            "operator": "is",
                            "value": False,
                        },
                    ],
                },
            },
            format="json",
        )

        assert not_upgradeable_response.status_code == status.HTTP_200_OK
        returned_pks = {result["item"]["pk"] for result in not_upgradeable_response.data["results"]}
        assert str(self.dataset.pk) not in returned_pks
        assert str(self.history_dataset.pk) in returned_pks
        assert str(self.unpublished_dataset.pk) in returned_pks

    def test_has_upgradeable_metadata_templates_rejects_non_boolean_value(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "has_upgradeable_metadata_templates",
                            "operator": "is",
                            "value": "yes",
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_metadata_is_complete_now_available_on_dataset_in_schema(self, client):
        url = reverse("search-global-schema")

        response = client.get(url, format="json")

        assert response.status_code == status.HTTP_200_OK
        types = response.data["types"]

        dataset_fields = {f["id"]: f for f in types["dataset"]["fields"]}
        dv_fields = {f["id"]: f for f in types["dataset_version"]["fields"]}

        assert "metadata_is_complete" in dataset_fields
        assert dataset_fields["metadata_is_complete"]["value_type"] == "boolean"
        assert "metadata_is_complete" in dv_fields

        for other_type in ("file", "project", "folder", "template", "template_version"):
            other_field_ids = {f["id"] for f in types[other_type]["fields"]}
            assert "metadata_is_complete" not in other_field_ids

    def test_has_upgradeable_metadata_templates_in_schema_endpoint(self, client):
        url = reverse("search-global-schema")

        response = client.get(url, format="json")

        assert response.status_code == status.HTTP_200_OK
        types = response.data["types"]

        dataset_fields = {f["id"]: f for f in types["dataset"]["fields"]}
        assert "has_upgradeable_metadata_templates" in dataset_fields
        assert dataset_fields["has_upgradeable_metadata_templates"]["value_type"] == "boolean"
        assert sorted(dataset_fields["has_upgradeable_metadata_templates"]["operators"]) == ["is", "is_not"]

        for other_type in ("dataset_version", "file", "project", "folder", "template", "template_version"):
            other_fields = {f["id"] for f in types[other_type]["fields"]}
            assert "has_upgradeable_metadata_templates" not in other_fields, (
                f"field should not appear under {other_type}"
            )

    @pytest.mark.parametrize("result_type", ["dataset_version", "file", "project", "folder"])
    def test_has_upgradeable_metadata_templates_rejected_outside_dataset(self, client, result_type):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": [result_type],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "has_upgradeable_metadata_templates",
                            "operator": "is",
                            "value": True,
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.parametrize(
        "result_type,field_name",
        [
            ("dataset", "status"),
            ("dataset_version", "status"),
            ("file", "status"),
            ("dataset", "metadata_upgrade_status"),
            ("dataset_version", "metadata_upgrade_status"),
        ],
    )
    def test_operational_status_fields_are_not_searchable(self, client, result_type, field_name):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": [result_type],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": field_name,
                            "operator": "is",
                            "value": UploadsDataset.Status.FINISHED,
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert f"Unsupported field: {field_name}" in str(response.data)

    def test_metadata_source_filter(self, client):
        url = reverse("search-global")

        template_response = client.post(
            url,
            {
                "result_types": [
                    "dataset_version",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.key",
                            "operator": "is",
                            "value": "integer",
                        },
                        {
                            "field": "metadata.source",
                            "operator": "is",
                            "value": "template",
                        },
                    ],
                },
            },
            format="json",
        )

        assert template_response.status_code == status.HTTP_200_OK
        assert {result["item"]["pk"] for result in template_response.data["results"]} == {
            str(self.version.pk),
        }

        template_materialized_response = client.post(
            url,
            {
                "result_types": [
                    "dataset_version",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.key",
                            "operator": "is",
                            "value": "integer",
                        },
                        {
                            "field": "metadata.source",
                            "operator": "is",
                            "value": "instance",
                        },
                    ],
                },
            },
            format="json",
        )

        assert template_materialized_response.status_code == status.HTTP_200_OK
        assert template_materialized_response.data["results"] == []

        instance_response = client.post(
            url,
            {
                "result_types": [
                    "dataset_version",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.key",
                            "operator": "is",
                            "value": "SampleCount",
                        },
                        {
                            "field": "metadata.source",
                            "operator": "is",
                            "value": "instance",
                        },
                    ],
                },
            },
            format="json",
        )

        assert instance_response.status_code == status.HTTP_200_OK
        assert {result["item"]["pk"] for result in instance_response.data["results"]} == {
            str(self.version.pk),
        }

        dataset_template_response = client.post(
            url,
            {
                "result_types": [
                    "dataset",
                ],
                "search_text": "integer",
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.source",
                            "operator": "is",
                            "value": "template",
                        },
                    ],
                },
            },
            format="json",
        )

        assert dataset_template_response.status_code == status.HTTP_200_OK
        assert {result["item"]["pk"] for result in dataset_template_response.data["results"]} == {
            str(self.dataset.pk),
        }

        dataset_instance_response = client.post(
            url,
            {
                "result_types": [
                    "dataset",
                ],
                "search_text": "integer",
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.source",
                            "operator": "is",
                            "value": "instance",
                        },
                    ],
                },
            },
            format="json",
        )

        assert dataset_instance_response.status_code == status.HTTP_200_OK
        assert dataset_instance_response.data["results"] == []

    def test_metadata_source_constrains_search_text_metadata_matches(self, client):
        url = reverse("search-global")

        template_response = client.post(
            url,
            {
                "result_types": [
                    "dataset_version",
                ],
                "search_text": "integer",
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.source",
                            "operator": "is",
                            "value": "template",
                        },
                    ],
                },
            },
            format="json",
        )

        assert template_response.status_code == status.HTTP_200_OK
        assert {result["item"]["pk"] for result in template_response.data["results"]} == {
            str(self.version.pk),
            str(self.history_old_version.pk),
        }

        instance_response = client.post(
            url,
            {
                "result_types": [
                    "dataset_version",
                ],
                "search_text": "integer",
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.source",
                            "operator": "is",
                            "value": "instance",
                        },
                    ],
                },
            },
            format="json",
        )

        assert instance_response.status_code == status.HTTP_200_OK
        assert instance_response.data["results"] == []

    def test_metadata_source_does_not_block_non_metadata_broad_matches(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": [
                    "dataset_version",
                ],
                "search_text": self.version.name,
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.source",
                            "operator": "is",
                            "value": "template",
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert {result["item"]["pk"] for result in response.data["results"]} == {
            str(self.version.pk),
        }

    def test_metadata_source_with_mixed_result_types_only_returns_dataset_versions(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": [
                    "project",
                    "folder",
                    "dataset",
                    "dataset_version",
                    "file",
                    "template",
                    "template_version",
                ],
                "search_text": "integer",
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.source",
                            "operator": "is",
                            "value": "template",
                        },
                    ],
                },
                "response_mode": "compact",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert {result["result_type"] for result in response.data["results"]} == {
            "dataset",
            "dataset_version",
        }

    def test_dataset_search_uses_latest_version_only(self, client):
        url = reverse("search-global")

        dataset_response = client.post(
            url,
            {
                "result_types": [
                    "dataset",
                ],
                "search_text": "Legacy History Match",
            },
            format="json",
        )

        assert dataset_response.status_code == status.HTTP_200_OK
        assert dataset_response.data["results"] == []

        dataset_version_response = client.post(
            url,
            {
                "result_types": [
                    "dataset_version",
                ],
                "search_text": "Legacy History Match",
            },
            format="json",
        )

        assert dataset_version_response.status_code == status.HTTP_200_OK
        assert {result["item"]["pk"] for result in dataset_version_response.data["results"]} == {
            str(self.history_old_version.pk),
        }

    def test_dataset_child_name_filters_use_latest_version_only(self, client):
        url = reverse("search-global")

        dataset_response = client.post(
            url,
            {
                "result_types": [
                    "dataset",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "dataset_version_name",
                            "operator": "contains",
                            "value": "Legacy History Match",
                        },
                    ],
                },
            },
            format="json",
        )

        assert dataset_response.status_code == status.HTTP_200_OK
        assert dataset_response.data["results"] == []

        file_response = client.post(
            url,
            {
                "result_types": [
                    "dataset",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "file_name",
                            "operator": "contains",
                            "value": "legacy-history-match.csv",
                        },
                    ],
                },
            },
            format="json",
        )

        assert file_response.status_code == status.HTTP_200_OK
        assert file_response.data["results"] == []

    def test_dataset_metadata_filters_use_latest_version_only(self, client):
        url = reverse("search-global")

        dataset_response = client.post(
            url,
            {
                "result_types": [
                    "dataset",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.key",
                            "operator": "is",
                            "value": "legacy_integer",
                        },
                        {
                            "field": "metadata.source",
                            "operator": "is",
                            "value": "template",
                        },
                    ],
                },
            },
            format="json",
        )

        assert dataset_response.status_code == status.HTTP_200_OK
        assert dataset_response.data["results"] == []

        dataset_version_response = client.post(
            url,
            {
                "result_types": [
                    "dataset_version",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.key",
                            "operator": "is",
                            "value": "legacy_integer",
                        },
                        {
                            "field": "metadata.source",
                            "operator": "is",
                            "value": "template",
                        },
                    ],
                },
            },
            format="json",
        )

        assert dataset_version_response.status_code == status.HTTP_200_OK
        assert {result["item"]["pk"] for result in dataset_version_response.data["results"]} == {
            str(self.history_old_version.pk),
        }

    def test_dataset_has_file_matches_latest_version_state(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": [
                    "dataset",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "has_file",
                            "operator": "is",
                            "value": False,
                        },
                    ],
                },
                "response_mode": "compact",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        result_items = {result["item"]["pk"]: result["item"] for result in response.data["results"]}
        assert str(self.history_dataset.pk) in result_items
        assert result_items[str(self.history_dataset.pk)]["has_file"] is False
        assert str(self.dataset.pk) not in result_items

    def test_nested_metadata_group_correlates_same_row(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": [
                    "project",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.key",
                            "operator": "is",
                            "value": "Author",
                        },
                        {
                            "field": "metadata.value",
                            "operator": "contains",
                            "value": "nbuechler",
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["item"]["name"] == self.project_metadata_match.name

    def test_version_search_matches_template_backed_metadata_defaults(self, client):
        url = reverse("search-global")

        key_only_response = client.post(
            url,
            {
                "result_types": [
                    "dataset_version",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.key",
                            "operator": "is",
                            "value": "integer",
                        },
                    ],
                },
            },
            format="json",
        )

        assert key_only_response.status_code == status.HTTP_200_OK
        assert key_only_response.data["count"] == 1
        assert key_only_response.data["results"][0]["item"]["name"] == self.version.name

        value_only_response = client.post(
            url,
            {
                "result_types": [
                    "dataset_version",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.value",
                            "operator": "is",
                            "value": "1",
                        },
                    ],
                },
            },
            format="json",
        )

        assert value_only_response.status_code == status.HTTP_200_OK
        assert value_only_response.data["count"] == 1
        assert value_only_response.data["results"][0]["item"]["name"] == self.version.name

        typed_response = client.post(
            url,
            {
                "result_types": [
                    "dataset_version",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.key",
                            "operator": "is",
                            "value": "integer",
                        },
                        {
                            "field": "metadata.value",
                            "operator": "is",
                            "value": 1,
                            "field_type": MetadataFieldType.INTEGER,
                        },
                    ],
                },
            },
            format="json",
        )

        assert typed_response.status_code == status.HTTP_200_OK
        assert typed_response.data["count"] == 1
        assert typed_response.data["results"][0]["item"]["name"] == self.version.name

        search_text_response = client.post(
            url,
            {
                "search_text": "integer",
                "result_types": [
                    "dataset_version",
                ],
            },
            format="json",
        )

        assert search_text_response.status_code == status.HTTP_200_OK
        assert {result["item"]["pk"] for result in search_text_response.data["results"]} == {
            str(self.version.pk),
            str(self.history_old_version.pk),
        }

    def test_template_results_return_visible_templates_only(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": [
                    "template",
                ],
                "search_text": "Search Template",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert {result["item"]["name"] for result in response.data["results"]} == {
            self.version_template_container.name,
            self.project_template_container.name,
            self.folder_template_container.name,
            self.dataset_template_container.name,
        }
        assert self.hidden_template_container.name not in {
            result["item"]["name"] for result in response.data["results"]
        }
        assert response.data["count"] == 4

    def test_template_filters_and_compact_mode(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": [
                    "template",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "template_type",
                            "operator": "is",
                            "value": "project",
                        },
                        {
                            "field": "template_name",
                            "operator": "contains",
                            "value": "Project Search",
                        },
                    ],
                },
                "response_mode": "compact",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        item = response.data["results"][0]["item"]
        assert item["pk"] == str(self.project_template_container.pk)
        assert item["template_type"] == "project"
        assert item["assigned_to"]["result_type"] == "project"
        assert item["field_count"] == 1
        assert item["latest_template"]["pk"] == str(self.project_template.pk)

    def test_metadata_field_type_matches_versions_and_templates(self, client):
        url = reverse("search-global")

        version_instance_response = client.post(
            url,
            {
                "result_types": [
                    "dataset_version",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.key",
                            "operator": "is",
                            "value": "SampleCount",
                        },
                        {
                            "field": "metadata.field_type",
                            "operator": "is",
                            "value": MetadataFieldType.INTEGER,
                        },
                    ],
                },
            },
            format="json",
        )

        assert version_instance_response.status_code == status.HTTP_200_OK
        assert {result["item"]["pk"] for result in version_instance_response.data["results"]} == {
            str(self.version.pk),
        }

        version_template_response = client.post(
            url,
            {
                "result_types": [
                    "dataset_version",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.key",
                            "operator": "is",
                            "value": "integer",
                        },
                        {
                            "field": "metadata.field_type",
                            "operator": "is",
                            "value": MetadataFieldType.INTEGER,
                        },
                        {
                            "field": "metadata.value",
                            "operator": "is",
                            "value": 1,
                            "field_type": MetadataFieldType.INTEGER,
                        },
                    ],
                },
            },
            format="json",
        )

        assert version_template_response.status_code == status.HTTP_200_OK
        assert {result["item"]["pk"] for result in version_template_response.data["results"]} == {
            str(self.version.pk),
        }

        template_response = client.post(
            url,
            {
                "result_types": [
                    "template",
                ],
                "group": {
                    "conditions": [
                        {
                            "field": "metadata.field_type",
                            "operator": "is",
                            "value": MetadataFieldType.INTEGER,
                        },
                    ],
                },
            },
            format="json",
        )

        assert template_response.status_code == status.HTTP_200_OK
        assert {result["item"]["pk"] for result in template_response.data["results"]} == {
            str(self.version_template_container.pk),
            str(self.history_template_container.pk),
        }

        latest_only_response = client.post(
            url,
            {
                "result_types": [
                    "template",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "template_type",
                            "operator": "is",
                            "value": "folder",
                        },
                        {
                            "field": "metadata.field_type",
                            "operator": "is",
                            "value": MetadataFieldType.INTEGER,
                        },
                    ],
                },
            },
            format="json",
        )

        assert latest_only_response.status_code == status.HTTP_200_OK
        assert latest_only_response.data["count"] == 0

    def test_mixed_template_and_version_results_are_unified(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": [
                    "template",
                    "dataset_version",
                ],
                "group": {
                    "conditions": [
                        {
                            "field": "metadata.field_type",
                            "operator": "is",
                            "value": MetadataFieldType.INTEGER,
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert {result["result_type"] for result in response.data["results"]} == {
            "template",
            "dataset_version",
        }
        assert len([result for result in response.data["results"] if result["result_type"] == "template"]) == 2

    def test_template_version_returns_historical_versions(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": [
                    "template_version",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "template_type",
                            "operator": "is",
                            "value": "folder",
                        },
                        {
                            "field": "metadata.field_type",
                            "operator": "is",
                            "value": MetadataFieldType.INTEGER,
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["result_type"] == "template_version"
        assert response.data["results"][0]["item"]["pk"] == str(self.folder_template.pk)
        assert response.data["results"][0]["item"]["template"]["pk"] == str(self.folder_template_container.pk)

    def test_template_and_template_version_can_be_searched_together(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": [
                    "template",
                    "template_version",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "template_type",
                            "operator": "is",
                            "value": "folder",
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert {result["result_type"] for result in response.data["results"]} == {
            "template",
            "template_version",
        }
        assert len([result for result in response.data["results"] if result["result_type"] == "template"]) == 1
        assert len([result for result in response.data["results"] if result["result_type"] == "template_version"]) == 2

    def test_template_name_matches_all_versions_in_container(self, client):
        """template_name on template_version should match by container name, not version display name."""
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": [
                    "template_version",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "template_name",
                            "operator": "is",
                            "value": "Folder Search Template",
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        # Both versions (v1 and v2) should be returned even though their names differ from the container
        assert response.data["count"] == 2
        returned_pks = {result["item"]["pk"] for result in response.data["results"]}
        assert str(self.folder_template.pk) in returned_pks
        assert str(self.folder_template_latest.pk) in returned_pks

    def test_template_version_name_matches_display_name(self, client):
        """template_version_name should match by version display name (Coalesce of version.name and container.name)."""
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": [
                    "template_version",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "template_version_name",
                            "operator": "contains",
                            "value": "Folder Search Template v1",
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["item"]["pk"] == str(self.folder_template.pk)

    def test_template_id_filters_template_versions(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": [
                    "template_version",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "template_id",
                            "operator": "is",
                            "value": str(self.folder_template_container.pk),
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 2
        assert all(
            result["item"]["template"]["pk"] == str(self.folder_template_container.pk)
            for result in response.data["results"]
        )

    def test_template_version_id_filters_exact_version(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": [
                    "template_version",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "template_version_id",
                            "operator": "is",
                            "value": str(self.folder_template.pk),
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["item"]["pk"] == str(self.folder_template.pk)

    def test_name_field_works_for_template_result_type(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": [
                    "template",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "name",
                            "operator": "contains",
                            "value": "Project Search",
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["item"]["pk"] == str(self.project_template_container.pk)

    def test_name_field_on_template_version_uses_plain_name(self, client):
        """name on template_version should match the version's own name, not the coalesced display name."""
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": [
                    "template_version",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "name",
                            "operator": "is",
                            "value": "Folder Search Template v1",
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["item"]["pk"] == str(self.folder_template.pk)

    def test_project_name_filters_templates_by_scope(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": [
                    "template",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "project_name",
                            "operator": "contains",
                            "value": "Horizon",
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        # project_template is assigned to project, folder_template to folder (in the project),
        # dataset_template to dataset (in the folder in the project) — all three match via project_name
        returned_pks = {result["item"]["pk"] for result in response.data["results"]}
        assert str(self.project_template_container.pk) in returned_pks
        assert str(self.folder_template_container.pk) in returned_pks
        assert str(self.dataset_template_container.pk) in returned_pks

    def test_project_name_filters_template_versions_by_scope(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": [
                    "template_version",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "project_name",
                            "operator": "contains",
                            "value": "Horizon",
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] > 0
        # All returned template versions should belong to containers scoped within the project
        for result in response.data["results"]:
            assert result["result_type"] == "template_version"

    def test_folder_name_filters_templates_by_scope(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": [
                    "template",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "folder_name",
                            "operator": "contains",
                            "value": "Research",
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        returned_pks = {result["item"]["pk"] for result in response.data["results"]}
        # folder_template is assigned to the folder, dataset_template is assigned to dataset in the folder
        assert str(self.folder_template_container.pk) in returned_pks
        assert str(self.dataset_template_container.pk) in returned_pks
        # project_template is assigned to project, not folder — should NOT match
        assert str(self.project_template_container.pk) not in returned_pks

    def test_dataset_name_filters_templates_by_scope(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": [
                    "template",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "dataset_name",
                            "operator": "contains",
                            "value": "Experiment",
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["item"]["pk"] == str(self.dataset_template_container.pk)

    def test_template_type_requires_template_result_type(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": [
                    "dataset_version",
                ],
                "group": {
                    "conditions": [
                        {
                            "field": "template_type",
                            "operator": "is",
                            "value": "global",
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_schema_endpoint_returns_per_type_structure(self, client):
        url = reverse("search-global-schema")

        response = client.get(url, format="json")

        assert response.status_code == status.HTTP_200_OK

        # Top-level keys present
        assert "result_types" in response.data
        assert "default_result_types" in response.data
        assert "sort_directions" in response.data
        assert "response_modes" in response.data
        assert "types" in response.data
        assert "compact" in response.data["response_modes"]

        # Old flat keys removed
        assert "fields" not in response.data
        assert "sort_fields" not in response.data
        assert "default_sort" not in response.data

        # All 8 result types have entries
        expected_types = {
            "project",
            "folder",
            "subfolder",
            "dataset",
            "dataset_version",
            "file",
            "template",
            "template_version",
        }
        assert set(response.data["types"].keys()) == expected_types

        # Each type has required keys
        for result_type, type_schema in response.data["types"].items():
            assert "fields" in type_schema, f"{result_type} missing fields"
            assert "sort_fields" in type_schema, f"{result_type} missing sort_fields"
            assert "default_sort" in type_schema, f"{result_type} missing default_sort"
            assert type_schema["sort_fields"] == ["relevance", "updated_at", "created_at"]
            assert type_schema["default_sort"] == [{"field": "updated_at", "direction": "desc"}]

    def test_schema_type_specific_fields_only_under_correct_types(self, client):
        url = reverse("search-global-schema")

        response = client.get(url, format="json")
        types = response.data["types"]

        # "status" is no longer searchable on any result type (ADR 0003).
        for result_type, type_schema in types.items():
            field_ids = {f["id"] for f in type_schema["fields"]}
            assert "status" not in field_ids, f"status should not appear under {result_type}"
            assert "metadata_upgrade_status" not in field_ids, (
                f"metadata_upgrade_status should not appear under {result_type}"
            )

        dv_field_ids = {f["id"] for f in types["dataset_version"]["fields"]}
        project_field_ids = {f["id"] for f in types["project"]["fields"]}

        # "metadata_is_complete" only under dataset_version
        assert "metadata_is_complete" in dv_field_ids
        assert "metadata_is_complete" not in project_field_ids

        # "name" should be under all types (universal)
        for result_type, type_schema in types.items():
            field_ids = {f["id"] for f in type_schema["fields"]}
            assert "name" in field_ids, f"name missing from {result_type}"

        # "description" only under project and folder
        folder_field_ids = {f["id"] for f in types["folder"]["fields"]}
        file_field_ids = {f["id"] for f in types["file"]["fields"]}
        assert "description" in project_field_ids
        assert "description" in folder_field_ids
        assert "description" not in file_field_ids

    def test_schema_fields_do_not_contain_result_types(self, client):
        url = reverse("search-global-schema")

        response = client.get(url, format="json")

        for result_type, type_schema in response.data["types"].items():
            for field in type_schema["fields"]:
                assert "result_types" not in field, (
                    f"Field {field['id']} under {result_type} should not have result_types"
                )

    def test_schema_field_properties_preserved(self, client):
        url = reverse("search-global-schema")

        response = client.get(url, format="json")
        types = response.data["types"]

        dv_fields = {f["id"]: f for f in types["dataset_version"]["fields"]}

        # Check metadata_only and backend-owned per-type operators on metadata.value.
        assert dv_fields["metadata.value"]["metadata_only"] is True
        assert "typed_field_types" not in dv_fields["metadata.value"]
        assert dv_fields["metadata.value"]["operators_by_field_type"][MetadataFieldType.INTEGER] == [
            "between",
            "gt",
            "gte",
            "is",
            "is_empty",
            "is_not",
            "is_not_empty",
            "lt",
            "lte",
        ]

        # Check metadata.field_type enum_values includes all MetadataFieldType choices
        assert dv_fields["metadata.field_type"]["enum_values"] == sorted(
            [choice for choice, _label in MetadataFieldType.choices],
        )

        # Check template_type enum on template type
        template_fields = {f["id"]: f for f in types["template"]["fields"]}
        assert template_fields["template_type"]["enum_values"] == [
            "dataset",
            "folder",
            "global",
            "project",
        ]

        # metadata.source only under dataset and dataset_version
        dataset_fields = {f["id"]: f for f in types["dataset"]["fields"]}
        assert "metadata.source" in dataset_fields
        assert dataset_fields["metadata.source"]["enum_values"] == [
            "either",
            "instance",
            "system",
            "template",
        ]
        assert "metadata.source" not in template_fields

    def test_schema_default_sort_conditions_present(self, client):
        url = reverse("search-global-schema")

        response = client.get(url, format="json")

        for result_type, type_schema in response.data["types"].items():
            assert "default_sort_conditions" in type_schema, f"{result_type} missing default_sort_conditions"
            conditions = type_schema["default_sort_conditions"]
            condition_map = {c["when"]: c["sort"] for c in conditions}
            assert "positive_text_input_present" in condition_map
            assert "otherwise" in condition_map
            assert condition_map["positive_text_input_present"] == [
                {"field": "relevance", "direction": "desc"},
            ]
            assert condition_map["otherwise"] == [
                {"field": "updated_at", "direction": "desc"},
            ]

    def test_default_sort_uses_relevance_when_search_text_present(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": ["project"],
                "search_text": "Horizon",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] > 0
        # When search_text is present and no explicit sort, results should be
        # sorted by relevance (which means the most relevant result is first).
        # We just verify the request succeeds — the important thing is that
        # DEFAULT_SORT with relevance is applied, not updated_at.

    def test_default_sort_uses_updated_at_when_no_text_input(self, client):
        url = reverse("search-global")

        # Filter-only search with no search_text and no text-matching operators
        response = client.post(
            url,
            {
                "result_types": ["project"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "my_project_permission",
                            "operator": "is",
                            "value": "project_admin",
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

    def test_explicit_sort_overrides_conditional_default(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": ["project"],
                "search_text": "Horizon",
                "sort": [{"field": "created_at", "direction": "asc"}],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

    def test_kind_rule_and_group_accepted(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": ["project"],
                "group": {
                    "kind": "group",
                    "operator": "AND",
                    "conditions": [
                        {
                            "kind": "rule",
                            "field": "name",
                            "operator": "contains",
                            "value": "Horizon",
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] > 0

    def test_kind_omitted_backward_compatible(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": ["project"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "name",
                            "operator": "contains",
                            "value": "Horizon",
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] > 0

    def test_invalid_kind_value_rejected(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": ["project"],
                "group": {
                    "kind": "foo",
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "name",
                            "operator": "contains",
                            "value": "Horizon",
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_mismatched_kind_rejected(self, client):
        url = reverse("search-global")

        # kind: "group" on what is structurally a rule
        response = client.post(
            url,
            {
                "result_types": ["project"],
                "group": {
                    "kind": "group",
                    "operator": "AND",
                    "conditions": [
                        {
                            "kind": "group",
                            "field": "name",
                            "operator": "contains",
                            "value": "Horizon",
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_legacy_version_result_type_is_rejected(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": [
                    "version",
                ],
                "search_text": self.version.name,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_permission_filters_use_effective_permissions(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": [
                    "project",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "my_project_permission",
                            "operator": "is",
                            "value": "project_admin",
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert {result["item"]["name"] for result in response.data["results"]} >= {
            self.project_admin.name,
            self.project_metadata_match.name,
        }
        assert self.shared_member_project.name not in {result["item"]["name"] for result in response.data["results"]}

        response = client.post(
            url,
            {
                "result_types": [
                    "folder",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "my_folder_permission",
                            "operator": "is",
                            "value": "viewer",
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert [result["item"]["name"] for result in response.data["results"]] == [
            self.shared_member_folder.name,
        ]

    def test_hidden_objects_do_not_leak(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "search_text": self.hidden_project.name,
                "result_types": [
                    "project",
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 0
        assert response.data["results"] == []

    def test_typed_metadata_comparison_validation_and_matching(self, client):
        url = reverse("search-global")

        invalid_response = client.post(
            url,
            {
                "result_types": [
                    "dataset_version",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.value",
                            "operator": "gt",
                            "value": 100,
                        },
                    ],
                },
            },
            format="json",
        )

        assert invalid_response.status_code == status.HTTP_400_BAD_REQUEST

        valid_response = client.post(
            url,
            {
                "result_types": [
                    "dataset_version",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.key",
                            "operator": "is",
                            "value": "SampleCount",
                        },
                        {
                            "field": "metadata.value",
                            "operator": "gt",
                            "value": 100,
                        },
                    ],
                },
            },
            format="json",
        )

        assert valid_response.status_code == status.HTTP_200_OK
        assert valid_response.data["count"] == 1
        assert valid_response.data["results"][0]["item"]["name"] == self.version.name

        typed_equality_response = client.post(
            url,
            {
                "result_types": [
                    "dataset_version",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.key",
                            "operator": "is",
                            "value": "SampleCount",
                        },
                        {
                            "field": "metadata.value",
                            "operator": "is",
                            "value": 150,
                        },
                    ],
                },
            },
            format="json",
        )

        assert typed_equality_response.status_code == status.HTTP_200_OK
        assert typed_equality_response.data["count"] == 1
        assert typed_equality_response.data["results"][0]["item"]["name"] == self.version.name

        typed_inequality_response = client.post(
            url,
            {
                "result_types": [
                    "dataset_version",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.key",
                            "operator": "is",
                            "value": "SampleCount",
                        },
                        {
                            "field": "metadata.value",
                            "operator": "is_not",
                            "value": 150,
                        },
                    ],
                },
            },
            format="json",
        )

        assert typed_inequality_response.status_code == status.HTTP_200_OK
        assert typed_inequality_response.data["count"] == 0

        explicit_field_type_response = client.post(
            url,
            {
                "result_types": [
                    "dataset_version",
                ],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.key",
                            "operator": "is",
                            "value": "SampleCount",
                        },
                        {
                            "field": "metadata.value",
                            "operator": "gt",
                            "value": 100,
                            "field_type": MetadataFieldType.INTEGER,
                        },
                    ],
                },
            },
            format="json",
        )

        assert explicit_field_type_response.status_code == status.HTTP_200_OK
        assert explicit_field_type_response.data["count"] == 1
        assert explicit_field_type_response.data["results"][0]["item"]["name"] == self.version.name

    def test_cursor_pagination_round_trip(self, client):
        url = reverse("search-global")

        first_response = client.post(
            url,
            {
                "search_text": "Project",
                "result_types": [
                    "project",
                ],
                "limit": 1,
                "sort": [
                    {
                        "field": "created_at",
                        "direction": "asc",
                    },
                ],
            },
            format="json",
        )

        assert first_response.status_code == status.HTTP_200_OK
        assert first_response.data["count"] >= 3
        assert len(first_response.data["results"]) == 1
        assert first_response.data["next_cursor"] is not None
        assert first_response.data["previous_cursor"] is None

        second_response = client.post(
            url,
            {
                "search_text": "Project",
                "result_types": [
                    "project",
                ],
                "limit": 1,
                "cursor": first_response.data["next_cursor"],
                "cursor_direction": "next",
                "sort": [
                    {
                        "field": "created_at",
                        "direction": "asc",
                    },
                ],
            },
            format="json",
        )

        assert second_response.status_code == status.HTTP_200_OK
        assert len(second_response.data["results"]) == 1
        assert second_response.data["next_cursor"] is not None
        assert second_response.data["previous_cursor"] is not None
        assert second_response.data["results"][0]["item"]["pk"] != first_response.data["results"][0]["item"]["pk"]

        previous_response = client.post(
            url,
            {
                "search_text": "Project",
                "result_types": [
                    "project",
                ],
                "limit": 1,
                "cursor": second_response.data["previous_cursor"],
                "cursor_direction": "previous",
                "sort": [
                    {
                        "field": "created_at",
                        "direction": "asc",
                    },
                ],
            },
            format="json",
        )

        assert previous_response.status_code == status.HTTP_200_OK
        assert len(previous_response.data["results"]) == 1
        assert previous_response.data["results"][0]["item"]["pk"] == first_response.data["results"][0]["item"]["pk"]

    def test_cursor_mismatch_and_invalid_cursor_are_rejected(self, client):
        url = reverse("search-global")

        first_response = client.post(
            url,
            {
                "search_text": "Project",
                "result_types": [
                    "project",
                ],
                "limit": 1,
            },
            format="json",
        )

        assert first_response.status_code == status.HTTP_200_OK
        cursor = first_response.data["next_cursor"]
        assert cursor is not None

        mismatched_response = client.post(
            url,
            {
                "search_text": "Shared",
                "result_types": [
                    "project",
                ],
                "limit": 1,
                "cursor": cursor,
            },
            format="json",
        )

        assert mismatched_response.status_code == status.HTTP_400_BAD_REQUEST

        invalid_response = client.post(
            url,
            {
                "search_text": "Project",
                "result_types": [
                    "project",
                ],
                "limit": 1,
                "cursor": f"{cursor}tampered",
            },
            format="json",
        )

        assert invalid_response.status_code == status.HTTP_400_BAD_REQUEST

    def test_offset_is_rejected(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "search_text": "Project",
                "result_types": [
                    "project",
                ],
                "limit": 1,
                "offset": 1,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_keyless_date_between_matches_across_metadata_sources(self, client):
        url = reverse("search-global")

        # Add DATE metadata on the version (loose metadata)
        set_metadata(
            assigned_to_content_type=self.version.get_content_type(),
            assigned_to_object_id=self.version.pk,
            custom_key="DueDate",
            value="2026-03-15",
            field_type=MetadataFieldType.DATE,
        )

        # Keyless DATE between — should match via loose metadata on the version
        response = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.value",
                            "operator": "between",
                            "value": ["2026-03-01", "2026-03-31"],
                            "field_type": MetadataFieldType.DATE,
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["item"]["name"] == self.dataset.name

        # Out-of-range date — should match nothing
        no_match_response = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.value",
                            "operator": "between",
                            "value": ["2026-01-01", "2026-02-28"],
                            "field_type": MetadataFieldType.DATE,
                        },
                    ],
                },
            },
            format="json",
        )

        assert no_match_response.status_code == status.HTTP_200_OK
        assert no_match_response.data["count"] == 0

    def test_keyless_typed_metadata_search_with_explicit_field_type(self, client):
        url = reverse("search-global")

        # Keyless INTEGER gt — should match the version with SampleCount=150
        response = client.post(
            url,
            {
                "result_types": ["dataset_version"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.value",
                            "operator": "gt",
                            "value": 100,
                            "field_type": MetadataFieldType.INTEGER,
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["item"]["name"] == self.version.name

        # Keyless INTEGER gt — threshold above SampleCount, should match nothing
        no_match_response = client.post(
            url,
            {
                "result_types": ["dataset_version"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.value",
                            "operator": "gt",
                            "value": 200,
                            "field_type": MetadataFieldType.INTEGER,
                        },
                    ],
                },
            },
            format="json",
        )

        assert no_match_response.status_code == status.HTTP_200_OK
        assert no_match_response.data["count"] == 0

        # Keyless strict operator, no field_type — should still fail validation
        invalid_response = client.post(
            url,
            {
                "result_types": ["dataset_version"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.value",
                            "operator": "gt",
                            "value": 100,
                        },
                    ],
                },
            },
            format="json",
        )

        assert invalid_response.status_code == status.HTTP_400_BAD_REQUEST

    def test_keyless_typed_metadata_in_or_group(self, client):
        url = reverse("search-global")

        # OR group: keyless INTEGER gt OR keyless INTEGER lt (same type)
        # Version has SampleCount=150 (INTEGER) — gt 100 matches
        response = client.post(
            url,
            {
                "result_types": ["dataset_version"],
                "group": {
                    "operator": "OR",
                    "conditions": [
                        {
                            "field": "metadata.value",
                            "operator": "gt",
                            "value": 100,
                            "field_type": MetadataFieldType.INTEGER,
                        },
                        {
                            "field": "metadata.value",
                            "operator": "lt",
                            "value": -100,
                            "field_type": MetadataFieldType.INTEGER,
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["item"]["name"] == self.version.name

    def test_keyed_typed_metadata_in_or_group_still_rejected(self, client):
        url = reverse("search-global")

        # Keyed typed condition in OR group — should still be rejected
        response = client.post(
            url,
            {
                "result_types": ["dataset_version"],
                "group": {
                    "operator": "OR",
                    "conditions": [
                        {
                            "field": "metadata.key",
                            "operator": "is",
                            "value": "SampleCount",
                        },
                        {
                            "field": "metadata.value",
                            "operator": "gt",
                            "value": 100,
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_keyless_multi_key_dedup(self, client):
        url = reverse("search-global")

        # Add two DATE metadata entries on the version, both in range
        set_metadata(
            assigned_to_content_type=self.version.get_content_type(),
            assigned_to_object_id=self.version.pk,
            custom_key="StartDate",
            value="2026-03-10",
            field_type=MetadataFieldType.DATE,
        )
        set_metadata(
            assigned_to_content_type=self.version.get_content_type(),
            assigned_to_object_id=self.version.pk,
            custom_key="EndDate",
            value="2026-03-20",
            field_type=MetadataFieldType.DATE,
        )

        # Both date keys fall in range — entity should appear exactly once
        response = client.post(
            url,
            {
                "result_types": ["dataset_version"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.value",
                            "operator": "between",
                            "value": ["2026-03-01", "2026-03-31"],
                            "field_type": MetadataFieldType.DATE,
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1

        # Neither date key in range — entity should not appear
        no_match_response = client.post(
            url,
            {
                "result_types": ["dataset_version"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.value",
                            "operator": "between",
                            "value": ["2026-06-01", "2026-06-30"],
                            "field_type": MetadataFieldType.DATE,
                        },
                    ],
                },
            },
            format="json",
        )

        assert no_match_response.status_code == status.HTTP_200_OK
        assert no_match_response.data["count"] == 0

    def test_sibling_field_type_condition_as_type_anchor(self, client):
        url = reverse("search-global")

        # metadata.field_type "is" INTEGER as sibling condition provides the type
        # anchor — no inline field_type on the metadata.value rule needed.
        # Version has SampleCount=150 (INTEGER) — gt 100 matches.
        response = client.post(
            url,
            {
                "result_types": ["dataset_version"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.field_type",
                            "operator": "is",
                            "value": MetadataFieldType.INTEGER,
                        },
                        {
                            "field": "metadata.value",
                            "operator": "gt",
                            "value": 100,
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["item"]["name"] == self.version.name

        # Threshold above SampleCount — should match nothing
        no_match_response = client.post(
            url,
            {
                "result_types": ["dataset_version"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.field_type",
                            "operator": "is",
                            "value": MetadataFieldType.INTEGER,
                        },
                        {
                            "field": "metadata.value",
                            "operator": "gt",
                            "value": 200,
                        },
                    ],
                },
            },
            format="json",
        )

        assert no_match_response.status_code == status.HTTP_200_OK
        assert no_match_response.data["count"] == 0

    def test_sibling_field_type_and_metadata_key_conflict(self, client):
        url = reverse("search-global")

        # metadata.key "is" SampleCount (INTEGER in DB) + metadata.field_type "is" DECIMAL — conflict → 400.
        response = client.post(
            url,
            {
                "result_types": ["dataset_version"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.key",
                            "operator": "is",
                            "value": "SampleCount",
                        },
                        {
                            "field": "metadata.field_type",
                            "operator": "is",
                            "value": MetadataFieldType.DECIMAL,
                        },
                        {
                            "field": "metadata.value",
                            "operator": "gt",
                            "value": 100,
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_sibling_field_type_and_metadata_key_agreement(self, client):
        url = reverse("search-global")

        # metadata.key "is" SampleCount (INTEGER in DB) + metadata.field_type "is" INTEGER — consistent.
        response = client.post(
            url,
            {
                "result_types": ["dataset_version"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.key",
                            "operator": "is",
                            "value": "SampleCount",
                        },
                        {
                            "field": "metadata.field_type",
                            "operator": "is",
                            "value": MetadataFieldType.INTEGER,
                        },
                        {
                            "field": "metadata.value",
                            "operator": "gt",
                            "value": 100,
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["item"]["name"] == self.version.name

    def test_sibling_field_type_and_inline_field_type_conflict(self, client):
        url = reverse("search-global")

        # Sibling says INTEGER, inline says DECIMAL — conflict → 400.
        response = client.post(
            url,
            {
                "result_types": ["dataset_version"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.field_type",
                            "operator": "is",
                            "value": MetadataFieldType.INTEGER,
                        },
                        {
                            "field": "metadata.value",
                            "operator": "gt",
                            "value": 100,
                            "field_type": MetadataFieldType.DECIMAL,
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_sibling_field_type_and_inline_field_type_agreement(self, client):
        url = reverse("search-global")

        # Both sibling metadata.field_type and inline field_type agree on INTEGER.
        response = client.post(
            url,
            {
                "result_types": ["dataset_version"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.field_type",
                            "operator": "is",
                            "value": MetadataFieldType.INTEGER,
                        },
                        {
                            "field": "metadata.value",
                            "operator": "gt",
                            "value": 100,
                            "field_type": MetadataFieldType.INTEGER,
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1

    def test_sibling_field_type_non_is_operator_does_not_qualify(self, client):
        url = reverse("search-global")

        # metadata.field_type with "in" operator does not qualify as a type anchor.
        response = client.post(
            url,
            {
                "result_types": ["dataset_version"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.field_type",
                            "operator": "in",
                            "value": [MetadataFieldType.INTEGER],
                        },
                        {
                            "field": "metadata.value",
                            "operator": "gt",
                            "value": 100,
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_sibling_field_type_condition_in_or_group_rejected(self, client):
        url = reverse("search-global")

        # metadata.field_type as sibling in an OR group — semantically wrong,
        # the type constraint doesn't conjunctively apply to the value comparison.
        response = client.post(
            url,
            {
                "result_types": ["dataset_version"],
                "group": {
                    "operator": "OR",
                    "conditions": [
                        {
                            "field": "metadata.field_type",
                            "operator": "is",
                            "value": MetadataFieldType.INTEGER,
                        },
                        {
                            "field": "metadata.value",
                            "operator": "gt",
                            "value": 100,
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_standalone_template_name_filters_datasets(self, client):
        url = reverse("search-global")

        # Search for datasets with metadata from "Global Version Search Template"
        # (version_metadata_template is linked via version.metadata_templates M2M)
        response = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.template_name",
                            "operator": "is",
                            "value": "Global Version Search Template",
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["item"]["name"] == self.dataset.name

        # Non-matching template name → empty results
        no_match = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.template_name",
                            "operator": "is",
                            "value": "Nonexistent Template",
                        },
                    ],
                },
            },
            format="json",
        )

        assert no_match.status_code == status.HTTP_200_OK
        assert no_match.data["count"] == 0

    def test_standalone_template_id_filters_datasets(self, client):
        url = reverse("search-global")

        # Search by template container ID
        response = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.template_id",
                            "operator": "is",
                            "value": str(self.version_template_container.pk),
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["item"]["name"] == self.dataset.name

        # Non-matching template ID → empty results
        no_match = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.template_id",
                            "operator": "is",
                            "value": "00000000-0000-0000-0000-000000000000",
                        },
                    ],
                },
            },
            format="json",
        )

        assert no_match.status_code == status.HTTP_200_OK
        assert no_match.data["count"] == 0

    def test_standalone_template_name_in_or_group(self, client):
        url = reverse("search-global")

        # Standalone template qualifier in OR group with non-metadata condition
        response = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "operator": "OR",
                    "conditions": [
                        {
                            "field": "metadata.template_name",
                            "operator": "is",
                            "value": "Global Version Search Template",
                        },
                        {
                            "field": "name",
                            "operator": "is",
                            "value": "Nonexistent Dataset Name",
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["item"]["name"] == self.dataset.name

    def test_template_name_with_metadata_key_narrows_correctly(self, client):
        url = reverse("search-global")

        # Template "Global Version Search Template" has key "integer"
        # Search: template_name + metadata.key that exists on the template → match
        response = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.template_name",
                            "operator": "is",
                            "value": "Global Version Search Template",
                        },
                        {
                            "field": "metadata.key",
                            "operator": "is",
                            "value": "integer",
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["item"]["name"] == self.dataset.name

        # Search: same template + key that only exists as loose metadata → no match
        no_match = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.template_name",
                            "operator": "is",
                            "value": "Global Version Search Template",
                        },
                        {
                            "field": "metadata.key",
                            "operator": "is",
                            "value": "SampleCount",
                        },
                    ],
                },
            },
            format="json",
        )

        assert no_match.status_code == status.HTTP_200_OK
        assert no_match.data["count"] == 0

    def test_template_name_with_keyless_typed_value(self, client):
        url = reverse("search-global")

        # Add a DATE template field to the version template
        MetadataTemplateField.objects.create(
            metadata_template=self.version_metadata_template,
            custom_key="experiment_date",
            field_type=MetadataFieldType.DATE,
            value={"value": "2026-04-15"},
        )

        # template_name + keyless typed DATE between → should match
        response = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.template_name",
                            "operator": "is",
                            "value": "Global Version Search Template",
                        },
                        {
                            "field": "metadata.value",
                            "operator": "between",
                            "value": ["2026-04-14", "2026-04-16"],
                            "field_type": MetadataFieldType.DATE,
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["item"]["name"] == self.dataset.name

        # Same search but wrong template → no match
        no_match = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.template_name",
                            "operator": "is",
                            "value": "Nonexistent Template",
                        },
                        {
                            "field": "metadata.value",
                            "operator": "between",
                            "value": ["2026-04-14", "2026-04-16"],
                            "field_type": MetadataFieldType.DATE,
                        },
                    ],
                },
            },
            format="json",
        )

        assert no_match.status_code == status.HTTP_200_OK
        assert no_match.data["count"] == 0

    def test_template_qualifier_with_metadata_conditions_in_or_group_rejected(self, client):
        url = reverse("search-global")

        # template_name + metadata.key in OR group → 400
        response = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "operator": "OR",
                    "conditions": [
                        {
                            "field": "metadata.template_name",
                            "operator": "is",
                            "value": "Global Version Search Template",
                        },
                        {
                            "field": "metadata.key",
                            "operator": "is",
                            "value": "integer",
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_metadata_source_system_distinguishes_read_only_metadata(self, client):
        url = reverse("search-global")

        # Add system-generated read_only metadata to the version file
        set_metadata(
            assigned_to_content_type=self.version_file.get_content_type(),
            assigned_to_object_id=self.version_file.pk,
            custom_key="FILE_SIZE",
            value="1024",
            read_only=True,
        )

        # metadata.source is "system" → should match the dataset (via version file's read_only metadata)
        response = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.source",
                            "operator": "is",
                            "value": "system",
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["item"]["name"] == self.dataset.name

        # metadata.source is "instance" → should still match datasets with user-authored metadata
        instance_response = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.source",
                            "operator": "is",
                            "value": "instance",
                        },
                    ],
                },
            },
            format="json",
        )

        assert instance_response.status_code == status.HTTP_200_OK
        assert instance_response.data["count"] >= 1

        # Combine source=instance + key=FILE_SIZE → no match, because FILE_SIZE is "system"
        instance_file_size_response = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "field": "metadata.source",
                            "operator": "is",
                            "value": "instance",
                        },
                        {
                            "field": "metadata.key",
                            "operator": "is",
                            "value": "FILE_SIZE",
                        },
                    ],
                },
            },
            format="json",
        )

        assert instance_file_size_response.status_code == status.HTTP_200_OK
        assert instance_file_size_response.data["count"] == 0

    def test_string_contains_with_empty_value_is_rejected(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "conditions": [
                        {
                            "field": "name",
                            "operator": "contains",
                            "value": "",
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "name" in str(response.data)

    def test_identifier_in_with_empty_list_is_rejected(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "conditions": [
                        {
                            "field": "project_id",
                            "operator": "in",
                            "value": [],
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "project_id" in str(response.data)

    def test_datetime_with_null_value_is_rejected(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "conditions": [
                        {
                            "field": "created_at",
                            "operator": "gt",
                            "value": None,
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "created_at" in str(response.data)

    def test_string_is_empty_operator_passes_without_value_key(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": ["folder"],
                "group": {
                    "conditions": [
                        {
                            "field": "description",
                            "operator": "is_empty",
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

    def test_boolean_false_value_is_not_treated_as_empty(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "conditions": [
                        {
                            "field": "is_published",
                            "operator": "is",
                            "value": False,
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

    def _make_dataset_with_metadata_rows(self, name, rows):
        dataset = UploadsDataset.objects.create(name=name, folder=self.admin_folder)
        version = UploadsVersion.objects.create(name=f"{name} v1", dataset=dataset)
        for custom_key, value, field_type in rows:
            set_metadata(
                assigned_to_content_type=version.get_content_type(),
                assigned_to_object_id=version.pk,
                custom_key=custom_key,
                value=str(value),
                field_type=field_type,
            )
        return dataset

    def test_two_cluster_and_on_dataset_metadata_requires_separate_row_bindings(self, client):
        url = reverse("search-global")

        both_match = self._make_dataset_with_metadata_rows(
            "Both Conditions Dataset",
            [
                ("temperature", 35, MetadataFieldType.INTEGER),
                ("humidity", 45, MetadataFieldType.INTEGER),
            ],
        )
        self._make_dataset_with_metadata_rows(
            "Temperature Only Dataset",
            [("temperature", 35, MetadataFieldType.INTEGER)],
        )
        self._make_dataset_with_metadata_rows(
            "Humidity Only Dataset",
            [("humidity", 45, MetadataFieldType.INTEGER)],
        )

        response = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "kind": "group",
                            "operator": "AND",
                            "conditions": [
                                {
                                    "field": "metadata.key",
                                    "operator": "is",
                                    "value": "temperature",
                                },
                                {
                                    "field": "metadata.value",
                                    "operator": "gt",
                                    "value": 30,
                                    "field_type": MetadataFieldType.INTEGER,
                                },
                            ],
                        },
                        {
                            "kind": "group",
                            "operator": "AND",
                            "conditions": [
                                {
                                    "field": "metadata.key",
                                    "operator": "is",
                                    "value": "humidity",
                                },
                                {
                                    "field": "metadata.value",
                                    "operator": "gt",
                                    "value": 40,
                                    "field_type": MetadataFieldType.INTEGER,
                                },
                            ],
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        pks = {result["item"]["pk"] for result in response.data["results"]}
        assert str(both_match.pk) in pks
        assert pks == {str(both_match.pk)}

    def test_two_cluster_or_on_dataset_metadata_returns_union(self, client):
        url = reverse("search-global")

        both_match = self._make_dataset_with_metadata_rows(
            "Both Conditions OR",
            [
                ("temperature", 35, MetadataFieldType.INTEGER),
                ("humidity", 45, MetadataFieldType.INTEGER),
            ],
        )
        temp_only = self._make_dataset_with_metadata_rows(
            "Temperature Only OR",
            [("temperature", 35, MetadataFieldType.INTEGER)],
        )
        humidity_only = self._make_dataset_with_metadata_rows(
            "Humidity Only OR",
            [("humidity", 45, MetadataFieldType.INTEGER)],
        )
        self._make_dataset_with_metadata_rows(
            "Neither OR",
            [("temperature", 10, MetadataFieldType.INTEGER)],
        )

        response = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "operator": "OR",
                    "conditions": [
                        {
                            "kind": "group",
                            "operator": "AND",
                            "conditions": [
                                {"field": "metadata.key", "operator": "is", "value": "temperature"},
                                {
                                    "field": "metadata.value",
                                    "operator": "gt",
                                    "value": 30,
                                    "field_type": MetadataFieldType.INTEGER,
                                },
                            ],
                        },
                        {
                            "kind": "group",
                            "operator": "AND",
                            "conditions": [
                                {"field": "metadata.key", "operator": "is", "value": "humidity"},
                                {
                                    "field": "metadata.value",
                                    "operator": "gt",
                                    "value": 40,
                                    "field_type": MetadataFieldType.INTEGER,
                                },
                            ],
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        pks = {result["item"]["pk"] for result in response.data["results"]}
        assert pks == {str(both_match.pk), str(temp_only.pk), str(humidity_only.pk)}

    def test_three_cluster_and_on_dataset_metadata(self, client):
        url = reverse("search-global")

        all_three = self._make_dataset_with_metadata_rows(
            "Three Clusters Match",
            [
                ("temperature", 35, MetadataFieldType.INTEGER),
                ("humidity", 45, MetadataFieldType.INTEGER),
                ("pressure", 1010, MetadataFieldType.INTEGER),
            ],
        )
        self._make_dataset_with_metadata_rows(
            "Two Of Three",
            [
                ("temperature", 35, MetadataFieldType.INTEGER),
                ("humidity", 45, MetadataFieldType.INTEGER),
            ],
        )

        def cluster(key, value):
            return {
                "kind": "group",
                "operator": "AND",
                "conditions": [
                    {"field": "metadata.key", "operator": "is", "value": key},
                    {
                        "field": "metadata.value",
                        "operator": "gt",
                        "value": value,
                        "field_type": MetadataFieldType.INTEGER,
                    },
                ],
            }

        response = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        cluster("temperature", 30),
                        cluster("humidity", 40),
                        cluster("pressure", 1000),
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        pks = {result["item"]["pk"] for result in response.data["results"]}
        assert pks == {str(all_three.pk)}

    def test_metadata_only_group_with_nested_clusters_uses_separate_row_bindings(self, client):
        url = reverse("search-global")

        both_match = self._make_dataset_with_metadata_rows(
            "Nested Metadata Group Match",
            [
                ("temperature", 35, MetadataFieldType.INTEGER),
                ("humidity", 45, MetadataFieldType.INTEGER),
            ],
        )
        self._make_dataset_with_metadata_rows(
            "Nested Metadata Group One Row",
            [("temperature", 35, MetadataFieldType.INTEGER)],
        )

        def cluster(key, threshold):
            return {
                "kind": "group",
                "operator": "AND",
                "conditions": [
                    {"field": "metadata.key", "operator": "is", "value": key},
                    {
                        "field": "metadata.value",
                        "operator": "gt",
                        "value": threshold,
                        "field_type": MetadataFieldType.INTEGER,
                    },
                ],
            }

        response = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {
                            "kind": "group",
                            "operator": "AND",
                            "conditions": [
                                cluster("temperature", 30),
                                cluster("humidity", 40),
                            ],
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        pks = {result["item"]["pk"] for result in response.data["results"]}
        assert pks == {str(both_match.pk)}

    def test_nested_cluster_combines_with_non_metadata_rule(self, client):
        url = reverse("search-global")

        target = self._make_dataset_with_metadata_rows(
            "Mixed Filter Target",
            [
                ("temperature", 35, MetadataFieldType.INTEGER),
                ("humidity", 45, MetadataFieldType.INTEGER),
            ],
        )
        # Distractor: matches metadata, wrong name.
        self._make_dataset_with_metadata_rows(
            "Other Name",
            [
                ("temperature", 35, MetadataFieldType.INTEGER),
                ("humidity", 45, MetadataFieldType.INTEGER),
            ],
        )

        response = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "operator": "AND",
                    "conditions": [
                        {"field": "name", "operator": "contains", "value": "Mixed Filter"},
                        {
                            "kind": "group",
                            "operator": "AND",
                            "conditions": [
                                {"field": "metadata.key", "operator": "is", "value": "temperature"},
                                {
                                    "field": "metadata.value",
                                    "operator": "gt",
                                    "value": 30,
                                    "field_type": MetadataFieldType.INTEGER,
                                },
                            ],
                        },
                        {
                            "kind": "group",
                            "operator": "AND",
                            "conditions": [
                                {"field": "metadata.key", "operator": "is", "value": "humidity"},
                                {
                                    "field": "metadata.value",
                                    "operator": "gt",
                                    "value": 40,
                                    "field_type": MetadataFieldType.INTEGER,
                                },
                            ],
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        pks = {result["item"]["pk"] for result in response.data["results"]}
        assert pks == {str(target.pk)}

    def test_whitespace_string_value_passes_through(self, client):
        url = reverse("search-global")

        response = client.post(
            url,
            {
                "result_types": ["dataset"],
                "group": {
                    "conditions": [
                        {
                            "field": "name",
                            "operator": "contains",
                            "value": " ",
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
