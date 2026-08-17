# Structured search API

## Endpoint

`POST /api/v1/search/global/`

This endpoint performs a unified search across multiple entity types:

- `project`
- `folder`
- `dataset`
- `dataset_version`
- `file`
- `template`
- `template_version`

`version` is no longer a valid `result_type`. Use `dataset_version` instead.

You can use it in three modes:

- Broad search only via `search_text`
- Structured filtering only via `group`
- Broad search plus structured filtering together

The response is always one mixed result list. Each row contains a `result_type`
and the serialized entity under `item`.

Filter discovery endpoint:

- `GET /api/v1/search/global/schema/`

## Request body

```json
{
  "result_types": ["project", "folder", "dataset", "dataset_version", "file", "template", "template_version"],
  "search_text": "Horizon 2.5",
  "limit": 25,
  "cursor": "optional-opaque-cursor",
  "cursor_direction": "next",
  "sort": [
    {"field": "relevance", "direction": "desc"},
    {"field": "updated_at", "direction": "desc"},
    {"field": "created_at", "direction": "desc"}
  ],
  "response_mode": "full",
  "group": {
    "operator": "AND",
    "conditions": [
      {
        "field": "description",
        "operator": "contains",
        "value": "consortium"
      },
      {
        "kind": "group",
        "operator": "OR",
        "conditions": [
          {
            "field": "my_project_permission",
            "operator": "is",
            "value": "project_admin"
          },
          {
            "field": "my_folder_permission",
            "operator": "is",
            "value": "editor"
          }
        ]
      }
    ]
  }
}
```

## Top-level fields

### `result_types`

Optional list of entity types to search in.

Allowed values:

- `project`
- `folder`
- `dataset`
- `dataset_version`
- `file`
- `template`
- `template_version`

Default:

```json
["project", "folder", "dataset", "dataset_version", "file", "template", "template_version"]
```

### `search_text`

Optional broad text search string.

This searches the main text fields relevant to each selected result type, for
example names, descriptions, related names, metadata text, and file path/name.
For `dataset`, child-derived matches come from the latest dataset version only:
latest version name, latest version file, and latest version metadata.
Older version history is only searched through `dataset_version`.
For `template`, this searches the logical template container name, the latest
template version name, and the latest template version fields.
For `template_version`, this searches each template version row independently.

### `limit`

Optional page size.

- Minimum: `1`
- Maximum: `100`
- Default: `25`

### `cursor`

Optional opaque cursor returned by a previous search response.

Use this together with `cursor_direction` to continue forward or backward
through the same normalized search request.

### `cursor_direction`

Optional cursor pagination direction.

- Allowed values: `next`, `previous`
- Default: `next`

### `sort`

Optional ordered list of sort rules.

Allowed sort fields:

- `relevance`
- `updated_at`
- `created_at`

Allowed directions:

- `asc`
- `desc`

Default:

```json
[
  {"field": "relevance", "direction": "desc"},
  {"field": "updated_at", "direction": "desc"},
  {"field": "created_at", "direction": "desc"}
]
```

### `group`

Optional structured filter tree.

If you omit both `search_text` and `group`, the request is invalid.

### `response_mode`

Optional response payload mode.

Allowed values:

- `full`
- `compact`

Default:

```json
"full"
```

You can also send `?view=compact` as a query parameter.

## `group` grammar

A `group` is a nested boolean expression.

Each group contains:

- `operator`: `AND` or `OR`
- `conditions`: an array of rules or nested groups

If a group contains exactly one condition, `operator` may be omitted and the
backend defaults it to `AND`.

### Rule shape

```json
{
  "kind": "rule",
  "field": "metadata.key",
  "operator": "is",
  "value": "Author"
}
```

`kind` is optional. If omitted, the backend still treats the object as a rule.

### Group shape

```json
{
  "kind": "group",
  "operator": "AND",
  "conditions": [
    {"field": "name", "operator": "contains", "value": "Horizon"},
    {"field": "project_name", "operator": "starts_with", "value": "EU"}
  ]
}
```

`kind` is optional. If omitted and `conditions` is present, the backend treats
the object as a group.

## Supported filter fields

### Text fields

- `name`
- `description`
- `project_name`
- `folder_name`
- `dataset_name`
- `dataset_version_name`
- `file_name`
- `template_name`
- `template_version_name`

Supported operators:

- `contains`
- `not_contains`
- `is`
- `is_not`
- `starts_with`
- `ends_with`
- `in`
- `not_in`
- `is_empty`
- `is_not_empty`

### Scope fields

- `project_id`
- `folder_id`
- `dataset_id`
- `dataset_version_id`
- `template_id`
- `template_version_id`

Supported operators:

- `is`
- `is_not`
- `in`
- `not_in`

### Metadata fields

- `metadata.key`
- `metadata.value`
- `metadata.field_type`
- `metadata.source`
- `metadata.template_name`
- `metadata.template_id`
- `template_type`

Text-style operators:

- `contains`
- `not_contains`
- `is`
- `is_not`
- `starts_with`
- `ends_with`
- `in`
- `not_in`
- `is_empty`
- `is_not_empty`

Typed comparison operators for `metadata.value`:

- `is`
- `is_not`
- `lt`
- `lte`
- `gt`
- `gte`
- `between`

Important rule:

Typed comparison on `metadata.value` is only allowed when the same metadata
group also contains an exact `metadata.key` filter, for example:

```json
{
  "operator": "AND",
  "conditions": [
    {"field": "metadata.key", "operator": "is", "value": "SampleCount"},
    {"field": "metadata.value", "operator": "gt", "value": 100}
  ]
}
```

Typed equality works the same way:

```json
{
  "operator": "AND",
  "conditions": [
    {"field": "metadata.key", "operator": "is", "value": "SampleCount"},
    {"field": "metadata.value", "operator": "is", "value": 150}
  ]
}
```

Allowed `metadata.field_type` values include:

- `TEXT`
- `INTEGER`
- `DECIMAL`
- `DATE`
- `DATETIME`

Supported operators for `metadata.field_type`:

- `is`
- `is_not`
- `in`
- `not_in`

Allowed `metadata.source` values:

- `instance`
- `template`
- `system`
- `either`

`metadata.source` means metadata provenance on `dataset` and
`dataset_version` results:

- `instance`: metadata authored directly on the latest dataset version or on a dataset version result
- `template`: metadata originating from a template field, including copied/materialized metadata rows linked by `metadata_template_field`
- `system`: system-generated read-only metadata
- `either`: both provenance buckets

For `dataset`, this applies to the latest dataset version only.

Allowed `template_type` values:

- `global`
- `project`
- `folder`
- `dataset`

Supported operators for `template_type`:

- `is`
- `is_not`
- `in`
- `not_in`

### Permission fields

- `my_project_permission`
- `my_folder_permission`

Supported operators:

- `is`
- `is_not`
- `in`
- `not_in`

Allowed `my_project_permission` values:

- `project_admin`
- `member`
- `member_create_folders`
- `member_metadata_template_admin`
- `member_extended`

Allowed `my_folder_permission` values:

- `admin`
- `editor`
- `viewer`

### State fields

- `is_published`
- `metadata_is_complete`
- `has_upgradeable_metadata_templates`
- `has_file`

Supported operators for boolean fields:

- `is`
- `is_not`

`metadata_is_complete` is available on `dataset` and `dataset_version`
results. On `dataset`, it evaluates the latest version only. versionless
datasets have no Metadata Completeness value: they match neither
`metadata_is_complete is true` nor `metadata_is_complete is false`, but they
do match `metadata_is_complete is_not true` and
`metadata_is_complete is_not false`.

`has_upgradeable_metadata_templates` is available on `dataset` results only
and exposes whether the latest version uses an Upgradeable Metadata Template.

### Timestamp fields

- `publication_date`
- `expiry_date`
- `created_at`
- `updated_at`

Supported operators:

- `is`
- `is_not`
- `lt`
- `lte`
- `gt`
- `gte`
- `between`
- `is_empty`
- `is_not_empty`

These values must be ISO datetime strings.

## Metadata matching semantics

Metadata filters are correlated to the same metadata row.

That means this query:

```json
{
  "operator": "AND",
  "conditions": [
    {"field": "metadata.key", "operator": "is", "value": "Author"},
    {"field": "metadata.value", "operator": "contains", "value": "research-team"}
  ]
}
```

matches only when a single metadata entry has both:

- key = `Author`
- value containing `research-team`

It does not match when one metadata row has the key and a different metadata row
has the value.

## Response body

```json
{
  "count": 123,
  "next_cursor": "opaque-next-cursor",
  "previous_cursor": null,
  "results": [
    {
      "result_type": "project",
      "item": {
        "pk": "..."
      }
    }
  ]
}
```

### Response fields

- `count`: total number of matching results across the selected result types
- `next_cursor`: opaque cursor for the next page or `null`
- `previous_cursor`: opaque cursor for the previous page or `null`
- `results`: ordered page of mixed entity results

Each result contains:

- `result_type`: one of `project`, `folder`, `dataset`, `dataset_version`, `file`, `template`, `template_version`
- `item`: serializer output for that entity type

When `response_mode` is `compact`, `item` contains a smaller card-oriented
payload instead of the full search serializer output.

For `template` results, full payloads include:

- template container `pk`
- logical template `name`
- resolved `template_type`
- `assigned_to` summary for non-global templates
- `field_count`
- `latest_template` summary

For `template_version` results, full payloads include:

- template version `pk`
- template version/display `name`
- parent `template` summary
- resolved `template_type`
- `assigned_to` summary for non-global templates
- `field_count`

## Examples

### Broad search

```json
{
  "search_text": "Horizon 2.5"
}
```

### Filter by metadata key/value

```json
{
  "result_types": ["dataset_version"],
  "group": {
    "operator": "AND",
    "conditions": [
      {"field": "metadata.key", "operator": "is", "value": "Author"},
      {"field": "metadata.value", "operator": "contains", "value": "research-team"}
    ]
  }
}
```

### Filter by scope

```json
{
  "result_types": ["dataset_version"],
  "group": {
    "conditions": [
      {"field": "dataset_id", "operator": "is", "value": "d7390e83-3d97-4e33-9e72-ebb93b7769fd"}
    ]
  },
  "response_mode": "compact"
}
```

### Filter by metadata source

```json
{
  "result_types": ["dataset"],
  "group": {
    "operator": "AND",
    "conditions": [
      {"field": "metadata.key", "operator": "is", "value": "SampleCount"},
      {"field": "metadata.source", "operator": "is", "value": "template"}
    ]
  }
}
```

With broad search, `metadata.source` only constrains metadata text matches. Other
searchable fields like latest version names, dataset names, and latest file
names still match normally.

### Filter metadata templates

```json
{
  "result_types": ["template"],
  "group": {
    "operator": "AND",
    "conditions": [
      {"field": "template_type", "operator": "is", "value": "project"},
      {"field": "template_name", "operator": "contains", "value": "Search"}
    ]
  }
}
```

### Filter by metadata field type

```json
{
  "result_types": ["template", "dataset_version"],
  "group": {
    "conditions": [
      {"field": "metadata.field_type", "operator": "is", "value": "INTEGER"}
    ]
  }
}
```

### Filter by numeric metadata

```json
{
  "result_types": ["dataset_version"],
  "group": {
    "operator": "AND",
    "conditions": [
      {"field": "metadata.key", "operator": "is", "value": "SampleCount"},
      {"field": "metadata.value", "operator": "gt", "value": 100}
    ]
  }
}
```

### Filter by exact numeric metadata

```json
{
  "result_types": ["dataset_version"],
  "group": {
    "operator": "AND",
    "conditions": [
      {"field": "metadata.key", "operator": "is", "value": "SampleCount"},
      {"field": "metadata.value", "operator": "is", "value": 150}
    ]
  }
}
```

### Filter by permissions

```json
{
  "result_types": ["project", "folder"],
  "group": {
    "operator": "OR",
    "conditions": [
      {"field": "my_project_permission", "operator": "is", "value": "project_admin"},
      {"field": "my_folder_permission", "operator": "is", "value": "editor"}
    ]
  }
}
```

### Combine broad search with structured filters

```json
{
  "search_text": "Horizon",
  "group": {
    "operator": "AND",
    "conditions": [
      {"field": "description", "operator": "contains", "value": "consortium"},
      {
        "operator": "OR",
        "conditions": [
          {"field": "project_name", "operator": "starts_with", "value": "EU"},
          {"field": "folder_name", "operator": "contains", "value": "Research"}
        ]
      }
    ]
  }
}
```

## Notes

- Access control is always enforced. You only get back entities you can already
  view.
- `file_name` matches against the stored upload path and returns the file
  serializer result for the matching file.
- `metadata.source` is for dataset and dataset_version metadata provenance, not
  raw storage location. For dataset results it applies to the latest version
  view only. Template-derived metadata still counts as `template` even if it
  has been materialized into a concrete `Metadata` row on the version.
- Use `template` in `result_types` and `template_type` when searching templates
  themselves.
- `template` search results are logical template containers. Metadata filters on
  template searches apply to the latest template version in each container.
- `template_version` search results are individual historical template versions.
- Default ordering favors relevance, then newer results.
