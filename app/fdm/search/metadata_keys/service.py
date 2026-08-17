from fdm.search.metadata_keys.explicit_resolver import TEMPLATE_SCOPE, ExplicitTemplateResolver
from fdm.search.metadata_keys.extractor import extract_keys
from fdm.search.metadata_keys.resolver import (
    DATASET_SCOPE,
    FOLDER_SCOPE,
    PROJECT_SCOPE,
    ActiveTemplateScopeResolver,
)

SOURCE_ORDER = (TEMPLATE_SCOPE, PROJECT_SCOPE, FOLDER_SCOPE, DATASET_SCOPE)


def collect_metadata_key_suggestions(
    scopes: list[tuple[str, str]],
    user,
    template_ids: list | None = None,
) -> list[dict]:
    template_ids = template_ids or []
    buckets: dict[str, list[dict]] = {source: [] for source in SOURCE_ORDER}
    seen: set[tuple] = set()

    if template_ids:
        explicit = ExplicitTemplateResolver().resolve(template_ids)
        for template, source, source_id in explicit:
            dedupe_key = (template.pk, source, str(source_id))
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            buckets[source].extend(extract_keys(template, source, source_id))

    template_key_filter: set[str] | None = {row["key"] for row in buckets[TEMPLATE_SCOPE]} if template_ids else None

    if scopes:
        resolver = ActiveTemplateScopeResolver(user)
        for scope_kind, scope_id in scopes:
            for template, source, source_id in resolver.resolve(scope_kind, scope_id):
                dedupe_key = (template.pk, source, str(source_id))
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                rows = extract_keys(template, source, source_id)
                if template_key_filter is not None:
                    rows = [row for row in rows if row["key"] in template_key_filter]
                buckets[source].extend(rows)

    ordered: list[dict] = []
    for source in SOURCE_ORDER:
        ordered.extend(buckets[source])

    return [
        {
            "key": row["key"],
            "field_type": row["field_type"],
            "source": row["source"],
            "source_id": row["source_id"],
            "is_linked": row["is_linked"],
        }
        for row in ordered
    ]
