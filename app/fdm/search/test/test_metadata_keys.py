import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound

from fdm.core.helpers import set_request_for_user
from fdm.folders.models import FolderPermission
from fdm.metadata.enums import MetadataFieldType
from fdm.metadata.models import (
    MetadataField,
    MetadataTemplate,
    MetadataTemplateContainer,
    MetadataTemplateField,
)
from fdm.projects.models import Project, ProjectMembership
from fdm.search.metadata_keys.explicit_resolver import ExplicitTemplateResolver
from fdm.search.metadata_keys.extractor import extract_keys
from fdm.uploads.models import UploadsDataset


@pytest.mark.django_db
class TestExplicitTemplateResolver:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        self.user_1 = initial_users["user_1"]
        self.user_2 = initial_users["user_2"]
        set_request_for_user(self.user_1)

    def _make_global_container(self, *, name: str = "Global Container") -> MetadataTemplateContainer:
        container = MetadataTemplateContainer.objects.create(name=name)
        MetadataTemplate.objects.create(
            metadata_template_container=container,
            name=f"{name} v1",
        )
        return container

    def test_single_viewable_global_container_resolves_to_latest_template(self):
        container = self._make_global_container()

        result = ExplicitTemplateResolver().resolve([container.pk])

        assert result == [(container.latest_metadata_template, "template", container.pk)]

    def test_empty_input_returns_empty_list(self):
        assert ExplicitTemplateResolver().resolve([]) == []

    def test_project_assigned_template_viewable_to_project_member(self):
        project = Project.objects.create(name="User 1 Project")
        container = MetadataTemplateContainer.objects.create(
            name="Project-Owned Template",
            assigned_to_content_type=project.get_content_type(),
            assigned_to_object_id=project.pk,
        )
        template = MetadataTemplate.objects.create(
            metadata_template_container=container,
            name="v1",
        )

        result = ExplicitTemplateResolver().resolve([container.pk])

        assert result == [(template, "template", container.pk)]

    def test_template_assigned_to_project_user_is_not_member_of_raises_not_found(self):
        set_request_for_user(self.user_2)
        hidden_project = Project.objects.create(name="User 2 Hidden Project")
        hidden_container = MetadataTemplateContainer.objects.create(
            name="Hidden Project Template",
            assigned_to_content_type=hidden_project.get_content_type(),
            assigned_to_object_id=hidden_project.pk,
        )
        MetadataTemplate.objects.create(
            metadata_template_container=hidden_container,
            name="Hidden Project Template v1",
        )
        set_request_for_user(self.user_1)

        with pytest.raises(NotFound):
            ExplicitTemplateResolver().resolve([hidden_container.pk])

    def test_nonexistent_template_id_raises_not_found(self):
        with pytest.raises(NotFound):
            ExplicitTemplateResolver().resolve(
                ["00000000-0000-0000-0000-000000000000"],
            )

    def test_container_without_latest_template_is_silently_skipped(self):
        empty_container = MetadataTemplateContainer.objects.create(name="Empty")
        populated = self._make_global_container(name="Populated")

        result = ExplicitTemplateResolver().resolve([empty_container.pk, populated.pk])

        assert result == [(populated.latest_metadata_template, "template", populated.pk)]

    def test_multiple_ids_preserve_input_order(self):
        first = self._make_global_container(name="First")
        second = self._make_global_container(name="Second")

        result_a = ExplicitTemplateResolver().resolve([first.pk, second.pk])
        result_b = ExplicitTemplateResolver().resolve([second.pk, first.pk])

        assert [pair[2] for pair in result_a] == [first.pk, second.pk]
        assert [pair[2] for pair in result_b] == [second.pk, first.pk]


@pytest.mark.django_db
class TestExtractKeys:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])
        self.container = MetadataTemplateContainer.objects.create(name="Container")
        self.template = MetadataTemplate.objects.create(
            metadata_template_container=self.container,
            name="Template",
        )

    def test_custom_key_field_returns_one_row_with_expected_shape(self):
        MetadataTemplateField.objects.create(
            metadata_template=self.template,
            custom_key="SampleCount",
            field_type=MetadataFieldType.INTEGER,
            order=0,
        )

        rows = extract_keys(self.template, "project", "11111111-1111-1111-1111-111111111111")

        assert rows == [
            {
                "key": "SampleCount",
                "field_type": MetadataFieldType.INTEGER,
                "source": "project",
                "source_id": "11111111-1111-1111-1111-111111111111",
                "is_linked": False,
                "order": 0,
            },
        ]

    def test_rows_are_ordered_by_template_field_order(self):
        MetadataTemplateField.objects.create(
            metadata_template=self.template,
            custom_key="Second",
            field_type=MetadataFieldType.TEXT,
            order=10,
        )
        MetadataTemplateField.objects.create(
            metadata_template=self.template,
            custom_key="First",
            field_type=MetadataFieldType.TEXT,
            order=0,
        )

        rows = extract_keys(self.template, "project", "11111111-1111-1111-1111-111111111111")

        assert [row["key"] for row in rows] == ["First", "Second"]

    def test_linked_field_uses_field_key_and_marks_is_linked(self):
        linked_field = MetadataField.objects.create(
            key="Author",
            field_type=MetadataFieldType.TEXT,
        )
        MetadataTemplateField.objects.create(
            metadata_template=self.template,
            field=linked_field,
            field_type=MetadataFieldType.TEXT,
            order=0,
        )

        rows = extract_keys(self.template, "folder", "22222222-2222-2222-2222-222222222222")

        assert rows == [
            {
                "key": "Author",
                "field_type": MetadataFieldType.TEXT,
                "source": "folder",
                "source_id": "22222222-2222-2222-2222-222222222222",
                "is_linked": True,
                "order": 0,
            },
        ]


@pytest.mark.django_db
class TestMetadataKeysEndpoint:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        self.user_1 = initial_users["user_1"]
        self.user_2 = initial_users["user_2"]
        set_request_for_user(self.user_1)

        self.project = Project.objects.create(name="Horizon 2.5")
        self.project_container = MetadataTemplateContainer.objects.create(
            name="Horizon Project Template",
            assigned_to_content_type=self.project.get_content_type(),
            assigned_to_object_id=self.project.pk,
        )
        self.project_template = MetadataTemplate.objects.create(
            metadata_template_container=self.project_container,
            name="Horizon Project Template v1",
        )
        self.linked_field = MetadataField.objects.create(
            key="Author",
            field_type=MetadataFieldType.TEXT,
        )
        MetadataTemplateField.objects.create(
            metadata_template=self.project_template,
            field=self.linked_field,
            field_type=MetadataFieldType.TEXT,
            order=0,
        )
        MetadataTemplateField.objects.create(
            metadata_template=self.project_template,
            custom_key="SampleCount",
            field_type=MetadataFieldType.INTEGER,
            order=1,
        )
        self.project.metadata_template = self.project_template
        self.project.save()

    def _attach_folder_active_template(self, folder, *, custom_keys: list[tuple[str, str]]):
        container = MetadataTemplateContainer.objects.create(
            name=f"Folder Template for {folder.pk}",
            assigned_to_content_type=folder.get_content_type(),
            assigned_to_object_id=folder.pk,
        )
        template = MetadataTemplate.objects.create(
            metadata_template_container=container,
            name=f"Folder Template for {folder.pk} v1",
        )
        for order, (key, ft) in enumerate(custom_keys):
            MetadataTemplateField.objects.create(
                metadata_template=template,
                custom_key=key,
                field_type=ft,
                order=order,
            )
        folder.metadata_template = template
        folder.save()
        return template

    def test_folder_id_returns_project_then_folder_rows(self, client):
        folder = self.project.folders.first()
        self._attach_folder_active_template(
            folder,
            custom_keys=[
                ("ExperimentDate", MetadataFieldType.DATE),
                ("Notes", MetadataFieldType.TEXT),
            ],
        )
        url = reverse("search-metadata-keys")

        response = client.get(url, {"folder_id": str(folder.pk)})

        assert response.status_code == status.HTTP_200_OK
        assert response.data == {
            "results": [
                {
                    "key": "Author",
                    "field_type": MetadataFieldType.TEXT,
                    "source": "project",
                    "source_id": str(self.project.pk),
                    "is_linked": True,
                },
                {
                    "key": "SampleCount",
                    "field_type": MetadataFieldType.INTEGER,
                    "source": "project",
                    "source_id": str(self.project.pk),
                    "is_linked": False,
                },
                {
                    "key": "ExperimentDate",
                    "field_type": MetadataFieldType.DATE,
                    "source": "folder",
                    "source_id": str(folder.pk),
                    "is_linked": False,
                },
                {
                    "key": "Notes",
                    "field_type": MetadataFieldType.TEXT,
                    "source": "folder",
                    "source_id": str(folder.pk),
                    "is_linked": False,
                },
            ],
        }

    def test_same_key_on_folder_and_project_returns_two_rows_with_different_types(self, client):
        folder = self.project.folders.first()
        self._attach_folder_active_template(
            folder,
            custom_keys=[("SampleCount", MetadataFieldType.TEXT)],
        )
        url = reverse("search-metadata-keys")

        response = client.get(url, {"folder_id": str(folder.pk)})

        assert response.status_code == status.HTTP_200_OK
        sample_count_rows = [row for row in response.data["results"] if row["key"] == "SampleCount"]
        assert sample_count_rows == [
            {
                "key": "SampleCount",
                "field_type": MetadataFieldType.INTEGER,
                "source": "project",
                "source_id": str(self.project.pk),
                "is_linked": False,
            },
            {
                "key": "SampleCount",
                "field_type": MetadataFieldType.TEXT,
                "source": "folder",
                "source_id": str(folder.pk),
                "is_linked": False,
            },
        ]

    def _attach_dataset_active_template(self, dataset, *, custom_keys: list[tuple[str, str]]):
        container = MetadataTemplateContainer.objects.create(
            name=f"Dataset Template for {dataset.pk}",
            assigned_to_content_type=dataset.get_content_type(),
            assigned_to_object_id=dataset.pk,
        )
        template = MetadataTemplate.objects.create(
            metadata_template_container=container,
            name=f"Dataset Template for {dataset.pk} v1",
        )
        for order, (key, ft) in enumerate(custom_keys):
            MetadataTemplateField.objects.create(
                metadata_template=template,
                custom_key=key,
                field_type=ft,
                order=order,
            )
        dataset.metadata_template = template
        dataset.save()
        return template

    def test_dataset_id_returns_project_folder_dataset_in_that_order(self, client):
        folder = self.project.folders.first()
        self._attach_folder_active_template(
            folder,
            custom_keys=[("Notes", MetadataFieldType.TEXT)],
        )
        dataset = UploadsDataset.objects.create(name="Experiment Dataset", folder=folder)
        self._attach_dataset_active_template(
            dataset,
            custom_keys=[("CapturedAt", MetadataFieldType.DATE)],
        )
        url = reverse("search-metadata-keys")

        response = client.get(url, {"dataset_id": str(dataset.pk)})

        assert response.status_code == status.HTTP_200_OK
        assert [(row["key"], row["source"]) for row in response.data["results"]] == [
            ("Author", "project"),
            ("SampleCount", "project"),
            ("Notes", "folder"),
            ("CapturedAt", "dataset"),
        ]

    def test_dataset_id_missing_returns_404(self, client):
        url = reverse("search-metadata-keys")

        response = client.get(url, {"dataset_id": "00000000-0000-0000-0000-000000000000"})

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_unpublished_dataset_accessible_to_creator(self, client):
        folder = self.project.folders.first()
        dataset = UploadsDataset.objects.create(name="Draft", folder=folder)
        self._attach_dataset_active_template(
            dataset,
            custom_keys=[("CapturedAt", MetadataFieldType.DATE)],
        )
        url = reverse("search-metadata-keys")

        response = client.get(url, {"dataset_id": str(dataset.pk)})

        assert response.status_code == status.HTTP_200_OK
        dataset_rows = [row for row in response.data["results"] if row["source"] == "dataset"]
        assert [row["key"] for row in dataset_rows] == ["CapturedAt"]

    def test_published_dataset_accessible_to_folder_member_even_when_not_creator(self, client):
        set_request_for_user(self.user_2)
        u2_project = Project.objects.create(name="User 2 Project for Published Test")
        u2_folder = u2_project.folders.first()
        u2_dataset = UploadsDataset.objects.create(name="Published U2", folder=u2_folder)
        u2_dataset.publication_date = timezone.now()
        u2_dataset.save()

        membership = ProjectMembership.objects.create(
            project=u2_project,
            member=self.user_1,
            is_project_admin=False,
        )
        FolderPermission.objects.create(
            folder=u2_folder,
            project_membership=membership,
            is_folder_admin=False,
            can_edit=False,
        )
        self._attach_dataset_active_template(
            u2_dataset,
            custom_keys=[("CapturedAt", MetadataFieldType.DATE)],
        )
        set_request_for_user(self.user_1)
        url = reverse("search-metadata-keys")

        response = client.get(url, {"dataset_id": str(u2_dataset.pk)})

        assert response.status_code == status.HTTP_200_OK
        assert any(row["key"] == "CapturedAt" and row["source"] == "dataset" for row in response.data["results"])

    def test_published_dataset_denied_for_non_folder_member(self, client):
        set_request_for_user(self.user_2)
        u2_project = Project.objects.create(name="User 2 Hidden Project")
        u2_folder = u2_project.folders.first()
        u2_dataset = UploadsDataset.objects.create(name="Hidden Published", folder=u2_folder)
        u2_dataset.publication_date = timezone.now()
        u2_dataset.save()
        set_request_for_user(self.user_1)
        url = reverse("search-metadata-keys")

        response = client.get(url, {"dataset_id": str(u2_dataset.pk)})

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_unpublished_dataset_creator_without_ancestor_access_only_sees_dataset_keys(self, client):
        # user_1 is the creator (so passes _can_view_dataset) but has no ProjectMembership /
        # FolderPermission on the parent project. Ancestor template keys must be hidden.
        set_request_for_user(self.user_2)
        u2_project = Project.objects.create(name="Other User's Project")
        u2_folder = u2_project.folders.first()
        set_request_for_user(self.user_1)
        draft = UploadsDataset.objects.create(name="My Draft Elsewhere", folder=u2_folder)
        self._attach_dataset_active_template(
            draft,
            custom_keys=[("CapturedAt", MetadataFieldType.DATE)],
        )
        url = reverse("search-metadata-keys")

        response = client.get(url, {"dataset_id": str(draft.pk)})

        assert response.status_code == status.HTTP_200_OK
        assert {row["source"] for row in response.data["results"]} == {"dataset"}

    def test_dataset_id_with_invalid_uuid_returns_400(self, client):
        url = reverse("search-metadata-keys")

        response = client.get(url, {"dataset_id": "not-a-uuid"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_unpublished_dataset_denied_for_non_creator_without_folder_access(self, client):
        set_request_for_user(self.user_2)
        u2_project = Project.objects.create(name="User 2 Project")
        u2_folder = u2_project.folders.first()
        u2_dataset = UploadsDataset.objects.create(name="U2 Draft", folder=u2_folder)
        set_request_for_user(self.user_1)
        url = reverse("search-metadata-keys")

        response = client.get(url, {"dataset_id": str(u2_dataset.pk)})

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_folder_id_without_folder_access_returns_403(self, client):
        set_request_for_user(self.user_2)
        hidden_project = Project.objects.create(name="User 2's Hidden Project")
        hidden_folder = hidden_project.folders.first()
        set_request_for_user(self.user_1)
        url = reverse("search-metadata-keys")

        response = client.get(url, {"folder_id": str(hidden_folder.pk)})

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_folder_id_missing_returns_404(self, client):
        url = reverse("search-metadata-keys")

        response = client.get(url, {"folder_id": "00000000-0000-0000-0000-000000000000"})

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_project_id_non_member_returns_403(self, client):
        set_request_for_user(self.user_2)
        hidden_project = Project.objects.create(name="User 2's Hidden Project")
        set_request_for_user(self.user_1)
        url = reverse("search-metadata-keys")

        response = client.get(url, {"project_id": str(hidden_project.pk)})

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_project_id_missing_returns_404(self, client):
        url = reverse("search-metadata-keys")

        response = client.get(url, {"project_id": "00000000-0000-0000-0000-000000000000"})

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_no_scope_param_returns_400(self, client):
        url = reverse("search-metadata-keys")

        response = client.get(url)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        body = str(response.data).lower()
        for name in ("project_id", "folder_id", "dataset_id", "template_id"):
            assert name in body

    def test_project_with_no_active_template_returns_empty_results(self, client):
        empty_project = Project.objects.create(name="Empty Project")
        url = reverse("search-metadata-keys")

        response = client.get(url, {"project_id": str(empty_project.pk)})

        assert response.status_code == status.HTTP_200_OK
        assert response.data == {"results": []}

    def test_project_id_returns_active_template_keys_ordered_by_template_order(self, client):
        url = reverse("search-metadata-keys")

        response = client.get(url, {"project_id": str(self.project.pk)})

        assert response.status_code == status.HTTP_200_OK
        assert response.data == {
            "results": [
                {
                    "key": "Author",
                    "field_type": MetadataFieldType.TEXT,
                    "source": "project",
                    "source_id": str(self.project.pk),
                    "is_linked": True,
                },
                {
                    "key": "SampleCount",
                    "field_type": MetadataFieldType.INTEGER,
                    "source": "project",
                    "source_id": str(self.project.pk),
                    "is_linked": False,
                },
            ],
        }

    def test_multiple_project_ids_returns_rows_from_each_with_distinct_source_ids(self, client):
        second_project = Project.objects.create(name="Second Project")
        second_container = MetadataTemplateContainer.objects.create(
            name="Second Project Template",
            assigned_to_content_type=second_project.get_content_type(),
            assigned_to_object_id=second_project.pk,
        )
        second_template = MetadataTemplate.objects.create(
            metadata_template_container=second_container,
            name="Second Project Template v1",
        )
        MetadataTemplateField.objects.create(
            metadata_template=second_template,
            custom_key="OtherKey",
            field_type=MetadataFieldType.TEXT,
            order=0,
        )
        second_project.metadata_template = second_template
        second_project.save()
        url = reverse("search-metadata-keys")

        response = client.get(
            url,
            {"project_id": [str(self.project.pk), str(second_project.pk)]},
        )

        assert response.status_code == status.HTTP_200_OK
        assert [(row["key"], row["source_id"]) for row in response.data["results"]] == [
            ("Author", str(self.project.pk)),
            ("SampleCount", str(self.project.pk)),
            ("OtherKey", str(second_project.pk)),
        ]

    def test_overlapping_scopes_dedupe_project_keys(self, client):
        folder = self.project.folders.first()
        self._attach_folder_active_template(
            folder,
            custom_keys=[("Notes", MetadataFieldType.TEXT)],
        )
        url = reverse("search-metadata-keys")

        response = client.get(
            url,
            {"project_id": str(self.project.pk), "folder_id": str(folder.pk)},
        )

        assert response.status_code == status.HTTP_200_OK
        project_rows = [row for row in response.data["results"] if row["source"] == "project"]
        # project keys should appear once even though both project_id and folder_id pull them in
        assert [row["key"] for row in project_rows] == ["Author", "SampleCount"]

    def _make_global_template(
        self,
        *,
        name: str,
        custom_keys: list[tuple[str, str]],
    ) -> MetadataTemplateContainer:
        container = MetadataTemplateContainer.objects.create(name=name)
        template = MetadataTemplate.objects.create(
            metadata_template_container=container,
            name=f"{name} v1",
        )
        for order, (key, ft) in enumerate(custom_keys):
            MetadataTemplateField.objects.create(
                metadata_template=template,
                custom_key=key,
                field_type=ft,
                order=order,
            )
        return container

    def test_two_template_ids_union_key_set_against_folder_active_template(self, client):
        folder = self.project.folders.first()
        self._attach_folder_active_template(
            folder,
            custom_keys=[
                ("KeyOnlyInT1", MetadataFieldType.TEXT),
                ("KeyOnlyInT2", MetadataFieldType.TEXT),
                ("FolderOnlyKey", MetadataFieldType.TEXT),
            ],
        )
        t1 = self._make_global_template(
            name="Union T1",
            custom_keys=[("KeyOnlyInT1", MetadataFieldType.INTEGER)],
        )
        t2 = self._make_global_template(
            name="Union T2",
            custom_keys=[("KeyOnlyInT2", MetadataFieldType.DATE)],
        )
        url = reverse("search-metadata-keys")

        response = client.get(
            url,
            {
                "folder_id": str(folder.pk),
                "template_id": [str(t1.pk), str(t2.pk)],
            },
        )

        assert response.status_code == status.HTTP_200_OK
        folder_keys = [row["key"] for row in response.data["results"] if row["source"] == "folder"]
        # Both T1's and T2's keys should be in the union; folder rows for both surface,
        # but the folder-only key drops.
        assert sorted(folder_keys) == ["KeyOnlyInT1", "KeyOnlyInT2"]

    def test_unviewable_template_id_plus_viewable_folder_id_returns_404(self, client):
        folder = self.project.folders.first()
        set_request_for_user(self.user_2)
        hidden_project = Project.objects.create(name="User 2 Hidden Project for 404 Combo")
        hidden_container = MetadataTemplateContainer.objects.create(
            name="Hidden Template For 404 Combo",
            assigned_to_content_type=hidden_project.get_content_type(),
            assigned_to_object_id=hidden_project.pk,
        )
        MetadataTemplate.objects.create(
            metadata_template_container=hidden_container,
            name="Hidden v1",
        )
        set_request_for_user(self.user_1)
        url = reverse("search-metadata-keys")

        response = client.get(
            url,
            {
                "folder_id": str(folder.pk),
                "template_id": str(hidden_container.pk),
            },
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_viewable_template_id_plus_inaccessible_project_id_returns_403(self, client):
        viewable_template = self._make_global_template(
            name="Public Global Template",
            custom_keys=[("Anything", MetadataFieldType.TEXT)],
        )
        set_request_for_user(self.user_2)
        hidden_project = Project.objects.create(name="User 2 Private Project for Precedence Test")
        set_request_for_user(self.user_1)
        url = reverse("search-metadata-keys")

        response = client.get(
            url,
            {
                "template_id": str(viewable_template.pk),
                "project_id": str(hidden_project.pk),
            },
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_dataset_id_plus_template_id_orders_buckets_template_project_folder_dataset(self, client):
        folder = self.project.folders.first()
        self._attach_folder_active_template(
            folder,
            custom_keys=[("SharedKey", MetadataFieldType.TEXT)],
        )
        dataset = UploadsDataset.objects.create(name="Dataset for Ordering", folder=folder)
        self._attach_dataset_active_template(
            dataset,
            custom_keys=[("SharedKey", MetadataFieldType.DATE)],
        )
        # Project's active template already has Author + SampleCount; ensure SharedKey is the
        # only key common to all four levels so the filter retains one row per entity bucket.
        MetadataTemplateField.objects.create(
            metadata_template=self.project_template,
            custom_key="SharedKey",
            field_type=MetadataFieldType.TEXT,
            order=2,
        )
        template_container = self._make_global_template(
            name="Ordering Template",
            custom_keys=[("SharedKey", MetadataFieldType.INTEGER)],
        )
        url = reverse("search-metadata-keys")

        response = client.get(
            url,
            {"dataset_id": str(dataset.pk), "template_id": str(template_container.pk)},
        )

        assert response.status_code == status.HTTP_200_OK
        assert [row["source"] for row in response.data["results"]] == [
            "template",
            "project",
            "folder",
            "dataset",
        ]

    def test_viewable_empty_template_plus_folder_returns_empty_results(self, client):
        # Supplying a template_id whose latest version has no fields establishes an empty
        # key universe; entity rows pulled in by other scopes are filtered against that
        # universe and therefore drop out. Documents intentional behaviour so callers know
        # to omit template_id when they don't want narrowing.
        folder = self.project.folders.first()
        self._attach_folder_active_template(
            folder,
            custom_keys=[("FolderKey", MetadataFieldType.TEXT)],
        )
        empty_container = MetadataTemplateContainer.objects.create(name="Empty Template")
        MetadataTemplate.objects.create(
            metadata_template_container=empty_container,
            name="Empty v1",
        )
        url = reverse("search-metadata-keys")

        response = client.get(
            url,
            {"folder_id": str(folder.pk), "template_id": str(empty_container.pk)},
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data == {"results": []}

    def test_folder_id_plus_template_id_drops_entity_only_keys(self, client):
        folder = self.project.folders.first()
        self._attach_folder_active_template(
            folder,
            custom_keys=[
                ("FolderOnlyKey", MetadataFieldType.TEXT),
                ("SharedKey", MetadataFieldType.TEXT),
            ],
        )
        template_container = self._make_global_template(
            name="Filter Template Drop",
            custom_keys=[
                ("SharedKey", MetadataFieldType.INTEGER),
                ("TemplateOnlyKey", MetadataFieldType.DATE),
            ],
        )
        url = reverse("search-metadata-keys")

        response = client.get(
            url,
            {"folder_id": str(folder.pk), "template_id": str(template_container.pk)},
        )

        assert response.status_code == status.HTTP_200_OK
        assert "FolderOnlyKey" not in {row["key"] for row in response.data["results"]}
        author_rows = [row for row in response.data["results"] if row["key"] == "Author"]
        assert author_rows == []

    def test_folder_id_plus_template_id_emits_overlap_key_twice(self, client):
        folder = self.project.folders.first()
        self._attach_folder_active_template(
            folder,
            custom_keys=[
                ("SharedKey", MetadataFieldType.TEXT),
                ("FolderOnlyKey", MetadataFieldType.TEXT),
            ],
        )
        template_container = self._make_global_template(
            name="Filter Template Overlap",
            custom_keys=[
                ("SharedKey", MetadataFieldType.INTEGER),
                ("TemplateOnlyKey", MetadataFieldType.DATE),
            ],
        )
        url = reverse("search-metadata-keys")

        response = client.get(
            url,
            {"folder_id": str(folder.pk), "template_id": str(template_container.pk)},
        )

        assert response.status_code == status.HTTP_200_OK
        shared_rows = [row for row in response.data["results"] if row["key"] == "SharedKey"]
        assert len(shared_rows) == 2
        assert {row["source"] for row in shared_rows} == {"template", "folder"}
        assert {(row["source"], row["source_id"]) for row in shared_rows} == {
            ("template", str(template_container.pk)),
            ("folder", str(folder.pk)),
        }

    def test_multiple_template_ids_preserve_input_order(self, client):
        first = self._make_global_template(
            name="First Template",
            custom_keys=[("KeyFromFirst", MetadataFieldType.TEXT)],
        )
        second = self._make_global_template(
            name="Second Template",
            custom_keys=[("KeyFromSecond", MetadataFieldType.TEXT)],
        )
        url = reverse("search-metadata-keys")

        response = client.get(
            url,
            {"template_id": [str(first.pk), str(second.pk)]},
        )

        assert response.status_code == status.HTTP_200_OK
        assert [(row["key"], row["source_id"]) for row in response.data["results"]] == [
            ("KeyFromFirst", str(first.pk)),
            ("KeyFromSecond", str(second.pk)),
        ]

    def test_unviewable_template_id_returns_404(self, client):
        set_request_for_user(self.user_2)
        hidden_project = Project.objects.create(name="User 2 Hidden Project for Template Auth")
        hidden_container = MetadataTemplateContainer.objects.create(
            name="Hidden Project Template for Auth",
            assigned_to_content_type=hidden_project.get_content_type(),
            assigned_to_object_id=hidden_project.pk,
        )
        MetadataTemplate.objects.create(
            metadata_template_container=hidden_container,
            name="Hidden v1",
        )
        set_request_for_user(self.user_1)
        url = reverse("search-metadata-keys")

        response = client.get(url, {"template_id": str(hidden_container.pk)})

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_nonexistent_template_id_returns_404(self, client):
        url = reverse("search-metadata-keys")

        response = client.get(
            url,
            {"template_id": "00000000-0000-0000-0000-000000000000"},
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_template_id_alone_returns_template_sourced_rows(self, client):
        container = self._make_global_template(
            name="Standalone Template",
            custom_keys=[
                ("CapturedAt", MetadataFieldType.DATE),
                ("Notes", MetadataFieldType.TEXT),
            ],
        )
        url = reverse("search-metadata-keys")

        response = client.get(url, {"template_id": str(container.pk)})

        assert response.status_code == status.HTTP_200_OK
        assert response.data == {
            "results": [
                {
                    "key": "CapturedAt",
                    "field_type": MetadataFieldType.DATE,
                    "source": "template",
                    "source_id": str(container.pk),
                    "is_linked": False,
                },
                {
                    "key": "Notes",
                    "field_type": MetadataFieldType.TEXT,
                    "source": "template",
                    "source_id": str(container.pk),
                    "is_linked": False,
                },
            ],
        }
