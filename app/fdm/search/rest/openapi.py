from fdm.search.services import (
    SEARCH_CURSOR_DIRECTIONS as RUNTIME_SEARCH_CURSOR_DIRECTIONS,
)
from fdm.search.services import (
    SEARCH_RESPONSE_MODES as RUNTIME_SEARCH_RESPONSE_MODES,
)
from fdm.search.services import (
    SEARCH_RESULT_TYPES as RUNTIME_SEARCH_RESULT_TYPES,
)
from fdm.search.services import (
    SearchFieldCatalog,
)

SEARCH_RESULT_TYPE_ENUM_NAME = "SearchResultType"
SEARCH_FIELD_ID_ENUM_NAME = "SearchFieldId"
SEARCH_OPERATOR_ENUM_NAME = "SearchOperator"
SEARCH_LOGICAL_OPERATOR_ENUM_NAME = "SearchLogicalOperator"
SEARCH_METADATA_FIELD_TYPE_ENUM_NAME = "SearchMetadataFieldType"
SEARCH_RESPONSE_MODE_ENUM_NAME = "SearchResponseMode"
SEARCH_CURSOR_DIRECTION_ENUM_NAME = "SearchCursorDirection"
SEARCH_SORT_FIELD_ENUM_NAME = "SearchSortField"
SEARCH_SORT_DIRECTION_ENUM_NAME = "SearchSortDirection"
SEARCH_SORT_RULE_SCHEMA_NAME = "SearchSortRule"
SEARCH_RULE_SCHEMA_NAME = "SearchRule"
SEARCH_CONDITION_SCHEMA_NAME = "SearchCondition"
SEARCH_GROUP_SCHEMA_NAME = "SearchGroup"

SEARCH_RESULT_TYPES = list(RUNTIME_SEARCH_RESULT_TYPES)

SEARCH_FIELD_IDS = SearchFieldCatalog.field_ids()

SEARCH_OPERATORS = SearchFieldCatalog.operator_ids()

SEARCH_LOGICAL_OPERATORS = [
    "AND",
    "OR",
]

SEARCH_METADATA_FIELD_TYPES = SearchFieldCatalog.metadata_field_type_ids()

SEARCH_SORT_FIELDS = SearchFieldCatalog.sort_fields()

SEARCH_SORT_DIRECTIONS = [
    "asc",
    "desc",
]

SEARCH_RESPONSE_MODES = list(RUNTIME_SEARCH_RESPONSE_MODES)

SEARCH_CURSOR_DIRECTIONS = list(RUNTIME_SEARCH_CURSOR_DIRECTIONS)

SEARCH_OPENAPI_COMPONENTS = {
    "schemas": {
        SEARCH_RESULT_TYPE_ENUM_NAME: {
            "type": "string",
            "enum": SEARCH_RESULT_TYPES,
            "description": "Entity types that can be returned by the global search endpoint.",
        },
        SEARCH_FIELD_ID_ENUM_NAME: {
            "type": "string",
            "enum": SEARCH_FIELD_IDS,
            "description": "Public filter field identifiers accepted by structured search rules.",
        },
        SEARCH_OPERATOR_ENUM_NAME: {
            "type": "string",
            "enum": SEARCH_OPERATORS,
            "description": "Comparison operators accepted by structured search rules.",
        },
        SEARCH_LOGICAL_OPERATOR_ENUM_NAME: {
            "type": "string",
            "enum": SEARCH_LOGICAL_OPERATORS,
            "description": "Boolean operator used to combine child conditions inside a group.",
        },
        SEARCH_METADATA_FIELD_TYPE_ENUM_NAME: {
            "type": "string",
            "enum": SEARCH_METADATA_FIELD_TYPES,
            "description": (
                "Optional metadata scalar types for typed `metadata.value` comparisons. "
                "Used to disambiguate keys that exist with multiple metadata field types."
            ),
        },
        SEARCH_RESPONSE_MODE_ENUM_NAME: {
            "type": "string",
            "enum": SEARCH_RESPONSE_MODES,
            "description": "Search response payload mode.",
        },
        SEARCH_CURSOR_DIRECTION_ENUM_NAME: {
            "type": "string",
            "enum": SEARCH_CURSOR_DIRECTIONS,
            "description": "Cursor pagination direction.",
        },
        SEARCH_SORT_FIELD_ENUM_NAME: {
            "type": "string",
            "enum": SEARCH_SORT_FIELDS,
            "description": "Sort fields supported by the mixed search result list.",
        },
        SEARCH_SORT_DIRECTION_ENUM_NAME: {
            "type": "string",
            "enum": SEARCH_SORT_DIRECTIONS,
            "description": "Sort direction.",
        },
        SEARCH_SORT_RULE_SCHEMA_NAME: {
            "type": "object",
            "properties": {
                "field": {
                    "$ref": f"#/components/schemas/{SEARCH_SORT_FIELD_ENUM_NAME}",
                },
                "direction": {
                    "$ref": f"#/components/schemas/{SEARCH_SORT_DIRECTION_ENUM_NAME}",
                },
            },
            "required": [
                "field",
                "direction",
            ],
            "additionalProperties": False,
        },
        SEARCH_RULE_SCHEMA_NAME: {
            "type": "object",
            "description": (
                "Single filter rule. `value` depends on the selected field/operator and can be "
                "a string, number, datetime string, boolean, or list."
            ),
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": [
                        "rule",
                    ],
                    "default": "rule",
                    "description": "Optional. If omitted, the backend still treats this object as a rule.",
                },
                "field": {
                    "$ref": f"#/components/schemas/{SEARCH_FIELD_ID_ENUM_NAME}",
                },
                "operator": {
                    "$ref": f"#/components/schemas/{SEARCH_OPERATOR_ENUM_NAME}",
                },
                "value": {
                    "nullable": True,
                    "description": (
                        "Filter value. Use a list for `in`, `not_in`, and `between`. "
                        "Omit for `is_empty` / `is_not_empty`."
                    ),
                    "oneOf": [
                        {"type": "string"},
                        {"type": "number"},
                        {"type": "integer"},
                        {"type": "boolean"},
                        {"type": "array", "items": {"type": "string"}},
                        {"type": "array", "items": {"type": "number"}},
                        {"type": "array", "items": {"type": "integer"}},
                    ],
                },
                "field_type": {
                    "$ref": f"#/components/schemas/{SEARCH_METADATA_FIELD_TYPE_ENUM_NAME}",
                },
            },
            "required": [
                "field",
                "operator",
            ],
            "additionalProperties": False,
        },
        SEARCH_CONDITION_SCHEMA_NAME: {
            "oneOf": [
                {
                    "$ref": f"#/components/schemas/{SEARCH_RULE_SCHEMA_NAME}",
                },
                {
                    "$ref": f"#/components/schemas/{SEARCH_GROUP_SCHEMA_NAME}",
                },
            ],
            "description": "A single condition in a search group: either a rule or a nested group.",
        },
        SEARCH_GROUP_SCHEMA_NAME: {
            "type": "object",
            "description": (
                "Nested condition group. Combine child rules/groups with `AND` or `OR`. "
                "`operator` may be omitted when the group contains exactly one condition; the backend defaults it to `AND`."
            ),
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": [
                        "group",
                    ],
                    "default": "group",
                    "description": (
                        "Optional. If omitted, the backend still treats this object as a group when `conditions` exists."
                    ),
                },
                "operator": {
                    "$ref": f"#/components/schemas/{SEARCH_LOGICAL_OPERATOR_ENUM_NAME}",
                    "description": (
                        "How child conditions are combined. Optional when the group has exactly one condition; "
                        "required otherwise."
                    ),
                },
                "conditions": {
                    "type": "array",
                    "minItems": 1,
                    "description": "Child rules and/or nested groups.",
                    "items": {
                        "$ref": f"#/components/schemas/{SEARCH_CONDITION_SCHEMA_NAME}",
                    },
                },
            },
            "required": [
                "conditions",
            ],
            "additionalProperties": False,
        },
    },
}
