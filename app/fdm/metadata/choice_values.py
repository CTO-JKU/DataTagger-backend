from copy import deepcopy

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from fdm.metadata.enums import MetadataFieldType

CHOICE_FIELD_TYPES = {
    MetadataFieldType.SELECTION,
    MetadataFieldType.MULTI_SELECTION,
    MetadataFieldType.TAG,
}

MULTI_VALUE_CHOICE_FIELD_TYPES = {
    MetadataFieldType.MULTI_SELECTION,
    MetadataFieldType.TAG,
}


def canonicalize_choice_literal(value) -> str:
    canonical_value = str(value).strip()
    if not canonical_value:
        raise ValidationError(_("Choice values must not be empty after trimming."))

    return canonical_value


def _canonicalize_choice_literals(values: list, *, label: str) -> list[str]:
    canonical_values = []
    original_by_canonical_value = {}

    for value in values:
        canonical_value = canonicalize_choice_literal(value)
        if canonical_value in original_by_canonical_value and original_by_canonical_value[canonical_value] != value:
            raise ValidationError(
                _("Choice {label} must not resolve to the same value after trimming.").format(label=label),
            )

        original_by_canonical_value.setdefault(canonical_value, value)
        canonical_values.append(canonical_value)

    return canonical_values


def canonicalize_choice_field(*, field_type: str, value, config: dict | None) -> tuple[object, dict]:
    canonical_config = deepcopy(config) if isinstance(config, dict) else config
    if field_type not in CHOICE_FIELD_TYPES:
        return value, canonical_config

    if canonical_config is None:
        canonical_config = {}

    options = canonical_config.get("options") if isinstance(canonical_config, dict) else None
    if isinstance(options, list):
        canonical_config["options"] = _canonicalize_choice_literals(options, label=str(_("options")))

    if value is None:
        canonical_value = None
    elif field_type == MetadataFieldType.SELECTION:
        canonical_value = canonicalize_choice_literal(value)
    else:
        selected_values = value if isinstance(value, list) else [value]
        canonical_value = _canonicalize_choice_literals(selected_values, label=str(_("selected values")))

    canonical_options = canonical_config.get("options") if isinstance(canonical_config, dict) else None
    if isinstance(canonical_options, list) and canonical_value is not None:
        selected_values = canonical_value if field_type in MULTI_VALUE_CHOICE_FIELD_TYPES else [canonical_value]
        if not all(selected_value in canonical_options for selected_value in selected_values):
            raise ValidationError(_("Selected choice values must be included in the configured options."))

    return canonical_value, canonical_config
