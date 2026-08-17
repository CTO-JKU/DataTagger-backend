from fdm.metadata.models import MetadataTemplate


def extract_keys(template: MetadataTemplate, source: str, source_id) -> list[dict]:
    rows = []
    for field in template.metadata_template_fields.select_related("field").order_by("order"):
        key = field.custom_key if field.custom_key else field.field.key
        rows.append(
            {
                "key": key,
                "field_type": field.field_type,
                "source": source,
                "source_id": str(source_id),
                "is_linked": field.field_id is not None,
                "order": field.order,
            },
        )
    return rows
