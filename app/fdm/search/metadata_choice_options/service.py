from django.db.models import Q

from fdm.folders.models import Folder
from fdm.metadata.models import MetadataTemplate, MetadataTemplateField
from fdm.projects.models import Project
from fdm.search.metadata_keys.explicit_resolver import TEMPLATE_SCOPE, ExplicitTemplateResolver
from fdm.search.metadata_keys.resolver import (
    DATASET_SCOPE,
    FOLDER_SCOPE,
    PROJECT_SCOPE,
    ActiveTemplateScopeResolver,
)
from fdm.uploads.models import UploadsDataset


def collect_metadata_choice_options(
    keys: list[str],
    field_type: str,
    scopes: list[tuple[str, object]],
    user,
    template_ids: list | None = None,
) -> list[dict]:
    pairs = _resolve_template_pairs(scopes, user, template_ids or [])
    ordered_keys = list(dict.fromkeys(keys))
    fields_by_template_and_key = _choice_fields_by_template_and_key(
        ordered_keys,
        field_type,
        [template.pk for template, _, _, _ in pairs],
    )
    results = []

    for key in ordered_keys:
        for template, source, source_id, source_name in pairs:
            for field in fields_by_template_and_key.get((template.pk, key.casefold()), []):
                options = field.config.get("options") if isinstance(field.config, dict) else None
                if not isinstance(options, list):
                    continue
                results.append(
                    {
                        "key": field.custom_key if field.custom_key else field.field.key,
                        "field_type": field.field_type,
                        "source": source,
                        "source_id": str(source_id),
                        "metadata_template_id": str(template.pk),
                        "metadata_template_name": template.name,
                        "metadata_template_container_name": template.metadata_template_container.name,
                        "metadata_template_field_id": str(field.pk),
                        "source_name": source_name,
                        "options": options,
                    },
                )

    return results


def _choice_fields_by_template_and_key(keys, field_type, template_ids):
    if not keys or not template_ids:
        return {}

    key_filter = Q()
    for key in keys:
        key_filter |= Q(custom_key__iexact=key) | Q(field__key__iexact=key)

    fields = (
        MetadataTemplateField.objects.select_related("field")
        .filter(
            key_filter,
            metadata_template_id__in=template_ids,
            field_type=field_type,
        )
        .order_by("metadata_template_id", "order", "pk")
    )
    normalized_keys = list(dict.fromkeys(key.casefold() for key in keys))
    fields_by_template_and_key = {}
    for field in fields:
        field_keys = {
            value.casefold()
            for value in (field.custom_key, field.field.key if field.field else None)
            if value is not None
        }
        for normalized_key in normalized_keys:
            if normalized_key in field_keys:
                fields_by_template_and_key.setdefault((field.metadata_template_id, normalized_key), []).append(field)

    return fields_by_template_and_key


def _resolve_template_pairs(scopes, user, template_ids):
    pairs = []
    seen = set()

    if template_ids:
        pairs.extend(ExplicitTemplateResolver().resolve(template_ids))

    if scopes:
        resolver = ActiveTemplateScopeResolver(user)
        for scope_kind, scope_id in scopes:
            pairs.extend(resolver.resolve(scope_kind, scope_id))

    if not scopes and not template_ids:
        from fdm.search.services import build_viewable_templates_queryset

        containers = (
            build_viewable_templates_queryset()
            .exclude(search_latest_template_pk__isnull=True)
            .order_by(
                "creation_date",
                "pk",
            )
        )
        latest_ids = [container.search_latest_template_pk for container in containers]
        templates = {template.pk: template for template in MetadataTemplate.objects.filter(pk__in=latest_ids)}
        pairs = [
            (templates[container.search_latest_template_pk], TEMPLATE_SCOPE, container.pk)
            for container in containers
            if container.search_latest_template_pk in templates
        ]

    ordered_pairs = []
    for template, source, source_id in pairs:
        dedupe_key = (template.pk, source, str(source_id))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        ordered_pairs.append((template, source, source_id))

    templates_by_id = MetadataTemplate.objects.select_related("metadata_template_container").in_bulk(
        template.pk for template, _, _ in ordered_pairs
    )
    source_models_and_name_fields = {
        PROJECT_SCOPE: (Project, "name"),
        FOLDER_SCOPE: (Folder, "name"),
        DATASET_SCOPE: (UploadsDataset, "display_name"),
    }
    source_names = {}
    for source, (model, name_field) in source_models_and_name_fields.items():
        source_ids = [source_id for _, pair_source, source_id in ordered_pairs if pair_source == source]
        source_names.update(
            ((source, str(source_id)), name)
            for source_id, name in model.objects.filter(pk__in=source_ids).values_list("pk", name_field)
        )

    return [
        (
            templates_by_id[template.pk],
            source,
            source_id,
            (
                templates_by_id[template.pk].metadata_template_container.name
                if source == TEMPLATE_SCOPE
                else source_names.get((source, str(source_id))) or str(source_id)
            ),
        )
        for template, source, source_id in ordered_pairs
    ]
