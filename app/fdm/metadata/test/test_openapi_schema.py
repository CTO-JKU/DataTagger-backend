import json
from io import StringIO

import pytest
from django.core.management import call_command

from fdm.metadata.enums import MetadataFieldType

pytestmark = pytest.mark.django_db


def _get_expected_field_types():
    return {choice[0] for choice in MetadataFieldType.choices}


def _get_expected_content_type_enums():
    from fdm.core.rest.serializers import METADATA_CONTENT_TYPES, METADATA_TEMPLATE_CONTENT_TYPES

    return METADATA_CONTENT_TYPES, METADATA_TEMPLATE_CONTENT_TYPES


def _walk_schema(node, path="root"):
    if isinstance(node, dict):
        yield path, node
        for key, value in node.items():
            yield from _walk_schema(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk_schema(value, f"{path}[{index}]")


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


def _assert_value_schema_types(openapi_schema, schema_name):
    value_schema = openapi_schema["components"]["schemas"][schema_name]["properties"]["value"]
    one_of_types = [option.get("type") for option in value_schema["oneOf"]]
    assert "string" in one_of_types
    assert "array" in one_of_types
    assert "object" in one_of_types


def _get_query_parameters(openapi_schema, path):
    return openapi_schema["paths"][path]["get"].get("parameters", [])


def _get_query_parameter(openapi_schema, path, name):
    return next(parameter for parameter in _get_query_parameters(openapi_schema, path) if parameter["name"] == name)


def test_openapi_metadata_template_container_create_declares_skip_pool_add_param(openapi_schema):
    """
    The skip_pool_add query parameter must be declared in the schema so
    automation and SDK consumers can discover the opt-out for the auto-add
    behavior on scoped template creation.
    """
    post_spec = openapi_schema["paths"]["/api/v1/metadata-template-container/"]["post"]
    parameters = post_spec.get("parameters", [])

    skip_params = [p for p in parameters if p.get("name") == "skip_pool_add" and p.get("in") == "query"]
    assert len(skip_params) == 1

    skip_param = skip_params[0]
    assert skip_param["schema"]["type"] == "boolean"
    assert "description" in skip_param and skip_param["description"].strip()


def test_openapi_metadata_template_container_clone_declares_target_payload(openapi_schema):
    clone_paths = [
        path
        for path in openapi_schema["paths"]
        if path.startswith("/api/v1/metadata-template-container/") and path.endswith("/clone/")
    ]
    assert len(clone_paths) == 1

    post_spec = openapi_schema["paths"][clone_paths[0]]["post"]
    assert "requestBody" in post_spec

    request_schema = post_spec["requestBody"]["content"]["application/json"]["schema"]
    if "$ref" in request_schema:
        schema_name = request_schema["$ref"].split("/")[-1]
        request_schema = openapi_schema["components"]["schemas"][schema_name]

    serialized_schema = json.dumps(request_schema)
    assert "assigned_to_content_type" in serialized_schema
    assert "assigned_to_object_id" in serialized_schema


def test_openapi_metadata_template_content_type_enum_includes_uploads_dataset(openapi_schema):
    assert "uploads.uploadsdataset" in json.dumps(openapi_schema)


def test_openapi_assigned_content_type_filter_enums_match_supported_models(openapi_schema):
    """
    Assigned content type query params must advertise only the content types each endpoint can actually filter.
    """
    metadata_content_types, metadata_template_content_types = _get_expected_content_type_enums()

    metadata_param = _get_query_parameter(
        openapi_schema,
        "/api/v1/metadata/",
        "assigned_to_content_type",
    )
    assert set(metadata_param["schema"]["enum"]) == set(metadata_content_types)

    template_container_param = _get_query_parameter(
        openapi_schema,
        "/api/v1/metadata-template-container/",
        "assigned_to_content_type",
    )
    assert set(template_container_param["schema"]["enum"]) == set(metadata_template_content_types)

    file_parser_param = _get_query_parameter(
        openapi_schema,
        "/api/v1/file-parser-configuration/",
        "assigned_to_content_type",
    )
    assert set(file_parser_param["schema"]["enum"]) == set(metadata_template_content_types)


def test_openapi_file_parser_configuration_declares_supported_filters(openapi_schema):
    parameter_names = {
        parameter["name"] for parameter in _get_query_parameters(openapi_schema, "/api/v1/file-parser-configuration/")
    }

    assert {
        "parser_type",
        "assigned_to_content_type",
        "assigned_to_object_id",
        "enabled",
    }.issubset(parameter_names)


def test_openapi_metadata_template_does_not_declare_legacy_assigned_content_filters(openapi_schema):
    parameter_names = {
        parameter["name"] for parameter in _get_query_parameters(openapi_schema, "/api/v1/metadata-template/")
    }

    assert "assigned_to_content_type" not in parameter_names
    assert "assigned_to_object_id" not in parameter_names


def test_openapi_field_type_enum_matches_metadata_field_types(openapi_schema):
    expected_field_types = _get_expected_field_types()
    field_types = openapi_schema["components"]["schemas"]["FieldTypeEnum"]["enum"]
    assert set(field_types) == expected_field_types


def test_openapi_metadata_response_value_schema_contains_array(openapi_schema):
    _assert_value_schema_types(
        openapi_schema=openapi_schema,
        schema_name="Metadata",
    )
    _assert_value_schema_types(
        openapi_schema=openapi_schema,
        schema_name="MetadataTemplateField",
    )
    _assert_value_schema_types(
        openapi_schema=openapi_schema,
        schema_name="MinimalMetadata",
    )


def test_openapi_metadata_request_value_schema_contains_array(openapi_schema):
    metadata_value_schema = openapi_schema["components"]["schemas"]["MetadataBulkActionRequest"]["properties"][
        "metadata"
    ]["items"]["properties"]["value"]
    assert "array" in [option.get("type") for option in metadata_value_schema["oneOf"]]

    metadata_template_field_value_schema = openapi_schema["components"]["schemas"][
        "MetadataTemplateContainerVersionActionPayloadRequest"
    ]["properties"]["metadata_template_fields"]["items"]["properties"]["value"]
    assert "array" in [option.get("type") for option in metadata_template_field_value_schema["oneOf"]]


def test_openapi_metadata_field_filter_matches_metadata_field_types(openapi_schema):
    expected_field_types = _get_expected_field_types()
    metadata_field_parameters = openapi_schema["paths"]["/api/v1/metadata-field/"]["get"]["parameters"]
    field_type_parameter = next(
        parameter for parameter in metadata_field_parameters if parameter["name"] == "field_type"
    )

    field_type_filter_values = field_type_parameter["schema"]["enum"]
    assert set(field_type_filter_values) == expected_field_types


def test_openapi_all_field_type_properties_match_metadata_field_types(openapi_schema):
    expected_field_types = _get_expected_field_types()
    mismatches = []

    for path, node in _walk_schema(openapi_schema):
        if not isinstance(node, dict):
            continue

        properties = node.get("properties")
        if not isinstance(properties, dict) or "field_type" not in properties:
            continue

        field_type_schema = properties["field_type"]
        if not isinstance(field_type_schema, dict):
            mismatches.append((path, "field_type is not a schema object"))
            continue

        ref = field_type_schema.get("$ref")
        if ref is not None:
            if ref != "#/components/schemas/FieldTypeEnum":
                mismatches.append((path, f"unexpected field_type ref: {ref}"))
            continue

        enum_values = field_type_schema.get("enum")
        if not isinstance(enum_values, list):
            mismatches.append((path, "field_type has neither $ref nor enum list"))
            continue

        if set(enum_values) != expected_field_types:
            mismatches.append((path, f"enum mismatch: {enum_values}"))

    assert not mismatches, f"field_type schema mismatches found: {mismatches}"


def test_openapi_all_field_type_query_params_match_metadata_field_types(openapi_schema):
    expected_field_types = _get_expected_field_types()
    endpoint_specific_field_types = {
        "/api/v1/search/metadata-choice-options/": {
            MetadataFieldType.SELECTION,
            MetadataFieldType.MULTI_SELECTION,
            MetadataFieldType.TAG,
        },
    }
    mismatches = []

    for path, path_item in openapi_schema.get("paths", {}).items():
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue

            for parameter in operation.get("parameters", []):
                if parameter.get("name") != "field_type":
                    continue

                enum_values = parameter.get("schema", {}).get("enum")
                expected_values = endpoint_specific_field_types.get(path, expected_field_types)
                if not isinstance(enum_values, list) or set(enum_values) != expected_values:
                    mismatches.append((f"{method.upper()} {path}", enum_values))

    assert not mismatches, f"field_type query parameter enum mismatches found: {mismatches}"
