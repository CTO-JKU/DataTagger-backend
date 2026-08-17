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


def test_openapi_exposes_dashboard_snapshot_contract(openapi_schema):
    operation = openapi_schema["paths"]["/api/v1/dashboard/"]["get"]
    response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    snapshot_schema = _resolve_schema(openapi_schema, response_schema)
    resource_counts_schema = _resolve_schema(
        openapi_schema,
        snapshot_schema["properties"]["resource_counts"],
    )
    auto_deletion_schema = _resolve_schema(
        openapi_schema,
        snapshot_schema["properties"]["upcoming_auto_deletion"],
    )

    assert operation["security"]
    assert set(snapshot_schema["properties"]) == {
        "generated_at",
        "resource_counts",
        "upcoming_auto_deletion",
    }
    assert set(resource_counts_schema["properties"]) == {
        "draft_datasets",
        "project_datasets",
        "created_projects",
        "collaborating_projects",
        "datasets_missing_files",
    }
    assert set(auto_deletion_schema["properties"]) == {
        "today_or_tomorrow",
        "in_2_to_3_days",
        "in_4_to_7_days",
        "in_8_to_14_days",
    }
