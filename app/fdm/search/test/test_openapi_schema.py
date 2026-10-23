import json
from io import StringIO

import pytest
from django.core.management import call_command

pytestmark = pytest.mark.django_db


@pytest.fixture
def openapi_schema():
    stdout = StringIO()
    call_command(
        "spectacular",
        "--format",
        "openapi-json",
        stdout=stdout,
    )
    return json.loads(stdout.getvalue())


def _resolve_schema(openapi_schema, schema):
    if "allOf" in schema and len(schema["allOf"]) == 1:
        return _resolve_schema(openapi_schema, schema["allOf"][0])

    if "$ref" not in schema:
        return schema

    ref = schema["$ref"]
    prefix = "#/components/schemas/"
    assert ref.startswith(prefix), f"Unexpected schema ref: {ref}"
    return openapi_schema["components"]["schemas"][ref.removeprefix(prefix)]


def _get_search_operation(openapi_schema):
    return openapi_schema["paths"]["/api/v1/search/global/"]["post"]


def test_openapi_choice_option_groups_expose_display_names(openapi_schema):
    operation = openapi_schema["paths"]["/api/v1/search/metadata-choice-options/"]["get"]
    response_schema = _resolve_schema(
        openapi_schema,
        operation["responses"]["200"]["content"]["application/json"]["schema"],
    )
    group_schema = _resolve_schema(openapi_schema, response_schema["properties"]["results"]["items"])

    assert "metadata_template_container_name" in group_schema["required"]
    assert "source_name" in group_schema["required"]


def test_openapi_search_request_schema_uses_cursor_contract(openapi_schema):
    operation = _get_search_operation(openapi_schema)
    request_schema = _resolve_schema(
        openapi_schema,
        operation["requestBody"]["content"]["application/json"]["schema"],
    )

    properties = request_schema["properties"]

    assert "cursor" in properties
    assert "cursor_direction" in properties
    assert "result_types" in properties
    assert "sort" in properties
    assert "group" in properties
    assert "response_mode" in properties
    assert "offset" not in properties
    assert "additionalProperties" not in request_schema


def test_openapi_search_request_schema_is_not_free_form_or_legacy_ref(openapi_schema):
    operation = _get_search_operation(openapi_schema)
    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]

    assert request_schema.get("additionalProperties") is not True
    assert request_schema.get("$ref") != "#/components/schemas/SearchGlobalRequest"


def test_openapi_search_cursor_direction_enum_and_result_types(openapi_schema):
    operation = _get_search_operation(openapi_schema)
    request_schema = _resolve_schema(
        openapi_schema,
        operation["requestBody"]["content"]["application/json"]["schema"],
    )

    cursor_direction_schema = _resolve_schema(
        openapi_schema,
        request_schema["properties"]["cursor_direction"],
    )
    result_types_schema = _resolve_schema(
        openapi_schema,
        request_schema["properties"]["result_types"]["items"],
    )

    assert cursor_direction_schema["enum"] == [
        "next",
        "previous",
    ]
    assert "dataset_version" in result_types_schema["enum"]
    assert "template" in result_types_schema["enum"]
    assert "template_version" in result_types_schema["enum"]
    assert "version" not in result_types_schema["enum"]


def test_openapi_search_response_schema_uses_cursor_fields(openapi_schema):
    operation = _get_search_operation(openapi_schema)
    response_schema = _resolve_schema(
        openapi_schema,
        operation["responses"]["200"]["content"]["application/json"]["schema"],
    )

    properties = response_schema["properties"]

    assert "count" in properties
    assert "next_cursor" in properties
    assert "previous_cursor" in properties
    assert "results" in properties
    assert "next" not in properties
    assert "previous" not in properties


def test_openapi_search_examples_use_cursor_not_offset(openapi_schema):
    operation = _get_search_operation(openapi_schema)
    examples = operation["requestBody"]["content"]["application/json"].get("examples", {})

    assert any(
        example.get("value", {}).get("cursor_direction") == "next"
        and "cursor" in example.get("value", {})
        and "offset" not in example.get("value", {})
        for example in examples.values()
    )


def test_openapi_search_result_item_is_typed_union(openapi_schema):
    """SearchResult.item should be a oneOf union of all result type serializers, not a plain string."""
    schemas = openapi_schema["components"]["schemas"]

    assert "SearchResultItem" in schemas
    item_schema = schemas["SearchResultItem"]
    assert "oneOf" in item_schema

    one_of_refs = {ref["$ref"] for ref in item_schema["oneOf"]}
    expected_refs = {
        "#/components/schemas/ProjectSearch",
        "#/components/schemas/FolderSearch",
        "#/components/schemas/SubfolderSearch",
        "#/components/schemas/UploadsDatasetSearch",
        "#/components/schemas/UploadsVersionSearch",
        "#/components/schemas/UploadsVersionFileSearch",
        "#/components/schemas/MetadataTemplateSearch",
        "#/components/schemas/MetadataTemplateVersionSearch",
    }
    assert one_of_refs == expected_refs


def test_openapi_compact_search_schemas_are_included(openapi_schema):
    """Compact mode schemas should appear in the spec via the postprocessing hook."""
    schemas = openapi_schema["components"]["schemas"]

    assert "CompactProjectItem" in schemas
    assert "CompactFolderItem" in schemas
    assert "CompactSubfolderItem" in schemas
    assert "CompactDatasetItem" in schemas
    assert "CompactDatasetVersionItem" in schemas
    assert "CompactFileItem" in schemas
    assert "CompactTemplateItem" in schemas
    assert "CompactTemplateVersionItem" in schemas
    assert "CompactSearchResultItem" in schemas

    item_schema = schemas["CompactSearchResultItem"]
    assert "oneOf" in item_schema
    assert len(item_schema["oneOf"]) == 8


def test_openapi_search_schema_has_no_custom_search_ref_components(openapi_schema):
    components = openapi_schema.get("components", {}).get("schemas", {})

    assert "SearchGlobalRequest" not in components

    removed_custom_components = {
        "SearchResultType",
        "SearchFieldId",
        "SearchOperator",
        "SearchLogicalOperator",
        "SearchMetadataFieldType",
        "SearchResponseMode",
        "SearchCursorDirection",
        "SearchSortField",
        "SearchSortDirection",
        "SearchSortRule",
        "SearchRule",
        "SearchCondition",
        "SearchGroup",
    }

    assert removed_custom_components.isdisjoint(components)


def test_static_search_openapi_field_ids_mirror_runtime_registry():
    from fdm.search.rest import openapi as search_openapi
    from fdm.search.services import SearchFieldCatalog

    assert search_openapi.SEARCH_FIELD_IDS == SearchFieldCatalog.field_ids()
