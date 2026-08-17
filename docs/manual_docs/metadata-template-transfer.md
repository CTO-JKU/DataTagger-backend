# Metadata template container transfer

Metadata template container transfer moves the current definition of a metadata
template container between scopes or systems. It does not move version history.

## Export

Create a metadata template export job:

`POST /api/v1/metadata-template-export/`

```json
{
  "metadata_template_containers": [
    "00000000-0000-0000-0000-000000000000"
  ]
}
```

The response is an asynchronous export job with a status and, when finished, a
download URL. The backend builds a ZIP file and sends a ready email when the ZIP
is available. The download expires using the same lifetime setting as other
exports.

The ZIP is always flat. It contains one JSON file per selected metadata template
container. Filenames are based on metadata template container names and receive
suffixes such as ` (1)` and ` (2)` when names collide inside the ZIP.

Each JSON file contains one create-compatible payload:

```json
{
  "name": "Lab sample template",
  "metadata_template_fields": [
    {
      "field": "00000000-0000-0000-0000-000000000000",
      "field_type": "text",
      "value": "default value",
      "config": {},
      "mandatory": true,
      "order": 1
    },
    {
      "custom_key": "sample_count",
      "field_type": "integer",
      "value": "1",
      "config": {},
      "mandatory": false,
      "order": 2
    }
  ]
}
```

Only the latest metadata template in the metadata template container is
exported. Source scope, source object IDs, source metadata template container
IDs, source metadata template IDs, source metadata template field IDs,
previous-version references, user fields, timestamps, locks, pool
relationships, and version history are not exported.

When a metadata template field references a global metadata field, `field`
contains that global metadata field UUID. This UUID must exist in the target
system for import to succeed.

## Import

Import accepts one JSON file only:

`POST /api/v1/metadata-template-container/import/`

Use multipart form data:

- `file`: required JSON file
- `assigned_to_content_type`: optional target scope content type
- `assigned_to_object_id`: optional target scope object ID

The two scope fields must be provided together. If both are omitted, the import
creates a global metadata template container. The uploaded JSON file must not
contain scope fields.

Import creates a fresh metadata template container with one initial metadata
template. If another metadata template container with the same name already
exists in the target scope, the imported name receives the first available
suffix: `Name (1)`, `Name (2)`, and so on.

Import is atomic. If parsing, validation, permissions, name conflict handling,
or field creation fails, no metadata template container or partial fields are
created.
