import pytest
from django.db import connection
from django.urls import reverse
from rest_framework import status

from fdm.core.helpers import set_request_for_user
from fdm.metadata.enums import MetadataFieldType
from fdm.metadata.helpers import set_metadata
from fdm.metadata.models import Metadata, MetadataTemplate, MetadataTemplateContainer, MetadataTemplateField
from fdm.projects.models import Project


@pytest.mark.django_db
def test_choice_value_gin_index_is_valid_partial_expression_index():
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT indexdef, indisvalid
            FROM pg_indexes
            JOIN pg_class ON pg_class.relname = pg_indexes.indexname
            JOIN pg_index ON pg_index.indexrelid = pg_class.oid
            WHERE indexname = 'search_metadata_choice_value_gin_idx'
            """,
        )
        row = cursor.fetchone()

    assert row is not None
    index_definition, is_valid = row
    assert is_valid is True
    assert "USING gin" in index_definition
    assert "value -> 'value'::text" in index_definition
    assert "jsonb_path_ops" in index_definition
    assert "field_type" in index_definition
    assert all(field_type in index_definition for field_type in ("SELECTION", "MULTI_SELECTION", "TAG"))


@pytest.mark.django_db
class TestChoiceMetadataValueSearch:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])
        self.project = Project.objects.create(name="Choice search project")
        set_metadata(
            assigned_to_content_type=self.project.get_content_type(),
            assigned_to_object_id=self.project.pk,
            custom_key="Workflow",
            field_type=MetadataFieldType.SELECTION,
            value="Open",
            config={"options": ["Open", "Closed"]},
        )

    @staticmethod
    def _choice_group(*conditions):
        return {
            "operator": "AND",
            "conditions": list(conditions),
        }

    def _search(self, client, *conditions):
        return client.post(
            reverse("search-global"),
            {
                "result_types": ["project"],
                "group": self._choice_group(*conditions),
            },
            format="json",
        )

    def test_schema_owns_metadata_value_operators_by_field_type(self, client):
        response = client.get(reverse("search-global-schema"))

        assert response.status_code == status.HTTP_200_OK
        fields = {field["id"]: field for field in response.data["types"]["project"]["fields"]}
        metadata_value = fields["metadata.value"]

        assert "typed_field_types" not in metadata_value
        assert metadata_value["operators_by_field_type"][MetadataFieldType.SELECTION] == [
            "in",
            "is",
            "is_empty",
            "is_not",
            "is_not_empty",
            "not_in",
        ]
        assert metadata_value["operators_by_field_type"][MetadataFieldType.MULTI_SELECTION] == [
            "contains_all",
            "contains_any",
            "contains_none",
            "is_empty",
            "is_exactly",
            "is_not_empty",
        ]
        assert metadata_value["operators_by_field_type"][MetadataFieldType.TAG] == [
            "contains_all",
            "contains_any",
            "contains_none",
            "is_empty",
            "is_exactly",
            "is_not_empty",
        ]

    def test_keyless_selection_equality_is_case_sensitive(self, client):
        match = self._search(
            client,
            {
                "field": "metadata.value",
                "operator": "is",
                "value": "Open",
                "field_type": MetadataFieldType.SELECTION,
            },
        )
        case_mismatch = self._search(
            client,
            {
                "field": "metadata.value",
                "operator": "is",
                "value": "open",
                "field_type": MetadataFieldType.SELECTION,
            },
        )

        assert match.status_code == status.HTTP_200_OK
        assert match.data["count"] == 1
        assert match.data["results"][0]["item"]["pk"] == str(self.project.pk)
        assert case_mismatch.status_code == status.HTTP_200_OK
        assert case_mismatch.data["count"] == 0

    def test_selection_any_of_matches_selected_or_configured_unselected_state(self, client):
        response = self._search(
            client,
            {
                "field": "metadata.value",
                "operator": "in",
                "value": {"selected": ["Draft"], "unselected": ["Closed"]},
                "field_type": MetadataFieldType.SELECTION,
            },
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1

    @pytest.mark.parametrize(
        ("field_type", "operator"),
        [
            (MetadataFieldType.SELECTION, "in"),
            (MetadataFieldType.MULTI_SELECTION, "contains_all"),
            (MetadataFieldType.TAG, "is_exactly"),
        ],
    )
    def test_choice_set_operators_reject_the_obsolete_literal_list_shape(self, client, field_type, operator):
        response = self._search(
            client,
            {
                "field": "metadata.value",
                "operator": operator,
                "value": ["Open"],
                "field_type": field_type,
            },
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "selected and unselected lists" in str(response.data)

    @pytest.mark.parametrize("field_type", [MetadataFieldType.MULTI_SELECTION, MetadataFieldType.TAG])
    def test_multi_value_contains_any_all_and_exact_set(self, client, field_type):
        metadata = set_metadata(
            assigned_to_content_type=self.project.get_content_type(),
            assigned_to_object_id=self.project.pk,
            custom_key=f"Labels {field_type}",
            field_type=field_type,
            value=["first", "third"],
            config={"options": ["first", "second", "third", "extra"]},
        )
        # Preserve a legacy duplicate occurrence to prove comparisons use set semantics.
        Metadata.objects.filter(pk=metadata.pk).update(value={"value": ["third", "first", "third"]})

        for operator, value, expected_count in [
            ("contains_any", {"selected": ["missing", "third"], "unselected": []}, 1),
            ("contains_all", {"selected": ["first", "third"], "unselected": []}, 1),
            ("contains_all", {"selected": ["first", "extra"], "unselected": []}, 0),
            ("is_exactly", {"selected": ["first", "third"], "unselected": []}, 1),
            ("is_exactly", {"selected": ["first"], "unselected": []}, 0),
        ]:
            response = self._search(
                client,
                {
                    "field": "metadata.key",
                    "operator": "is",
                    "value": f"Labels {field_type}",
                },
                {
                    "field": "metadata.value",
                    "operator": operator,
                    "value": value,
                    "field_type": field_type,
                },
            )

            assert response.status_code == status.HTTP_200_OK
            assert response.data["count"] == expected_count

    @pytest.mark.parametrize("field_type", [MetadataFieldType.MULTI_SELECTION, MetadataFieldType.TAG])
    def test_multi_value_matches_selected_and_configured_unselected_states(self, client, field_type):
        set_metadata(
            assigned_to_content_type=self.project.get_content_type(),
            assigned_to_object_id=self.project.pk,
            custom_key=f"Stateful labels {field_type}",
            field_type=field_type,
            value=["first", "third"],
            config={"options": ["first", "second", "third", "extra"]},
        )

        for operator, value, expected_count in [
            ("contains_any", {"selected": ["missing"], "unselected": ["second"]}, 1),
            (
                "contains_all",
                {"selected": ["first", "third"], "unselected": ["second"]},
                1,
            ),
            ("contains_all", {"selected": ["first"], "unselected": ["third"]}, 0),
            (
                "is_exactly",
                {"selected": ["first", "third"], "unselected": ["second"]},
                1,
            ),
            ("is_exactly", {"selected": ["first"], "unselected": ["second"]}, 0),
            ("contains_any", {"selected": [], "unselected": ["not-configured"]}, 0),
        ]:
            response = self._search(
                client,
                {
                    "field": "metadata.key",
                    "operator": "is",
                    "value": f"Stateful labels {field_type}",
                },
                {
                    "field": "metadata.value",
                    "operator": operator,
                    "value": value,
                    "field_type": field_type,
                },
            )

            assert response.status_code == status.HTTP_200_OK
            assert response.data["count"] == expected_count

    def test_contains_all_does_not_aggregate_across_metadata_entries(self, client):
        split_project = Project.objects.create(name="Split choice values")
        for value in [["first"], ["third"]]:
            set_metadata(
                assigned_to_content_type=split_project.get_content_type(),
                assigned_to_object_id=split_project.pk,
                custom_key="Split labels",
                field_type=MetadataFieldType.TAG,
                value=value,
                config={"options": ["first", "third"]},
            )

        response = self._search(
            client,
            {
                "field": "metadata.key",
                "operator": "is",
                "value": "Split labels",
            },
            {
                "field": "metadata.value",
                "operator": "contains_all",
                "value": {"selected": ["first", "third"], "unselected": []},
                "field_type": MetadataFieldType.TAG,
            },
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 0

    @pytest.mark.parametrize("field_type", [MetadataFieldType.MULTI_SELECTION, MetadataFieldType.TAG])
    def test_multi_value_empty_matches_existing_empty_but_not_missing(self, client, field_type):
        empty_project = Project.objects.create(name="Empty tag")
        set_metadata(
            assigned_to_content_type=empty_project.get_content_type(),
            assigned_to_object_id=empty_project.pk,
            custom_key="Labels",
            field_type=field_type,
            value=[],
            config={"options": ["first"]},
        )
        null_project = Project.objects.create(name="Null tag")
        set_metadata(
            assigned_to_content_type=null_project.get_content_type(),
            assigned_to_object_id=null_project.pk,
            custom_key="Labels",
            field_type=field_type,
            value=None,
            config={"options": ["first"]},
        )
        populated_project = Project.objects.create(name="Populated tag")
        set_metadata(
            assigned_to_content_type=populated_project.get_content_type(),
            assigned_to_object_id=populated_project.pk,
            custom_key="Labels",
            field_type=field_type,
            value=["first"],
            config={"options": ["first"]},
        )
        Project.objects.create(name="Missing tag")

        empty_response = self._search(
            client,
            {
                "field": "metadata.value",
                "operator": "is_empty",
                "field_type": field_type,
            },
        )
        non_empty_response = self._search(
            client,
            {
                "field": "metadata.value",
                "operator": "is_not_empty",
                "field_type": field_type,
            },
        )

        assert empty_response.status_code == status.HTTP_200_OK
        assert {result["item"]["name"] for result in empty_response.data["results"]} == {"Empty tag", "Null tag"}
        assert non_empty_response.status_code == status.HTTP_200_OK
        assert [result["item"]["name"] for result in non_empty_response.data["results"]] == ["Populated tag"]

    def test_selection_empty_non_empty_and_inequality_require_existing_candidates(self, client):
        empty_project = Project.objects.create(name="Empty selection")
        set_metadata(
            assigned_to_content_type=empty_project.get_content_type(),
            assigned_to_object_id=empty_project.pk,
            custom_key="Workflow",
            field_type=MetadataFieldType.SELECTION,
            value=None,
            config={"options": ["Open", "Closed"]},
        )
        blank_project = Project.objects.create(name="Legacy blank selection")
        blank_metadata = set_metadata(
            assigned_to_content_type=blank_project.get_content_type(),
            assigned_to_object_id=blank_project.pk,
            custom_key="Workflow",
            field_type=MetadataFieldType.SELECTION,
            value=None,
            config={"options": ["Open", "Closed"]},
        )
        Metadata.objects.filter(pk=blank_metadata.pk).update(value={"value": ""})
        Project.objects.create(name="Missing selection")

        def search(operator, value_marker=None):
            rule = {
                "field": "metadata.value",
                "operator": operator,
                "field_type": MetadataFieldType.SELECTION,
            }
            if value_marker is not None:
                rule["value"] = value_marker
            return self._search(client, rule)

        empty = search("is_empty")
        non_empty = search("is_not_empty")
        not_closed = search("is_not", "Closed")

        assert {result["item"]["name"] for result in empty.data["results"]} == {
            "Empty selection",
            "Legacy blank selection",
        }
        assert [result["item"]["name"] for result in non_empty.data["results"]] == ["Choice search project"]
        assert {result["item"]["name"] for result in not_closed.data["results"]} == {
            "Choice search project",
            "Empty selection",
            "Legacy blank selection",
        }

    def test_bounded_key_set_matches_each_candidate_without_aggregating(self, client):
        second_project = Project.objects.create(name="Second bounded key")
        set_metadata(
            assigned_to_content_type=second_project.get_content_type(),
            assigned_to_object_id=second_project.pk,
            custom_key="Review state",
            field_type=MetadataFieldType.SELECTION,
            value="Open",
            config={"options": ["Open", "Closed"]},
        )

        response = self._search(
            client,
            {
                "field": "metadata.key",
                "operator": "in",
                "value": ["Workflow", "Review state"],
            },
            {
                "field": "metadata.value",
                "operator": "is",
                "value": "Open",
                "field_type": MetadataFieldType.SELECTION,
            },
        )

        assert response.status_code == status.HTTP_200_OK
        assert {result["item"]["name"] for result in response.data["results"]} == {
            "Choice search project",
            "Second bounded key",
        }

    def test_template_result_matches_selected_and_configured_unselected_default_states(self, client):
        container = MetadataTemplateContainer.objects.create(name="Choice defaults")
        template = MetadataTemplate.objects.create(metadata_template_container=container, name="Choice defaults v1")
        MetadataTemplateField.objects.create(
            metadata_template=template,
            custom_key="Labels",
            field_type=MetadataFieldType.TAG,
            value={"value": ["selected"]},
            config={"options": ["selected", "configured-only"]},
        )

        def search_template(*, selected=None, unselected=None):
            return client.post(
                reverse("search-global"),
                {
                    "result_types": ["template"],
                    "group": self._choice_group(
                        {
                            "field": "metadata.value",
                            "operator": "contains_any",
                            "value": {
                                "selected": [selected] if selected else [],
                                "unselected": [unselected] if unselected else [],
                            },
                            "field_type": MetadataFieldType.TAG,
                        },
                    ),
                },
                format="json",
            )

        selected = search_template(selected="selected")
        configured_only_as_selected = search_template(selected="configured-only")
        configured_only_as_unselected = search_template(unselected="configured-only")
        unconfigured_as_unselected = search_template(unselected="missing")

        assert selected.status_code == status.HTTP_200_OK
        assert selected.data["count"] == 1
        assert configured_only_as_selected.status_code == status.HTTP_200_OK
        assert configured_only_as_selected.data["count"] == 0
        assert configured_only_as_unselected.status_code == status.HTTP_200_OK
        assert configured_only_as_unselected.data["count"] == 1
        assert unconfigured_as_unselected.status_code == status.HTTP_200_OK
        assert unconfigured_as_unselected.data["count"] == 0

    def test_same_key_matches_defaults_across_different_template_payloads(self, client):
        containers = []
        for index, extra_key in enumerate(("First extra", "Second extra"), start=1):
            container = MetadataTemplateContainer.objects.create(name=f"Shared choice {index}")
            template = MetadataTemplate.objects.create(
                metadata_template_container=container,
                name=f"Shared choice {index} v1",
            )
            MetadataTemplateField.objects.create(
                metadata_template=template,
                custom_key="Shared status",
                field_type=MetadataFieldType.SELECTION,
                value={"value": "Open"},
                config={"options": ["Open", "Closed"]},
            )
            MetadataTemplateField.objects.create(
                metadata_template=template,
                custom_key=extra_key,
                field_type=MetadataFieldType.TEXT,
            )
            containers.append(container)

        response = client.post(
            reverse("search-global"),
            {
                "result_types": ["template"],
                "group": self._choice_group(
                    {
                        "field": "metadata.key",
                        "operator": "is",
                        "value": "Shared status",
                    },
                    {
                        "field": "metadata.value",
                        "operator": "is",
                        "value": "Open",
                        "field_type": MetadataFieldType.SELECTION,
                    },
                ),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert {result["item"]["pk"] for result in response.data["results"]} == {
            str(container.pk) for container in containers
        }

    def test_template_negative_choice_comparison_uses_candidate_set_anti_existence(self, client):
        def make_template(name, values):
            container = MetadataTemplateContainer.objects.create(name=name)
            template = MetadataTemplate.objects.create(metadata_template_container=container, name=f"{name} v1")
            for value in values:
                MetadataTemplateField.objects.create(
                    metadata_template=template,
                    custom_key="Workflow",
                    field_type=MetadataFieldType.SELECTION,
                    value={"value": value},
                    config={"options": ["Open", "Closed"]},
                )
            return container

        make_template("Mixed defaults", ["Open", "Closed"])
        safe = make_template("Safe default", ["Closed"])
        empty = make_template("Empty default", [None])
        make_template("Missing default", [])

        response = client.post(
            reverse("search-global"),
            {
                "result_types": ["template"],
                "group": self._choice_group(
                    {
                        "field": "metadata.key",
                        "operator": "is",
                        "value": "Workflow",
                    },
                    {
                        "field": "metadata.value",
                        "operator": "not_in",
                        "value": {"selected": ["Open"], "unselected": []},
                    },
                ),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert {result["item"]["pk"] for result in response.data["results"]} == {str(safe.pk), str(empty.pk)}

    def test_selection_none_of_uses_anti_existence_across_all_candidates(self, client):
        set_metadata(
            assigned_to_content_type=self.project.get_content_type(),
            assigned_to_object_id=self.project.pk,
            custom_key="Workflow",
            field_type=MetadataFieldType.SELECTION,
            value="Closed",
            config={"options": ["Open", "Closed"]},
        )
        closed_project = Project.objects.create(name="Closed only")
        set_metadata(
            assigned_to_content_type=closed_project.get_content_type(),
            assigned_to_object_id=closed_project.pk,
            custom_key="Workflow",
            field_type=MetadataFieldType.SELECTION,
            value="Closed",
            config={"options": ["Open", "Closed"]},
        )
        empty_project = Project.objects.create(name="Empty workflow")
        set_metadata(
            assigned_to_content_type=empty_project.get_content_type(),
            assigned_to_object_id=empty_project.pk,
            custom_key="Workflow",
            field_type=MetadataFieldType.SELECTION,
            value=None,
            config={"options": ["Open", "Closed"]},
        )
        Project.objects.create(name="Missing workflow")

        response = self._search(
            client,
            {
                "field": "metadata.key",
                "operator": "is",
                "value": "Workflow",
            },
            {
                "field": "metadata.value",
                "operator": "not_in",
                "value": {"selected": ["Open"], "unselected": []},
                "field_type": MetadataFieldType.SELECTION,
            },
        )

        assert response.status_code == status.HTTP_200_OK
        assert {result["item"]["name"] for result in response.data["results"]} == {
            "Closed only",
            "Empty workflow",
        }

    def test_key_inferred_selection_none_of_uses_anti_existence_across_all_candidates(self, client):
        set_metadata(
            assigned_to_content_type=self.project.get_content_type(),
            assigned_to_object_id=self.project.pk,
            custom_key="Workflow",
            field_type=MetadataFieldType.SELECTION,
            value="Closed",
            config={"options": ["Open", "Closed"]},
        )
        safe_project = Project.objects.create(name="Inferred safe workflow")
        set_metadata(
            assigned_to_content_type=safe_project.get_content_type(),
            assigned_to_object_id=safe_project.pk,
            custom_key="Workflow",
            field_type=MetadataFieldType.SELECTION,
            value="Closed",
            config={"options": ["Open", "Closed"]},
        )

        response = self._search(
            client,
            {
                "field": "metadata.key",
                "operator": "is",
                "value": "Workflow",
            },
            {
                "field": "metadata.value",
                "operator": "not_in",
                "value": {"selected": ["Open"], "unselected": []},
            },
        )

        assert response.status_code == status.HTTP_200_OK
        assert [result["item"]["name"] for result in response.data["results"]] == ["Inferred safe workflow"]

    def test_negative_choice_comparison_rejects_or_cluster(self, client):
        response = client.post(
            reverse("search-global"),
            {
                "result_types": ["project"],
                "group": {
                    "operator": "OR",
                    "conditions": [
                        {
                            "field": "metadata.value",
                            "operator": "not_in",
                            "value": {"selected": ["Open"], "unselected": []},
                            "field_type": MetadataFieldType.SELECTION,
                        },
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "AND metadata predicate cluster" in str(response.data)

    @pytest.mark.parametrize(
        ("field_type", "operator", "value"),
        [
            (MetadataFieldType.SELECTION, "contains_any", ["Open"]),
            (MetadataFieldType.MULTI_SELECTION, "is", "Open"),
            (MetadataFieldType.TAG, "not_in", ["Open"]),
        ],
    )
    def test_invalid_choice_operator_and_field_type_combinations_are_rejected(
        self,
        client,
        field_type,
        operator,
        value,
    ):
        response = self._search(
            client,
            {
                "field": "metadata.value",
                "operator": operator,
                "value": value,
                "field_type": field_type,
            },
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.parametrize("field_type", [MetadataFieldType.MULTI_SELECTION, MetadataFieldType.TAG])
    def test_contains_none_uses_anti_existence_across_all_candidates(self, client, field_type):
        set_metadata(
            assigned_to_content_type=self.project.get_content_type(),
            assigned_to_object_id=self.project.pk,
            custom_key="Risk tags",
            field_type=field_type,
            value=["forbidden"],
            config={"options": ["forbidden", "safe"]},
        )
        set_metadata(
            assigned_to_content_type=self.project.get_content_type(),
            assigned_to_object_id=self.project.pk,
            custom_key="Risk tags",
            field_type=field_type,
            value=["safe"],
            config={"options": ["forbidden", "safe"]},
        )
        safe_project = Project.objects.create(name="Safe tags")
        set_metadata(
            assigned_to_content_type=safe_project.get_content_type(),
            assigned_to_object_id=safe_project.pk,
            custom_key="Risk tags",
            field_type=field_type,
            value=["safe"],
            config={"options": ["forbidden", "safe"]},
        )
        empty_project = Project.objects.create(name="Empty tags")
        set_metadata(
            assigned_to_content_type=empty_project.get_content_type(),
            assigned_to_object_id=empty_project.pk,
            custom_key="Risk tags",
            field_type=field_type,
            value=[],
            config={"options": ["forbidden", "safe"]},
        )
        Project.objects.create(name="Missing risk tags")

        response = self._search(
            client,
            {
                "field": "metadata.key",
                "operator": "is",
                "value": "Risk tags",
            },
            {
                "field": "metadata.value",
                "operator": "contains_none",
                "value": {"selected": ["forbidden"], "unselected": []},
                "field_type": field_type,
            },
        )

        assert response.status_code == status.HTTP_200_OK
        assert {result["item"]["name"] for result in response.data["results"]} == {
            "Safe tags",
            "Empty tags",
        }

        unselected_response = self._search(
            client,
            {
                "field": "metadata.key",
                "operator": "is",
                "value": "Risk tags",
            },
            {
                "field": "metadata.value",
                "operator": "contains_none",
                "value": {"selected": [], "unselected": ["safe"]},
                "field_type": field_type,
            },
        )

        assert unselected_response.status_code == status.HTTP_200_OK
        assert [result["item"]["name"] for result in unselected_response.data["results"]] == ["Safe tags"]
