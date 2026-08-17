import pytest
from django.urls import reverse
from rest_framework import status

from fdm.core.helpers import set_request_for_user
from fdm.metadata.enums import MetadataFieldType
from fdm.metadata.helpers import set_metadata
from fdm.metadata.models import MetadataTemplate, MetadataTemplateContainer, MetadataTemplateField
from fdm.projects.models import Project
from fdm.search.metadata_choice_options.service import _choice_fields_by_template_and_key
from fdm.uploads.models import UploadsDataset


@pytest.mark.django_db
class TestMetadataChoiceOptionsEndpoint:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        self.user_1 = initial_users["user_1"]
        set_request_for_user(self.user_1)

    @staticmethod
    def _template(name, *, assigned_to=None):
        container_kwargs = {"name": name}
        if assigned_to is not None:
            container_kwargs.update(
                assigned_to_content_type=assigned_to.get_content_type(),
                assigned_to_object_id=assigned_to.pk,
            )
        container = MetadataTemplateContainer.objects.create(**container_kwargs)
        template = MetadataTemplate.objects.create(
            metadata_template_container=container,
            name=f"{name} v1",
        )
        return container, template

    @staticmethod
    def _field(template, key, field_type, options, *, order=0):
        return MetadataTemplateField.objects.create(
            metadata_template=template,
            custom_key=key,
            field_type=field_type,
            value={"value": None if field_type == MetadataFieldType.SELECTION else []},
            config={"options": options},
            order=order,
        )

    def test_requires_bounded_keys_and_choice_field_type(self, client):
        url = reverse("search-metadata-choice-options")

        missing_key = client.get(url, {"field_type": MetadataFieldType.SELECTION})
        invalid_type = client.get(url, {"key": "Status", "field_type": MetadataFieldType.TEXT})

        assert missing_key.status_code == status.HTTP_400_BAD_REQUEST
        assert invalid_type.status_code == status.HTTP_400_BAD_REQUEST

    def test_choice_fields_for_all_keys_and_templates_are_loaded_in_one_query(self, django_assert_num_queries):
        _, first_template = self._template("First batch")
        _, second_template = self._template("Second batch")
        first_field = self._field(first_template, "Status", MetadataFieldType.SELECTION, ["Open"])
        second_field = self._field(second_template, "Priority", MetadataFieldType.SELECTION, ["High"])

        with django_assert_num_queries(1):
            fields = _choice_fields_by_template_and_key(
                ["Status", "Priority"],
                MetadataFieldType.SELECTION,
                [first_template.pk, second_template.pk],
            )

        assert fields[(first_template.pk, "status")] == [first_field]
        assert fields[(second_template.pk, "priority")] == [second_field]

    def test_keyless_scope_searches_viewable_definitions_and_preserves_duplicate_literals(self, client):
        first_container, first_template = self._template("First global")
        second_container, second_template = self._template("Second global")
        first_field = self._field(
            first_template,
            "Status",
            MetadataFieldType.SELECTION,
            ["Open", "Closed"],
        )
        second_field = self._field(
            second_template,
            "Status",
            MetadataFieldType.SELECTION,
            ["Open", "Archived"],
        )
        self._field(second_template, "Status", MetadataFieldType.TAG, ["wrong type"], order=1)

        response = client.get(
            reverse("search-metadata-choice-options"),
            [("key", "Status"), ("field_type", MetadataFieldType.SELECTION)],
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data == {
            "results": [
                {
                    "key": "Status",
                    "field_type": MetadataFieldType.SELECTION,
                    "source": "template",
                    "source_id": str(first_container.pk),
                    "metadata_template_id": str(first_template.pk),
                    "metadata_template_name": first_template.name,
                    "metadata_template_container_name": first_container.name,
                    "metadata_template_field_id": str(first_field.pk),
                    "source_name": first_container.name,
                    "options": ["Open", "Closed"],
                },
                {
                    "key": "Status",
                    "field_type": MetadataFieldType.SELECTION,
                    "source": "template",
                    "source_id": str(second_container.pk),
                    "metadata_template_id": str(second_template.pk),
                    "metadata_template_name": second_template.name,
                    "metadata_template_container_name": second_container.name,
                    "metadata_template_field_id": str(second_field.pk),
                    "source_name": second_container.name,
                    "options": ["Open", "Archived"],
                },
            ],
        }

    def test_project_scope_returns_matching_active_definition_only(self, client):
        project = Project.objects.create(name="Scoped project")
        container, template = self._template("Project choices", assigned_to=project)
        template.name = None
        template.save(update_fields=["name"])
        field = self._field(template, "Status", MetadataFieldType.SELECTION, ["Project Open"])
        project.metadata_template = template
        project.save()
        _, unrelated_template = self._template("Unrelated global")
        self._field(unrelated_template, "Status", MetadataFieldType.SELECTION, ["Global Open"])

        response = client.get(
            reverse("search-metadata-choice-options"),
            [
                ("key", "Status"),
                ("field_type", MetadataFieldType.SELECTION),
                ("project_id", str(project.pk)),
            ],
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["results"] == [
            {
                "key": "Status",
                "field_type": MetadataFieldType.SELECTION,
                "source": "project",
                "source_id": str(project.pk),
                "metadata_template_id": str(template.pk),
                "metadata_template_name": None,
                "metadata_template_container_name": container.name,
                "metadata_template_field_id": str(field.pk),
                "source_name": project.name,
                "options": ["Project Open"],
            },
        ]
        assert response.data["results"][0]["source_id"] != str(container.pk)

    def test_repeated_keys_are_grouped_in_request_order(self, client):
        _, template = self._template("Several keys")
        labels = self._field(template, "Labels", MetadataFieldType.TAG, ["urgent"], order=0)
        risks = self._field(template, "Risks", MetadataFieldType.TAG, ["high"], order=1)

        response = client.get(
            reverse("search-metadata-choice-options"),
            [
                ("key", "Risks"),
                ("key", "Labels"),
                ("field_type", MetadataFieldType.TAG),
            ],
        )

        assert response.status_code == status.HTTP_200_OK
        assert [result["key"] for result in response.data["results"]] == ["Risks", "Labels"]
        assert [result["metadata_template_field_id"] for result in response.data["results"]] == [
            str(risks.pk),
            str(labels.pk),
        ]

    def test_inaccessible_entity_scope_returns_forbidden(self, client, initial_users):
        set_request_for_user(initial_users["user_2"])
        hidden_project = Project.objects.create(name="Hidden project")
        set_request_for_user(self.user_1)

        response = client.get(
            reverse("search-metadata-choice-options"),
            {
                "key": "Status",
                "field_type": MetadataFieldType.SELECTION,
                "project_id": str(hidden_project.pk),
            },
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_missing_or_inaccessible_explicit_template_returns_not_found(self, client, initial_users):
        set_request_for_user(initial_users["user_2"])
        hidden_project = Project.objects.create(name="Hidden template project")
        hidden_container, hidden_template = self._template("Hidden choices", assigned_to=hidden_project)
        self._field(hidden_template, "Status", MetadataFieldType.SELECTION, ["Secret"])
        set_request_for_user(self.user_1)

        missing_response = client.get(
            reverse("search-metadata-choice-options"),
            {
                "key": "Status",
                "field_type": MetadataFieldType.SELECTION,
                "template_id": "00000000-0000-0000-0000-000000000000",
            },
        )
        hidden_response = client.get(
            reverse("search-metadata-choice-options"),
            {
                "key": "Status",
                "field_type": MetadataFieldType.SELECTION,
                "template_id": str(hidden_container.pk),
            },
        )

        assert missing_response.status_code == status.HTTP_404_NOT_FOUND
        assert hidden_response.status_code == status.HTTP_404_NOT_FOUND

    def test_one_invalid_scope_fails_the_complete_request(self, client, initial_users):
        viewable_container, viewable_template = self._template("Viewable choices")
        self._field(viewable_template, "Status", MetadataFieldType.SELECTION, ["Visible"])
        set_request_for_user(initial_users["user_2"])
        hidden_project = Project.objects.create(name="Hidden scope")
        set_request_for_user(self.user_1)

        response = client.get(
            reverse("search-metadata-choice-options"),
            {
                "key": "Status",
                "field_type": MetadataFieldType.SELECTION,
                "template_id": str(viewable_container.pk),
                "project_id": str(hidden_project.pk),
            },
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_missing_entity_scope_returns_not_found(self, client):
        response = client.get(
            reverse("search-metadata-choice-options"),
            {
                "key": "Status",
                "field_type": MetadataFieldType.SELECTION,
                "project_id": "00000000-0000-0000-0000-000000000000",
            },
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_combined_scopes_preserve_each_definition_group(self, client):
        project = Project.objects.create(name="Combined project")
        _, project_template = self._template("Combined project choices", assigned_to=project)
        self._field(project_template, "Status", MetadataFieldType.SELECTION, ["Project"])
        project.metadata_template = project_template
        project.save()
        global_container, global_template = self._template("Combined global choices")
        self._field(global_template, "Status", MetadataFieldType.SELECTION, ["Global"])

        response = client.get(
            reverse("search-metadata-choice-options"),
            [
                ("key", "Status"),
                ("field_type", MetadataFieldType.SELECTION),
                ("template_id", str(global_container.pk)),
                ("project_id", str(project.pk)),
            ],
        )

        assert response.status_code == status.HTTP_200_OK
        assert [(result["source"], result["options"]) for result in response.data["results"]] == [
            ("template", ["Global"]),
            ("project", ["Project"]),
        ]

    def test_empty_catalog_does_not_fall_back_to_observed_metadata_values(self, client):
        project = Project.objects.create(name="Observed values")
        set_metadata(
            assigned_to_content_type=project.get_content_type(),
            assigned_to_object_id=project.pk,
            custom_key="Observed status",
            field_type=MetadataFieldType.SELECTION,
            value="Production value",
            config={"options": ["Production value"]},
        )

        response = client.get(
            reverse("search-metadata-choice-options"),
            {
                "key": "Observed status",
                "field_type": MetadataFieldType.SELECTION,
                "project_id": str(project.pk),
            },
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data == {"results": []}

    def test_dataset_creator_without_ancestor_access_only_sees_dataset_definition(self, client, initial_users):
        set_request_for_user(initial_users["user_2"])
        hidden_project = Project.objects.create(name="Hidden ancestor project")
        hidden_folder = hidden_project.folders.first()
        project_container, project_template = self._template("Project choices", assigned_to=hidden_project)
        self._field(project_template, "Status", MetadataFieldType.SELECTION, ["Project secret"])
        hidden_project.metadata_template = project_template
        hidden_project.save()
        folder_container, folder_template = self._template("Folder choices", assigned_to=hidden_folder)
        self._field(folder_template, "Status", MetadataFieldType.SELECTION, ["Folder secret"])
        hidden_folder.metadata_template = folder_template
        hidden_folder.save()

        set_request_for_user(self.user_1)
        dataset = UploadsDataset.objects.create(name=None, display_name="owned-dataset.dcm", folder=hidden_folder)
        dataset_container, dataset_template = self._template("Dataset choices", assigned_to=dataset)
        dataset_field = self._field(dataset_template, "Status", MetadataFieldType.SELECTION, ["Dataset visible"])
        dataset.metadata_template = dataset_template
        dataset.save()

        response = client.get(
            reverse("search-metadata-choice-options"),
            {
                "key": "Status",
                "field_type": MetadataFieldType.SELECTION,
                "dataset_id": str(dataset.pk),
            },
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["results"] == [
            {
                "key": "Status",
                "field_type": MetadataFieldType.SELECTION,
                "source": "dataset",
                "source_id": str(dataset.pk),
                "metadata_template_id": str(dataset_template.pk),
                "metadata_template_name": dataset_template.name,
                "metadata_template_container_name": dataset_container.name,
                "metadata_template_field_id": str(dataset_field.pk),
                "source_name": dataset.display_name,
                "options": ["Dataset visible"],
            },
        ]
        assert str(project_container.pk) not in str(response.data)
        assert str(folder_container.pk) not in str(response.data)
        assert str(dataset_container.pk) not in str(response.data)
