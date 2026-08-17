import json

import pytest
from django.core.management import call_command

pytestmark = pytest.mark.django_db


@pytest.fixture
def openapi_schema(tmp_path):
    schema_path = tmp_path / "openapi.json"

    call_command(
        "spectacular",
        "--file",
        str(schema_path),
        "--format",
        "openapi-json",
    )

    return json.loads(schema_path.read_text())


def _resolve_schema(openapi_schema, schema):
    if "allOf" in schema and len(schema["allOf"]) == 1:
        return _resolve_schema(openapi_schema, schema["allOf"][0])

    if "$ref" not in schema:
        return schema

    ref = schema["$ref"]
    prefix = "#/components/schemas/"

    assert ref.startswith(prefix), f"Unexpected schema ref: {ref}"

    return openapi_schema["components"]["schemas"][ref.removeprefix(prefix)]


def test_openapi_project_list_declares_repeated_pk_filter(openapi_schema):
    parameters = openapi_schema["paths"]["/api/v1/project/"]["get"]["parameters"]
    pk_parameter = next(parameter for parameter in parameters if parameter["name"] == "pk")

    assert pk_parameter["in"] == "query"
    assert pk_parameter["schema"]["type"] == "array"
    assert pk_parameter["schema"]["items"]["format"] == "uuid"


def test_openapi_project_schema_contains_dashboard_summary_fields(openapi_schema):
    properties = openapi_schema["components"]["schemas"]["Project"]["properties"]
    my_membership_schema = _resolve_schema(openapi_schema, properties["my_membership"])

    assert my_membership_schema["type"] == "string"
    assert my_membership_schema["enum"] == ["admin", "member"]
    assert properties["folders_count"]["type"] == "integer"
    assert properties["datasets_count"]["type"] == "integer"
