from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.contrib.contenttypes.models import ContentType
from django.core import signing
from django.db.models import (
    Case,
    CharField,
    DateField,
    DateTimeField,
    DecimalField,
    IntegerField,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    TextField,
    Value,
    When,
)
from django.db.models.expressions import RawSQL
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, Coalesce
from django.utils.dateparse import parse_date, parse_datetime
from django_userforeignkey.request import get_current_user
from rest_framework import serializers

from fdm.folders.models import Folder, FolderPermission, Subfolder
from fdm.folders.rest.serializers import FolderSearchSerializer, SubfolderSearchSerializer
from fdm.metadata.enums import MetadataFieldType
from fdm.metadata.models import Metadata, MetadataTemplate, MetadataTemplateContainer, MetadataTemplateField
from fdm.metadata.rest.serializers import MetadataTemplateSearchSerializer, MetadataTemplateVersionSearchSerializer
from fdm.projects.models import Project, ProjectMembership
from fdm.projects.rest.serializers import ProjectSearchSerializer
from fdm.uploads.models import UploadsDataset, UploadsVersion, UploadsVersionFile
from fdm.uploads.rest.serializers import (
    UploadsDatasetSearchSerializer,
    UploadsVersionFileSearchSerializer,
    UploadsVersionSearchSerializer,
)

__all__ = [
    "DEFAULT_CURSOR_DIRECTION",
    "DEFAULT_RESULT_TYPES",
    "DEFAULT_RESPONSE_MODE",
    "DEFAULT_SORT",
    "SEARCH_CURSOR_DIRECTIONS",
    "SEARCH_RESULT_SERIALIZERS",
    "SEARCH_RESULT_TYPES",
    "SEARCH_RESPONSE_MODES",
    "SearchASTValidator",
    "SearchExecutor",
    "SearchFieldCatalog",
    "SearchFieldRegistry",
]


DEFAULT_RESULT_TYPES = [
    "project",
    "folder",
    "subfolder",
    "dataset",
    "dataset_version",
    "file",
    "template",
    "template_version",
]

DEFAULT_SORT = [
    {"field": "relevance", "direction": "desc"},
    {"field": "updated_at", "direction": "desc"},
    {"field": "created_at", "direction": "desc"},
]

DEFAULT_CURSOR_DIRECTION = "next"
DEFAULT_RESPONSE_MODE = "full"
SEARCH_CURSOR_DIRECTIONS = (
    "next",
    "previous",
)
SEARCH_RESPONSE_MODES = (
    "full",
    "compact",
)
SEARCH_CURSOR_SALT = "fdm.search.global.cursor"

SEARCH_RESULT_SERIALIZERS = {
    "project": ProjectSearchSerializer,
    "folder": FolderSearchSerializer,
    "subfolder": SubfolderSearchSerializer,
    "dataset": UploadsDatasetSearchSerializer,
    "dataset_version": UploadsVersionSearchSerializer,
    "file": UploadsVersionFileSearchSerializer,
    "template": MetadataTemplateSearchSerializer,
    "template_version": MetadataTemplateVersionSearchSerializer,
}

SEARCH_RESULT_TYPES = tuple(SEARCH_RESULT_SERIALIZERS.keys())

STRING_OPERATORS = {
    "contains",
    "not_contains",
    "is",
    "is_not",
    "starts_with",
    "ends_with",
    "in",
    "not_in",
    "is_empty",
    "is_not_empty",
}
DATETIME_OPERATORS = {
    "is",
    "is_not",
    "lt",
    "lte",
    "gt",
    "gte",
    "between",
    "is_empty",
    "is_not_empty",
}
PERMISSION_OPERATORS = {
    "is",
    "is_not",
    "in",
    "not_in",
}
IDENTIFIER_OPERATORS = {
    "is",
    "is_not",
    "in",
    "not_in",
}
BOOLEAN_OPERATORS = {
    "is",
    "is_not",
}
ENUM_OPERATORS = {
    "is",
    "is_not",
    "in",
    "not_in",
}
TYPED_METADATA_OPERATORS = {
    "is",
    "is_not",
    "lt",
    "lte",
    "gt",
    "gte",
    "between",
}
STRICT_TYPED_METADATA_OPERATORS = {
    "lt",
    "lte",
    "gt",
    "gte",
    "between",
}
CHOICE_SELECTION_OPERATORS = {
    "is",
    "is_not",
    "in",
    "not_in",
    "is_empty",
    "is_not_empty",
}
CHOICE_MULTI_VALUE_OPERATORS = {
    "contains_any",
    "contains_all",
    "contains_none",
    "is_exactly",
    "is_empty",
    "is_not_empty",
}
SCALAR_TYPED_METADATA_OPERATORS = TYPED_METADATA_OPERATORS | {
    "is_empty",
    "is_not_empty",
}
METADATA_OPERATORS_BY_FIELD_TYPE = {
    MetadataFieldType.INTEGER: SCALAR_TYPED_METADATA_OPERATORS,
    MetadataFieldType.DECIMAL: SCALAR_TYPED_METADATA_OPERATORS,
    MetadataFieldType.DATE: SCALAR_TYPED_METADATA_OPERATORS,
    MetadataFieldType.DATETIME: SCALAR_TYPED_METADATA_OPERATORS,
    MetadataFieldType.TIME: STRING_OPERATORS,
    MetadataFieldType.TEXT: STRING_OPERATORS,
    MetadataFieldType.WYSIWYG: STRING_OPERATORS,
    MetadataFieldType.SELECTION: CHOICE_SELECTION_OPERATORS,
    MetadataFieldType.MULTI_SELECTION: CHOICE_MULTI_VALUE_OPERATORS,
    MetadataFieldType.TAG: CHOICE_MULTI_VALUE_OPERATORS,
}
TYPE_AWARE_METADATA_OPERATORS = set().union(*METADATA_OPERATORS_BY_FIELD_TYPE.values())
STRICT_TYPE_AWARE_METADATA_OPERATORS = STRICT_TYPED_METADATA_OPERATORS | {
    "contains_any",
    "contains_all",
    "contains_none",
    "is_exactly",
}
NEGATIVE_CHOICE_OPERATOR_POSITIVE = {
    "is_not": "is",
    "not_in": "in",
    "contains_none": "contains_any",
}
METADATA_SOURCE_VALUES = {
    "instance",
    "template",
    "system",
    "either",
}
METADATA_FIELD_TYPE_VALUES = {choice for choice, _label in MetadataFieldType.choices}
TEMPLATE_TYPE_VALUES = {
    "global",
    "project",
    "folder",
    "dataset",
}

ENTITY_STRING_FIELD_PATHS = {
    "name": {
        "project": "name",
        "folder": "name",
        "subfolder": "name",
        "dataset": "name",
        "dataset_version": "name",
        "file": "uploaded_file",
        "template": "search_template_name",
        "template_version": "name",
    },
    "project_name": {
        "project": "name",
        "folder": "project__name",
        "subfolder": "folder__project__name",
        "dataset": "folder__project__name",
        "dataset_version": "dataset__folder__project__name",
        "file": "uploads_versions__dataset__folder__project__name",
    },
    "folder_name": {
        "folder": "name",
        "subfolder": "folder__name",
        "dataset": "folder__name",
        "dataset_version": "dataset__folder__name",
        "file": "uploads_versions__dataset__folder__name",
    },
    "dataset_name": {
        "dataset": "name",
        "dataset_version": "dataset__name",
        "file": "uploads_versions__dataset__name",
    },
    "dataset_version_name": {
        "dataset": "search_latest_version_name",
        "dataset_version": "name",
        "file": "uploads_versions__name",
    },
    "file_name": {
        "dataset": "search_latest_version_file_name",
        "dataset_version": "version_file__uploaded_file",
        "file": "uploaded_file",
    },
    "template_name": {
        "template": "search_template_name",
        "template_version": "metadata_template_container__name",
    },
    "template_version_name": {
        "template": "search_latest_template_name",
        "template_version": "search_template_version_name",
    },
}

ENTITY_IDENTIFIER_FIELD_PATHS = {
    "project_id": {
        "project": "pk",
        "folder": "project__pk",
        "subfolder": "folder__project__pk",
        "dataset": "folder__project__pk",
        "dataset_version": "dataset__folder__project__pk",
        "file": "uploads_versions__dataset__folder__project__pk",
    },
    "folder_id": {
        "folder": "pk",
        "subfolder": "folder__pk",
        "dataset": "folder__pk",
        "dataset_version": "dataset__folder__pk",
        "file": "uploads_versions__dataset__folder__pk",
    },
    "dataset_id": {
        "dataset": "pk",
        "dataset_version": "dataset__pk",
        "file": "uploads_versions__dataset__pk",
    },
    "dataset_version_id": {
        "dataset_version": "pk",
        "file": "uploads_versions__pk",
    },
    "template_id": {
        "template": "pk",
        "template_version": "metadata_template_container__pk",
    },
    "template_version_id": {
        "template_version": "pk",
    },
}

ENTITY_DATETIME_FIELD_PATHS = {
    "created_at": "creation_date",
    "updated_at": "last_modification_date",
}

FALSE_Q = Q(pk__in=[])


@dataclass(frozen=True)
class ResultTypeSpec:
    result_type: str
    model: type
    serializer_class: type
    base_queryset_factory: callable
    search_text_query_factory: callable
    primary_search_query_factory: callable
    hydration_queryset_factory: callable

    @property
    def content_type(self):
        return ContentType.objects.get_for_model(self.model)


def combine_predicates(operator: str, predicates: list[Q]) -> Q:
    if not predicates:
        return FALSE_Q

    combined = predicates[0]
    for predicate in predicates[1:]:
        if operator == "AND":
            combined &= predicate
        else:
            combined |= predicate
    return combined


def build_string_query(field_name: str, operator: str, value) -> Q:
    if operator in {"contains", "not_contains", "is", "is_not", "starts_with", "ends_with"}:
        if not isinstance(value, str):
            raise serializers.ValidationError("This operator requires a string value.")

    if operator in {"in", "not_in"}:
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise serializers.ValidationError("This operator requires a list of string values.")

    if operator == "contains":
        return Q(**{f"{field_name}__icontains": value})
    if operator == "not_contains":
        return ~Q(**{f"{field_name}__icontains": value})
    if operator == "is":
        return Q(**{f"{field_name}__iexact": value})
    if operator == "is_not":
        return ~Q(**{f"{field_name}__iexact": value})
    if operator == "starts_with":
        return Q(**{f"{field_name}__istartswith": value})
    if operator == "ends_with":
        return Q(**{f"{field_name}__iendswith": value})
    if operator == "in":
        return combine_predicates(
            "OR",
            [Q(**{f"{field_name}__iexact": item}) for item in value],
        )
    if operator == "not_in":
        return ~combine_predicates(
            "OR",
            [Q(**{f"{field_name}__iexact": item}) for item in value],
        )
    if operator == "is_empty":
        return Q(**{f"{field_name}__isnull": True}) | Q(**{field_name: ""})
    if operator == "is_not_empty":
        return ~(Q(**{f"{field_name}__isnull": True}) | Q(**{field_name: ""}))

    raise serializers.ValidationError("Unsupported operator for text field.")


def parse_choice_state_requirements(value, error_message: str) -> tuple[list[str], list[str]]:
    if not isinstance(value, dict) or set(value) != {"selected", "unselected"}:
        raise serializers.ValidationError(error_message)

    selected = value["selected"]
    unselected = value["unselected"]
    if (
        not isinstance(selected, list)
        or not all(isinstance(item, str) for item in selected)
        or not isinstance(unselected, list)
        or not all(isinstance(item, str) for item in unselected)
    ):
        raise serializers.ValidationError(error_message)

    unique_selected = list(dict.fromkeys(selected))
    unique_unselected = list(dict.fromkeys(unselected))
    if not unique_selected and not unique_unselected:
        raise serializers.ValidationError(error_message)
    if set(unique_selected) & set(unique_unselected):
        raise serializers.ValidationError("A choice value cannot be required as both selected and unselected.")

    return unique_selected, unique_unselected


def build_choice_state_predicates(
    field_type: str,
    selected: list[str],
    unselected: list[str],
) -> list[Q]:
    value_field = "value__value"
    is_selection = field_type == MetadataFieldType.SELECTION

    def selected_predicate(item: str) -> Q:
        return Q(**{value_field: item}) if is_selection else Q(**{f"{value_field}__contains": [item]})

    predicates = [selected_predicate(item) for item in selected]
    predicates.extend(Q(config__options__contains=[item]) & ~selected_predicate(item) for item in unselected)
    return predicates


def build_choice_metadata_value_query(field_type: str, operator: str, value) -> Q:
    value_field = "value__value"

    if field_type == MetadataFieldType.SELECTION:
        if operator in {"is", "is_not"}:
            if not isinstance(value, str):
                raise serializers.ValidationError("This selection operator requires a string value.")
            predicate = Q(**{value_field: value})
            return ~predicate if operator == "is_not" else predicate
        if operator in {"in", "not_in"}:
            selected, unselected = parse_choice_state_requirements(
                value,
                "This selection operator requires selected and unselected lists of string values.",
            )
            predicate = combine_predicates(
                "OR",
                build_choice_state_predicates(field_type, selected, unselected),
            )
            return ~predicate if operator == "not_in" else predicate
        if operator == "is_empty":
            return Q(**{value_field: None}) | Q(**{value_field: ""})
        if operator == "is_not_empty":
            return ~(Q(**{value_field: None}) | Q(**{value_field: ""}))

    if field_type in {MetadataFieldType.MULTI_SELECTION, MetadataFieldType.TAG}:
        if operator in {"contains_any", "contains_all", "contains_none", "is_exactly"}:
            selected, unselected = parse_choice_state_requirements(
                value,
                "This multi-value choice operator requires selected and unselected lists of string values.",
            )
            state_predicates = build_choice_state_predicates(field_type, selected, unselected)
            if operator == "contains_any":
                return combine_predicates("OR", state_predicates)
            if operator == "contains_all":
                return combine_predicates("AND", state_predicates)
            if operator == "contains_none":
                return ~combine_predicates("OR", state_predicates)

            exact_selected_predicate = (
                Q(
                    **{
                        f"{value_field}__contains": selected,
                        "search_metadata_choice_distinct_count": len(selected),
                    },
                )
                if selected
                else Q(**{value_field: None}) | Q(**{value_field: []})
            )
            if unselected:
                exact_selected_predicate &= combine_predicates(
                    "AND",
                    build_choice_state_predicates(field_type, [], unselected),
                )
            return exact_selected_predicate
        if operator == "is_empty":
            return Q(**{value_field: None}) | Q(**{value_field: []})
        if operator == "is_not_empty":
            return ~(Q(**{value_field: None}) | Q(**{value_field: []}))

    raise serializers.ValidationError("Unsupported operator for this choice metadata field type.")


def parse_uuid_value(value):
    if not isinstance(value, str):
        raise serializers.ValidationError("This operator requires a UUID string value.")
    try:
        return str(UUID(value))
    except (TypeError, ValueError) as e:
        raise serializers.ValidationError("This operator requires a UUID string value.") from e


def build_identifier_query(field_name: str, operator: str, value) -> Q:
    if operator in {"is", "is_not"}:
        parsed_value = parse_uuid_value(value)
    elif operator in {"in", "not_in"}:
        if not isinstance(value, list) or not value:
            raise serializers.ValidationError("This operator requires a non-empty list of UUID string values.")
        parsed_value = [parse_uuid_value(item) for item in value]
    else:
        raise serializers.ValidationError("Unsupported operator for identifier field.")

    if operator == "is":
        return Q(**{field_name: parsed_value})
    if operator == "is_not":
        return ~Q(**{field_name: parsed_value})
    if operator == "in":
        return Q(**{f"{field_name}__in": parsed_value})
    if operator == "not_in":
        return ~Q(**{f"{field_name}__in": parsed_value})

    raise serializers.ValidationError("Unsupported operator for identifier field.")


def build_boolean_query(field_name: str, operator: str, value) -> Q:
    if not isinstance(value, bool):
        raise serializers.ValidationError("This operator requires a boolean value.")

    if operator == "is":
        return Q(**{field_name: value})
    if operator == "is_not":
        return ~Q(**{field_name: value})

    raise serializers.ValidationError("Unsupported operator for boolean field.")


def build_enum_query(field_name: str, operator: str, value, allowed_values: set[str]) -> Q:
    if operator in {"is", "is_not"}:
        if not isinstance(value, str) or value not in allowed_values:
            raise serializers.ValidationError("This operator requires a supported enum value.")
        parsed_value = value
    elif operator in {"in", "not_in"}:
        if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
            raise serializers.ValidationError("This operator requires a non-empty list of supported enum values.")
        if not all(item in allowed_values for item in value):
            raise serializers.ValidationError("This operator requires a non-empty list of supported enum values.")
        parsed_value = value
    else:
        raise serializers.ValidationError("Unsupported operator for enum field.")

    if operator == "is":
        return Q(**{field_name: parsed_value})
    if operator == "is_not":
        return ~Q(**{field_name: parsed_value})
    if operator == "in":
        return Q(**{f"{field_name}__in": parsed_value})
    if operator == "not_in":
        return ~Q(**{f"{field_name}__in": parsed_value})

    raise serializers.ValidationError("Unsupported operator for enum field.")


def build_metadata_field_type_query(operator: str, value) -> Q:
    return build_enum_query("field_type", operator, value, METADATA_FIELD_TYPE_VALUES)


def build_metadata_source_query(operator: str, value) -> Q:
    values = value if isinstance(value, list) else [value]
    if not values or not all(isinstance(item, str) and item in METADATA_SOURCE_VALUES for item in values):
        raise serializers.ValidationError("Unsupported enum value.")

    if "either" in values:
        if operator in {"is", "in"}:
            return Q()
        if operator in {"is_not", "not_in"}:
            return FALSE_Q

    return build_enum_query(
        "search_metadata_source",
        operator,
        value,
        METADATA_SOURCE_VALUES,
    )


def build_template_type_predicate(operator: str, value, prefix: str = "") -> Q:
    values = value if isinstance(value, list) else [value]
    if not values or not all(isinstance(item, str) and item in TEMPLATE_TYPE_VALUES for item in values):
        raise serializers.ValidationError("Unsupported enum value.")

    project_content_type = ContentType.objects.get_for_model(Project)
    folder_content_type = ContentType.objects.get_for_model(Folder)
    dataset_content_type = ContentType.objects.get_for_model(UploadsDataset)

    predicates = []
    for template_type in values:
        if template_type == "global":
            predicates.append(
                Q(
                    **{
                        f"{prefix}assigned_to_content_type__isnull": True,
                        f"{prefix}assigned_to_object_id__isnull": True,
                    },
                ),
            )
        elif template_type == "project":
            predicates.append(
                Q(
                    **{
                        f"{prefix}assigned_to_content_type": project_content_type,
                        f"{prefix}assigned_to_object_id__isnull": False,
                    },
                ),
            )
        elif template_type == "folder":
            predicates.append(
                Q(
                    **{
                        f"{prefix}assigned_to_content_type": folder_content_type,
                        f"{prefix}assigned_to_object_id__isnull": False,
                    },
                ),
            )
        elif template_type == "dataset":
            predicates.append(
                Q(
                    **{
                        f"{prefix}assigned_to_content_type": dataset_content_type,
                        f"{prefix}assigned_to_object_id__isnull": False,
                    },
                ),
            )

    predicate = combine_predicates("OR", predicates)
    if operator in {"is_not", "not_in"}:
        return ~predicate
    return predicate


def build_template_scope_predicate(result_type: str, field_name: str, operator: str, value) -> Q:
    if result_type not in {"template", "template_version"}:
        return FALSE_Q

    if field_name == "project_id":
        content_type = ContentType.objects.get_for_model(Project)
    elif field_name == "folder_id":
        content_type = ContentType.objects.get_for_model(Folder)
    elif field_name == "dataset_id":
        content_type = ContentType.objects.get_for_model(UploadsDataset)
    else:
        return FALSE_Q

    prefix = "" if result_type == "template" else "metadata_template_container__"
    content_type_field = f"{prefix}assigned_to_content_type"
    object_id_field = f"{prefix}assigned_to_object_id"

    if operator in {"is", "is_not"}:
        parsed_value = parse_uuid_value(value)
        predicate = Q(**{content_type_field: content_type, object_id_field: parsed_value})
    elif operator in {"in", "not_in"}:
        if not isinstance(value, list) or not value:
            raise serializers.ValidationError("This operator requires a non-empty list of UUID string values.")
        parsed_value = [parse_uuid_value(item) for item in value]
        predicate = Q(**{content_type_field: content_type, f"{object_id_field}__in": parsed_value})
    else:
        raise serializers.ValidationError("Unsupported operator for identifier field.")

    if operator in {"is_not", "not_in"}:
        return ~predicate
    return predicate


def build_template_scope_name_predicate(result_type: str, field_name: str, operator: str, value) -> Q:
    if result_type not in {"template", "template_version"}:
        return FALSE_Q

    prefix = "" if result_type == "template" else "metadata_template_container__"
    content_type_field = f"{prefix}assigned_to_content_type"
    object_id_field = f"{prefix}assigned_to_object_id"

    project_ct = ContentType.objects.get_for_model(Project)
    folder_ct = ContentType.objects.get_for_model(Folder)
    dataset_ct = ContentType.objects.get_for_model(UploadsDataset)

    predicates = []

    if field_name == "project_name":
        predicates.append(
            Q(
                **{
                    content_type_field: project_ct,
                    f"{object_id_field}__in": Subquery(
                        Project.objects.filter(build_string_query("name", operator, value)).values("pk"),
                    ),
                },
            ),
        )
        predicates.append(
            Q(
                **{
                    content_type_field: folder_ct,
                    f"{object_id_field}__in": Subquery(
                        Folder.objects.filter(build_string_query("project__name", operator, value)).values("pk"),
                    ),
                },
            ),
        )
        predicates.append(
            Q(
                **{
                    content_type_field: dataset_ct,
                    f"{object_id_field}__in": Subquery(
                        UploadsDataset.objects.filter(
                            build_string_query("folder__project__name", operator, value),
                        ).values("pk"),
                    ),
                },
            ),
        )

    elif field_name == "folder_name":
        predicates.append(
            Q(
                **{
                    content_type_field: folder_ct,
                    f"{object_id_field}__in": Subquery(
                        Folder.objects.filter(build_string_query("name", operator, value)).values("pk"),
                    ),
                },
            ),
        )
        predicates.append(
            Q(
                **{
                    content_type_field: dataset_ct,
                    f"{object_id_field}__in": Subquery(
                        UploadsDataset.objects.filter(
                            build_string_query("folder__name", operator, value),
                        ).values("pk"),
                    ),
                },
            ),
        )

    elif field_name == "dataset_name":
        predicates.append(
            Q(
                **{
                    content_type_field: dataset_ct,
                    f"{object_id_field}__in": Subquery(
                        UploadsDataset.objects.filter(build_string_query("name", operator, value)).values("pk"),
                    ),
                },
            ),
        )

    else:
        return FALSE_Q

    return combine_predicates("OR", predicates)


def build_datetime_query(field_name: str, operator: str, value) -> Q:
    if operator in {"is", "is_not", "lt", "lte", "gt", "gte"}:
        parsed_value = parse_datetime(value) if isinstance(value, str) else None
        if parsed_value is None:
            raise serializers.ValidationError("This operator requires an ISO datetime string.")
    elif operator == "between":
        if not isinstance(value, list) or len(value) != 2:
            raise serializers.ValidationError("Between requires a list with two ISO datetime values.")
        parsed_value = [parse_datetime(item) if isinstance(item, str) else None for item in value]
        if any(item is None for item in parsed_value):
            raise serializers.ValidationError("Between requires a list with two ISO datetime values.")
    else:
        parsed_value = value

    if operator == "is":
        return Q(**{field_name: parsed_value})
    if operator == "is_not":
        return ~Q(**{field_name: parsed_value})
    if operator == "lt":
        return Q(**{f"{field_name}__lt": parsed_value})
    if operator == "lte":
        return Q(**{f"{field_name}__lte": parsed_value})
    if operator == "gt":
        return Q(**{f"{field_name}__gt": parsed_value})
    if operator == "gte":
        return Q(**{f"{field_name}__gte": parsed_value})
    if operator == "between":
        return Q(**{f"{field_name}__gte": parsed_value[0], f"{field_name}__lte": parsed_value[1]})
    if operator == "is_empty":
        return Q(**{f"{field_name}__isnull": True})
    if operator == "is_not_empty":
        return ~Q(**{f"{field_name}__isnull": True})

    raise serializers.ValidationError("Unsupported operator for datetime field.")


def filter_values_for_string_query(queryset: QuerySet, field_name: str, operator: str, value, values_field: str):
    return queryset.filter(build_string_query(field_name, operator, value)).values(values_field)


def filter_values_for_datetime_query(queryset: QuerySet, field_name: str, operator: str, value, values_field: str):
    return queryset.filter(build_datetime_query(field_name, operator, value)).values(values_field)


def build_description_predicate(base_queryset: QuerySet, operator: str, value) -> Q:
    described_queryset = base_queryset.annotate(
        search_description_text=Cast("description", output_field=TextField()),
    )
    return Q(
        pk__in=Subquery(
            filter_values_for_string_query(
                described_queryset,
                "search_description_text",
                operator,
                value,
                "pk",
            ),
        ),
    )


def annotate_metadata_text(queryset: QuerySet) -> QuerySet:
    return queryset.annotate(
        search_metadata_key=Coalesce("custom_key", "field__key"),
        search_metadata_value=Coalesce(
            KeyTextTransform("value", "value"),
            Cast("value", output_field=TextField()),
        ),
        search_metadata_source=Case(
            When(metadata_template_field__isnull=False, then=Value("template")),
            When(read_only=True, then=Value("system")),
            default=Value("instance"),
            output_field=CharField(),
        ),
    )


def annotate_metadata_template_field_text(queryset: QuerySet) -> QuerySet:
    return queryset.annotate(
        search_metadata_key=Coalesce("custom_key", "field__key"),
        search_metadata_value=Coalesce(
            KeyTextTransform("value", "value"),
            Cast("value", output_field=TextField()),
        ),
        search_metadata_source=Value("template", output_field=CharField()),
    )


def annotate_template_display_name(queryset: QuerySet) -> QuerySet:
    return queryset.annotate(
        search_template_name=Coalesce("name", "search_latest_template_name"),
    )


def annotate_template_container_search_fields(queryset: QuerySet) -> QuerySet:
    latest_template_queryset = MetadataTemplate.objects.filter(
        metadata_template_container=OuterRef("pk"),
    ).order_by("-creation_date")

    return annotate_template_display_name(
        queryset.annotate(
            search_latest_template_pk=Subquery(latest_template_queryset.values("pk")[:1]),
            search_latest_template_name=Subquery(latest_template_queryset.values("name")[:1]),
        ),
    )


def annotate_template_version_search_fields(queryset: QuerySet) -> QuerySet:
    return queryset.annotate(
        search_template_version_name=Coalesce("name", "metadata_template_container__name"),
    )


def annotate_dataset_search_fields(queryset: QuerySet) -> QuerySet:
    latest_version_queryset = UploadsVersion.objects.filter(
        dataset=OuterRef("pk"),
    ).order_by("-creation_date", "-pk")

    return queryset.annotate(
        search_latest_version_pk=Subquery(latest_version_queryset.values("pk")[:1]),
        search_latest_version_name=Subquery(latest_version_queryset.values("name")[:1]),
        search_latest_version_file_pk=Subquery(latest_version_queryset.values("version_file_id")[:1]),
        search_latest_version_file_name=Subquery(latest_version_queryset.values("version_file__uploaded_file")[:1]),
        search_latest_version_metadata_is_complete=Subquery(
            latest_version_queryset.values("metadata_is_complete")[:1],
        ),
    )


def normalize_metadata_source_values(source_values: set[str] | None) -> set[str] | None:
    if source_values is None:
        return None

    normalized_values = set(source_values)
    if "either" in normalized_values:
        return None

    normalized_values &= {
        "instance",
        "template",
    }
    if normalized_values == {
        "instance",
        "template",
    }:
        return None
    return normalized_values


def build_metadata_text_predicate_for_model(model: type, term: str, source_values: set[str] | None = None) -> Q:
    metadata_queryset = annotate_metadata_text(
        Metadata.objects.filter(
            assigned_to_content_type=ContentType.objects.get_for_model(model),
        ),
    )
    normalized_source_values = normalize_metadata_source_values(source_values)
    if normalized_source_values is not None:
        metadata_queryset = metadata_queryset.filter(search_metadata_source__in=normalized_source_values)
    return Q(
        pk__in=Subquery(
            metadata_queryset.filter(
                build_string_query("search_metadata_key", "contains", term)
                | build_string_query("search_metadata_value", "contains", term),
            ).values("assigned_to_object_id"),
        ),
    )


def build_description_search_predicate_for_model(model: type, term: str) -> Q:
    described_queryset = model.objects.annotate(
        search_description_text=Cast("description", output_field=TextField()),
    )
    return Q(
        pk__in=Subquery(
            described_queryset.filter(
                build_string_query("search_description_text", "contains", term),
            ).values("pk"),
        ),
    )


def build_permission_membership_queryset(permission_values: list[str]) -> QuerySet:
    current_user = get_current_user()
    queryset = ProjectMembership.objects.filter(member=current_user if current_user.pk else None)
    predicates = []
    for permission_value in permission_values:
        if permission_value == "project_admin":
            predicates.append(Q(is_project_admin=True))
        elif permission_value == "member":
            predicates.append(Q())
        elif permission_value == "member_create_folders":
            predicates.append(Q(can_create_folders=True))
        elif permission_value == "member_metadata_template_admin":
            predicates.append(Q(is_metadata_template_admin=True))
        elif permission_value == "member_extended":
            predicates.append(Q(can_create_folders=True) | Q(is_metadata_template_admin=True))
        else:
            raise serializers.ValidationError("Unsupported project permission value.")

    return queryset.filter(combine_predicates("OR", predicates))


def build_folder_permission_queryset(permission_values: list[str]) -> QuerySet:
    current_user = get_current_user()
    queryset = FolderPermission.objects.filter(project_membership__member=current_user if current_user.pk else None)
    predicates = []
    for permission_value in permission_values:
        if permission_value == "admin":
            predicates.append(Q(is_folder_admin=True))
        elif permission_value == "editor":
            predicates.append(Q(is_folder_admin=False, can_edit=True))
        elif permission_value == "viewer":
            predicates.append(Q(is_folder_admin=False, can_edit=False))
        else:
            raise serializers.ValidationError("Unsupported folder permission value.")

    return queryset.filter(combine_predicates("OR", predicates))


def build_project_permission_predicate(result_type: str, operator: str, value) -> Q:
    values = value if isinstance(value, list) else [value]
    values_path = {
        "project": "project_id",
        "folder": "project__folder__pk",
        "subfolder": "project__folder__subfolders__pk",
        "dataset": "project__folder__uploads_dataset__pk",
        "dataset_version": "project__folder__uploads_dataset__uploads_versions__pk",
        "file": "project__folder__uploads_dataset__uploads_versions__version_file__pk",
    }.get(result_type)
    if values_path is None:
        return FALSE_Q

    queryset = build_permission_membership_queryset(values).values(values_path)
    predicate = Q(pk__in=Subquery(queryset))
    if operator in {"is_not", "not_in"}:
        return ~predicate
    return predicate


def build_folder_permission_predicate(result_type: str, operator: str, value) -> Q:
    values = value if isinstance(value, list) else [value]
    values_path = {
        "folder": "folder_id",
        "subfolder": "folder__subfolders__pk",
        "dataset": "folder__uploads_dataset__pk",
        "dataset_version": "folder__uploads_dataset__uploads_versions__pk",
        "file": "folder__uploads_dataset__uploads_versions__version_file__pk",
    }.get(result_type)
    if values_path is None:
        return FALSE_Q

    queryset = build_folder_permission_queryset(values).values(values_path)
    predicate = Q(pk__in=Subquery(queryset))
    if operator in {"is_not", "not_in"}:
        return ~predicate
    return predicate


def build_is_published_predicate(result_type: str, operator: str, value) -> Q:
    if not isinstance(value, bool):
        raise serializers.ValidationError("This operator requires a boolean value.")

    publication_date_field = {
        "dataset": "publication_date",
        "dataset_version": "publication_date",
        "file": "publication_date",
    }.get(result_type)
    if publication_date_field is None:
        return FALSE_Q

    predicate = ~Q(**{f"{publication_date_field}__isnull": value})
    if operator == "is":
        return predicate
    if operator == "is_not":
        return ~predicate

    raise serializers.ValidationError("Unsupported operator for boolean field.")


def build_metadata_is_complete_predicate(result_type: str, operator: str, value) -> Q:
    if not isinstance(value, bool):
        raise serializers.ValidationError("This operator requires a boolean value.")

    if result_type == "dataset_version":
        return build_boolean_query("metadata_is_complete", operator, value)

    if result_type != "dataset":
        return FALSE_Q

    # NULL = "dataset has no versions"; `is_not` follows 3-VL and includes NULL.
    field_path = "search_latest_version_metadata_is_complete"
    isnull_path = f"{field_path}__isnull"

    if operator == "is":
        return Q(**{field_path: value})
    if operator == "is_not":
        return Q(**{field_path: not value}) | Q(**{isnull_path: True})

    raise serializers.ValidationError("Unsupported operator for boolean field.")


def build_has_upgradeable_metadata_templates_predicate(result_type: str, operator: str, value) -> Q:
    if not isinstance(value, bool):
        raise serializers.ValidationError("This operator requires a boolean value.")

    if result_type != "dataset":
        return FALSE_Q

    predicate = Q(has_upgradeable_metadata_templates=True)

    if operator == "is":
        return predicate if value else ~predicate
    if operator == "is_not":
        return ~predicate if value else predicate

    raise serializers.ValidationError("Unsupported operator for boolean field.")


def build_has_file_predicate(result_type: str, operator: str, value) -> Q:
    if not isinstance(value, bool):
        raise serializers.ValidationError("This operator requires a boolean value.")

    if result_type == "dataset_version":
        predicate = ~Q(version_file__isnull=True)
    elif result_type == "dataset":
        predicate = ~Q(search_latest_version_file_pk__isnull=True)
    else:
        return FALSE_Q

    if operator == "is":
        return predicate if value else ~predicate
    if operator == "is_not":
        return ~predicate if value else predicate

    raise serializers.ValidationError("Unsupported operator for boolean field.")


def build_project_search_text_query(term: str) -> Q:
    matching_folders = Folder.objects.annotate(
        search_description_text=Cast("description", output_field=TextField()),
    ).filter(
        Q(name__icontains=term)
        | build_string_query("search_description_text", "contains", term)
        | build_metadata_text_predicate_for_model(Folder, term),
    )
    return (
        Q(name__icontains=term)
        | build_description_search_predicate_for_model(Project, term)
        | build_metadata_text_predicate_for_model(Project, term)
        | Q(pk__in=Subquery(matching_folders.values("project_id")))
    )


def build_folder_search_text_query(term: str) -> Q:
    matching_datasets = UploadsDataset.objects.filter(
        Q(name__icontains=term)
        | Q(display_name__icontains=term)
        | Q(uploads_versions__name__icontains=term)
        | Q(uploads_versions__version_file__uploaded_file__icontains=term),
    )
    return (
        Q(name__icontains=term)
        | build_description_search_predicate_for_model(Folder, term)
        | Q(project__name__icontains=term)
        | build_metadata_text_predicate_for_model(Folder, term)
        | Q(pk__in=Subquery(matching_datasets.values("folder_id")))
    )


def build_subfolder_search_text_query(term: str) -> Q:
    return (
        Q(name__icontains=term)
        | Q(folder__name__icontains=term)
        | Q(folder__project__name__icontains=term)
        | build_metadata_text_predicate_for_model(Subfolder, term)
    )


def build_dataset_search_text_query(term: str) -> Q:
    return build_dataset_search_text_query_with_source(term)


def build_dataset_search_text_query_with_source(term: str, metadata_source_values: set[str] | None = None) -> Q:
    dataset_queryset = annotate_dataset_search_fields(UploadsDataset.objects.all())
    latest_version_ids = dataset_queryset.exclude(
        search_latest_version_pk__isnull=True,
    ).values("search_latest_version_pk")
    latest_file_ids = dataset_queryset.exclude(
        search_latest_version_file_pk__isnull=True,
    ).values("search_latest_version_file_pk")
    normalized_source_values = normalize_metadata_source_values(metadata_source_values)

    version_metadata_matches = annotate_metadata_text(
        Metadata.objects.filter(
            assigned_to_content_type=ContentType.objects.get_for_model(UploadsVersion),
            assigned_to_object_id__in=Subquery(latest_version_ids),
        ),
    )
    file_metadata_matches = annotate_metadata_text(
        Metadata.objects.filter(
            assigned_to_content_type=ContentType.objects.get_for_model(UploadsVersionFile),
            assigned_to_object_id__in=Subquery(latest_file_ids),
        ),
    )
    if normalized_source_values is not None:
        version_metadata_matches = version_metadata_matches.filter(search_metadata_source__in=normalized_source_values)
        file_metadata_matches = file_metadata_matches.filter(search_metadata_source__in=normalized_source_values)

    version_metadata_matches = version_metadata_matches.filter(
        build_string_query("search_metadata_key", "contains", term)
        | build_string_query("search_metadata_value", "contains", term),
    )
    file_metadata_matches = file_metadata_matches.filter(
        build_string_query("search_metadata_key", "contains", term)
        | build_string_query("search_metadata_value", "contains", term),
    )

    if normalized_source_values is None or "template" in normalized_source_values:
        version_template_metadata_matches = annotate_metadata_template_field_text(
            MetadataTemplateField.objects.filter(
                metadata_template__uploads_versions__pk__in=Subquery(latest_version_ids),
            ),
        ).filter(
            build_string_query("search_metadata_key", "contains", term)
            | build_string_query("search_metadata_value", "contains", term),
        )
        template_metadata_predicate = Q(
            pk__in=Subquery(
                UploadsVersion.objects.filter(
                    pk__in=Subquery(latest_version_ids),
                    metadata_templates__metadata_template_fields__pk__in=Subquery(
                        version_template_metadata_matches.values("pk"),
                    ),
                ).values("dataset_id"),
            ),
        )
    else:
        template_metadata_predicate = FALSE_Q

    return (
        Q(name__icontains=term)
        | Q(display_name__icontains=term)
        | Q(folder__name__icontains=term)
        | Q(folder__project__name__icontains=term)
        | Q(search_latest_version_name__icontains=term)
        | Q(search_latest_version_file_name__icontains=term)
        | Q(
            pk__in=Subquery(
                UploadsVersion.objects.filter(
                    pk__in=Subquery(version_metadata_matches.values("assigned_to_object_id")),
                ).values("dataset_id"),
            ),
        )
        | Q(
            pk__in=Subquery(
                UploadsVersion.objects.filter(
                    pk__in=Subquery(latest_version_ids),
                    version_file_id__in=Subquery(file_metadata_matches.values("assigned_to_object_id")),
                ).values("dataset_id"),
            ),
        )
        | template_metadata_predicate
    )


def build_dataset_version_search_text_query(term: str, metadata_source_values: set[str] | None = None) -> Q:
    normalized_source_values = normalize_metadata_source_values(metadata_source_values)
    file_metadata_matches = (
        Metadata.objects.filter(
            assigned_to_content_type=ContentType.objects.get_for_model(UploadsVersionFile),
        )
        .annotate(
            search_metadata_key=Coalesce("custom_key", "field__key"),
            search_metadata_value=Coalesce(
                KeyTextTransform("value", "value"),
                Cast("value", output_field=TextField()),
            ),
        )
        .filter(
            build_string_query("search_metadata_key", "contains", term)
            | build_string_query("search_metadata_value", "contains", term),
        )
    )
    version_template_metadata_matches = annotate_metadata_template_field_text(
        MetadataTemplateField.objects.filter(
            metadata_template__uploads_versions__isnull=False,
        ),
    ).filter(
        build_string_query("search_metadata_key", "contains", term)
        | build_string_query("search_metadata_value", "contains", term),
    )
    metadata_text_predicate = build_metadata_text_predicate_for_model(
        UploadsVersion,
        term,
        source_values=normalized_source_values,
    )
    if normalized_source_values is None or "template" in normalized_source_values:
        template_metadata_text_predicate = Q(
            metadata_templates__metadata_template_fields__pk__in=Subquery(
                version_template_metadata_matches.values("pk"),
            ),
        )
    else:
        template_metadata_text_predicate = FALSE_Q
    return (
        Q(name__icontains=term)
        | Q(dataset__name__icontains=term)
        | Q(dataset__display_name__icontains=term)
        | Q(dataset__folder__name__icontains=term)
        | Q(dataset__folder__project__name__icontains=term)
        | metadata_text_predicate
        | template_metadata_text_predicate
        | Q(version_file__uploaded_file__icontains=term)
        | Q(version_file_id__in=Subquery(file_metadata_matches.values("assigned_to_object_id")))
    )


def build_file_search_text_query(term: str) -> Q:
    matching_versions = UploadsVersion.objects.filter(
        Q(name__icontains=term)
        | Q(dataset__name__icontains=term)
        | Q(dataset__display_name__icontains=term)
        | Q(dataset__folder__name__icontains=term)
        | Q(dataset__folder__project__name__icontains=term),
    )
    return (
        Q(uploaded_file__icontains=term)
        | build_metadata_text_predicate_for_model(UploadsVersionFile, term)
        | Q(pk__in=Subquery(matching_versions.values("version_file_id")))
    )


def build_template_search_text_query(term: str) -> Q:
    latest_template_pk_queryset = annotate_template_container_search_fields(
        MetadataTemplateContainer.objects.all(),
    ).values("search_latest_template_pk")
    matching_template_fields = annotate_metadata_template_field_text(
        MetadataTemplateField.objects.filter(
            metadata_template_id__in=Subquery(latest_template_pk_queryset),
        ),
    ).filter(
        build_string_query("search_metadata_key", "contains", term)
        | build_string_query("search_metadata_value", "contains", term),
    )
    return (
        Q(search_template_name__icontains=term)
        | Q(search_latest_template_name__icontains=term)
        | Q(pk__in=Subquery(matching_template_fields.values("metadata_template__metadata_template_container_id")))
    )


def build_template_version_search_text_query(term: str) -> Q:
    matching_template_fields = annotate_metadata_template_field_text(
        MetadataTemplateField.objects.all(),
    ).filter(
        build_string_query("search_metadata_key", "contains", term)
        | build_string_query("search_metadata_value", "contains", term),
    )
    return (
        Q(search_template_version_name__icontains=term)
        | Q(metadata_template_container__name__icontains=term)
        | Q(pk__in=Subquery(matching_template_fields.values("metadata_template_id")))
    )


def build_base_queryset_project():
    current_user = get_current_user()
    return Project.objects.filter(project_members__member=current_user if current_user.pk else None).distinct()


def build_base_queryset_folder():
    current_user = get_current_user()
    return Folder.objects.filter(folderpermission__project_membership__member=current_user if current_user.pk else None)


def build_base_queryset_subfolder():
    current_user = get_current_user()
    # Without the default ordering by name, which DISTINCT would add as an extra column to the combined search query
    return Subfolder.objects.filter(
        folder__folderpermission__project_membership__member=current_user if current_user.pk else None,
    ).order_by()


def build_viewable_datasets_queryset():
    return annotate_dataset_search_fields(UploadsDataset.objects.all_viewable().distinct())


def build_viewable_dataset_versions_queryset():
    return UploadsVersion.objects.filter(
        dataset__in=UploadsDataset.objects.all_viewable().values("pk"),
    ).distinct()


def build_viewable_files_queryset():
    return UploadsVersionFile.objects.filter(
        uploads_versions__dataset__in=UploadsDataset.objects.all_viewable().values("pk"),
    ).distinct()


def build_viewable_templates_queryset():
    current_user = get_current_user()
    if not current_user.is_authenticated:
        return MetadataTemplateContainer.objects.none()

    project_content_type = ContentType.objects.get_for_model(Project)
    folder_content_type = ContentType.objects.get_for_model(Folder)
    dataset_content_type = ContentType.objects.get_for_model(UploadsDataset)

    query = Q(
        assigned_to_content_type__isnull=True,
        assigned_to_object_id__isnull=True,
    )
    query |= Q(
        assigned_to_content_type=project_content_type,
        assigned_to_object_id__in=Subquery(
            ProjectMembership.objects.filter(member=current_user).values_list("project__pk", flat=True),
        ),
    )
    query |= Q(
        assigned_to_content_type=folder_content_type,
        assigned_to_object_id__in=Subquery(
            FolderPermission.objects.filter(project_membership__member=current_user).values_list(
                "folder__pk",
                flat=True,
            ),
        ),
    )
    query |= Q(
        assigned_to_content_type=dataset_content_type,
        assigned_to_object_id__in=Subquery(
            UploadsDataset.objects.filter(
                Q(publication_date__isnull=True, created_by=current_user)
                | Q(folder__folderpermission__project_membership__member=current_user),
            ).values_list("pk", flat=True),
        ),
    )
    return annotate_template_container_search_fields(
        MetadataTemplateContainer.objects.filter(query).distinct(),
    )


def build_viewable_template_versions_queryset():
    return annotate_template_version_search_fields(
        MetadataTemplate.objects.filter(
            metadata_template_container__pk__in=Subquery(build_viewable_templates_queryset().values("pk")),
        ).distinct(),
    )


def build_dataset_hydration_queryset(pks):
    return annotate_dataset_search_fields(
        UploadsDataset.objects.filter(pk__in=pks),
    ).select_related(
        "folder",
        "folder__project",
        "folder__storage",
    )


def build_template_hydration_queryset(pks):
    return annotate_template_container_search_fields(
        MetadataTemplateContainer.objects.filter(pk__in=pks),
    ).select_related(
        "assigned_to_content_type",
    )


def build_template_version_hydration_queryset(pks):
    return (
        annotate_template_version_search_fields(
            MetadataTemplate.objects.filter(pk__in=pks),
        )
        .select_related(
            "metadata_template_container",
            "metadata_template_container__assigned_to_content_type",
        )
        .prefetch_related(
            "metadata_template_fields",
        )
    )


RESULT_TYPE_SPECS = {
    "project": ResultTypeSpec(
        result_type="project",
        model=Project,
        serializer_class=ProjectSearchSerializer,
        base_queryset_factory=build_base_queryset_project,
        search_text_query_factory=build_project_search_text_query,
        primary_search_query_factory=lambda term: Q(name__icontains=term),
        hydration_queryset_factory=lambda pks: Project.objects.filter(pk__in=pks),
    ),
    "folder": ResultTypeSpec(
        result_type="folder",
        model=Folder,
        serializer_class=FolderSearchSerializer,
        base_queryset_factory=build_base_queryset_folder,
        search_text_query_factory=build_folder_search_text_query,
        primary_search_query_factory=lambda term: Q(name__icontains=term),
        hydration_queryset_factory=lambda pks: Folder.objects.filter(pk__in=pks).select_related("project", "storage"),
    ),
    "subfolder": ResultTypeSpec(
        result_type="subfolder",
        model=Subfolder,
        serializer_class=SubfolderSearchSerializer,
        base_queryset_factory=build_base_queryset_subfolder,
        search_text_query_factory=build_subfolder_search_text_query,
        primary_search_query_factory=lambda term: Q(name__icontains=term),
        hydration_queryset_factory=lambda pks: Subfolder.objects.filter(pk__in=pks).select_related(
            "folder",
            "folder__project",
            "folder__storage",
            "parent",
        ),
    ),
    "dataset": ResultTypeSpec(
        result_type="dataset",
        model=UploadsDataset,
        serializer_class=UploadsDatasetSearchSerializer,
        base_queryset_factory=build_viewable_datasets_queryset,
        search_text_query_factory=build_dataset_search_text_query,
        primary_search_query_factory=lambda term: Q(name__icontains=term) | Q(display_name__icontains=term),
        hydration_queryset_factory=build_dataset_hydration_queryset,
    ),
    "dataset_version": ResultTypeSpec(
        result_type="dataset_version",
        model=UploadsVersion,
        serializer_class=UploadsVersionSearchSerializer,
        base_queryset_factory=build_viewable_dataset_versions_queryset,
        search_text_query_factory=build_dataset_version_search_text_query,
        primary_search_query_factory=lambda term: Q(name__icontains=term),
        hydration_queryset_factory=lambda pks: UploadsVersion.objects.filter(pk__in=pks).select_related(
            "dataset",
            "dataset__folder",
            "dataset__folder__project",
            "dataset__folder__storage",
            "version_file",
        ),
    ),
    "file": ResultTypeSpec(
        result_type="file",
        model=UploadsVersionFile,
        serializer_class=UploadsVersionFileSearchSerializer,
        base_queryset_factory=build_viewable_files_queryset,
        search_text_query_factory=build_file_search_text_query,
        primary_search_query_factory=lambda term: Q(uploaded_file__icontains=term),
        hydration_queryset_factory=lambda pks: UploadsVersionFile.objects.filter(pk__in=pks).prefetch_related(
            "uploads_versions",
            "uploads_versions__dataset",
        ),
    ),
    "template": ResultTypeSpec(
        result_type="template",
        model=MetadataTemplateContainer,
        serializer_class=MetadataTemplateSearchSerializer,
        base_queryset_factory=build_viewable_templates_queryset,
        search_text_query_factory=build_template_search_text_query,
        primary_search_query_factory=lambda term: Q(search_template_name__icontains=term),
        hydration_queryset_factory=build_template_hydration_queryset,
    ),
    "template_version": ResultTypeSpec(
        result_type="template_version",
        model=MetadataTemplate,
        serializer_class=MetadataTemplateVersionSearchSerializer,
        base_queryset_factory=build_viewable_template_versions_queryset,
        search_text_query_factory=build_template_version_search_text_query,
        primary_search_query_factory=lambda term: Q(search_template_version_name__icontains=term),
        hydration_queryset_factory=build_template_version_hydration_queryset,
    ),
}


_VALUELESS_OPERATORS = {"is_empty", "is_not_empty"}


def _value_is_missing(rule: dict) -> bool:
    if rule["operator"] in _VALUELESS_OPERATORS:
        return False
    if "value" not in rule:
        return True
    value = rule["value"]
    if value is None:
        return True
    if isinstance(value, str) and value == "":
        return True
    if isinstance(value, list) and len(value) == 0:
        return True
    return False


class SearchFieldCatalog:
    SUPPORTED_METADATA_VALUE_FIELD_TYPES = set(METADATA_OPERATORS_BY_FIELD_TYPE)

    PROJECT_PERMISSION_VALUES = {
        "project_admin",
        "member",
        "member_create_folders",
        "member_metadata_template_admin",
        "member_extended",
    }

    FOLDER_PERMISSION_VALUES = {
        "admin",
        "editor",
        "viewer",
    }

    FIELD_DESCRIPTIONS = {
        "name": "Primary entity name.",
        "description": "Entity description text.",
        "project_name": "Related project name.",
        "folder_name": "Related folder name.",
        "dataset_name": "Related dataset name.",
        "dataset_version_name": "Related dataset version name. For dataset results, this means the latest version only.",
        "file_name": "Related file name or upload path basename. For dataset results, this means the latest version file only.",
        "template_name": "Metadata template container name.",
        "template_version_name": "Metadata template version display name. For template results, this means the latest version's explicit name only.",
        "project_id": "Exact project id scope filter.",
        "folder_id": "Exact folder id scope filter.",
        "dataset_id": "Exact dataset id scope filter.",
        "dataset_version_id": "Exact dataset version id scope filter.",
        "template_id": "Exact metadata template container id scope filter.",
        "template_version_id": "Exact metadata template version id scope filter.",
        "metadata.key": "Metadata key.",
        "metadata.value": "Metadata value.",
        "metadata.template_name": "Metadata template container name filter. Narrows metadata search to entries from the specified template.",
        "metadata.template_id": "Metadata template container id filter. Narrows metadata search to entries from the specified template.",
        "metadata.field_type": "Metadata field type. For template results, matches the latest template version only; for template_version results, matches that version directly.",
        "metadata.source": "Metadata provenance for dataset and dataset_version results. On datasets, this applies to metadata from the latest version view only. `instance` means user-authored metadata; `template` means metadata originating from a template field, including materialized copies linked via metadata_template_field; `system` means system-generated read-only metadata (e.g., FILE_SIZE, MIME_TYPE).",
        "template_type": "Metadata template assignment scope for logical templates and template versions.",
        "my_project_permission": "Current user's effective project-level permission.",
        "my_folder_permission": "Current user's effective folder-level permission.",
        "is_published": "Whether the entity has been published.",
        "publication_date": "Publication timestamp.",
        "expiry_date": "Expiry timestamp.",
        "metadata_is_complete": "Whether the required metadata is complete. For `dataset` results this is evaluated against the latest version only; datasets with no versions are excluded from `is true` and `is false`, but included in `is_not true` and `is_not false` (three-valued logic: NULL latest-version flag counts as 'neither true nor false'). For `dataset_version` results it is the version's own flag.",
        "has_upgradeable_metadata_templates": "Whether the dataset's latest version is pinned to an outdated metadata template version that has a newer version available. Cached on `UploadsDataset.has_upgradeable_metadata_templates`.",
        "has_file": "Whether the entity has an associated file. For dataset results, this is based on the latest version only.",
        "created_at": "Creation timestamp.",
        "updated_at": "Last modification timestamp.",
    }

    FIELD_DEFINITIONS = {
        "name": {
            "operators": STRING_OPERATORS,
            "result_types": set(ENTITY_STRING_FIELD_PATHS["name"].keys()),
        },
        "description": {
            "operators": STRING_OPERATORS,
            "result_types": {
                "project",
                "folder",
            },
        },
        "project_name": {
            "operators": STRING_OPERATORS,
            "result_types": set(ENTITY_STRING_FIELD_PATHS["project_name"].keys()) | {"template", "template_version"},
        },
        "folder_name": {
            "operators": STRING_OPERATORS,
            "result_types": set(ENTITY_STRING_FIELD_PATHS["folder_name"].keys()) | {"template", "template_version"},
        },
        "dataset_name": {
            "operators": STRING_OPERATORS,
            "result_types": set(ENTITY_STRING_FIELD_PATHS["dataset_name"].keys()) | {"template", "template_version"},
        },
        "dataset_version_name": {
            "operators": STRING_OPERATORS,
            "result_types": set(ENTITY_STRING_FIELD_PATHS["dataset_version_name"].keys()),
        },
        "file_name": {
            "operators": STRING_OPERATORS,
            "result_types": set(ENTITY_STRING_FIELD_PATHS["file_name"].keys()),
        },
        "template_name": {
            "operators": STRING_OPERATORS,
            "result_types": set(ENTITY_STRING_FIELD_PATHS["template_name"].keys()),
        },
        "template_version_name": {
            "operators": STRING_OPERATORS,
            "result_types": set(ENTITY_STRING_FIELD_PATHS["template_version_name"].keys()),
        },
        "project_id": {
            "operators": IDENTIFIER_OPERATORS,
            "result_types": set(ENTITY_IDENTIFIER_FIELD_PATHS["project_id"].keys()) | {"template", "template_version"},
        },
        "folder_id": {
            "operators": IDENTIFIER_OPERATORS,
            "result_types": set(ENTITY_IDENTIFIER_FIELD_PATHS["folder_id"].keys()) | {"template", "template_version"},
        },
        "dataset_id": {
            "operators": IDENTIFIER_OPERATORS,
            "result_types": set(ENTITY_IDENTIFIER_FIELD_PATHS["dataset_id"].keys()) | {"template", "template_version"},
        },
        "dataset_version_id": {
            "operators": IDENTIFIER_OPERATORS,
            "result_types": set(ENTITY_IDENTIFIER_FIELD_PATHS["dataset_version_id"].keys()),
        },
        "template_id": {
            "operators": IDENTIFIER_OPERATORS,
            "result_types": set(ENTITY_IDENTIFIER_FIELD_PATHS["template_id"].keys()),
        },
        "template_version_id": {
            "operators": IDENTIFIER_OPERATORS,
            "result_types": set(ENTITY_IDENTIFIER_FIELD_PATHS["template_version_id"].keys()),
        },
        "metadata.key": {
            "operators": STRING_OPERATORS,
            "metadata_only": True,
        },
        "metadata.value": {
            "operators": TYPE_AWARE_METADATA_OPERATORS,
            "metadata_only": True,
        },
        "metadata.field_type": {
            "operators": ENUM_OPERATORS,
            "metadata_only": True,
            "result_types": set(SEARCH_RESULT_TYPES),
            "enum_values": sorted(METADATA_FIELD_TYPE_VALUES),
        },
        "metadata.source": {
            "operators": ENUM_OPERATORS,
            "metadata_only": True,
            "result_types": {
                "dataset",
                "dataset_version",
            },
            "enum_values": sorted(METADATA_SOURCE_VALUES),
        },
        "metadata.template_name": {
            "operators": STRING_OPERATORS,
            "metadata_only": True,
            "result_types": {
                "dataset",
                "dataset_version",
                "template",
                "template_version",
            },
        },
        "metadata.template_id": {
            "operators": IDENTIFIER_OPERATORS,
            "metadata_only": True,
            "result_types": {
                "dataset",
                "dataset_version",
                "template",
                "template_version",
            },
        },
        "template_type": {
            "operators": ENUM_OPERATORS,
            "result_types": {
                "template",
                "template_version",
            },
            "enum_values": sorted(TEMPLATE_TYPE_VALUES),
        },
        "my_project_permission": {
            "operators": PERMISSION_OPERATORS,
            "result_types": {
                "project",
                "folder",
                "dataset",
                "dataset_version",
                "file",
            },
        },
        "my_folder_permission": {
            "operators": PERMISSION_OPERATORS,
            "result_types": {
                "folder",
                "dataset",
                "dataset_version",
                "file",
            },
        },
        "is_published": {
            "operators": BOOLEAN_OPERATORS,
            "result_types": {
                "dataset",
                "dataset_version",
                "file",
            },
        },
        "publication_date": {
            "operators": DATETIME_OPERATORS,
            "result_types": {
                "dataset",
                "dataset_version",
                "file",
            },
        },
        "expiry_date": {
            "operators": DATETIME_OPERATORS,
            "result_types": {
                "dataset",
            },
        },
        "metadata_is_complete": {
            "operators": BOOLEAN_OPERATORS,
            "result_types": {
                "dataset",
                "dataset_version",
            },
        },
        "has_upgradeable_metadata_templates": {
            "operators": BOOLEAN_OPERATORS,
            "result_types": {
                "dataset",
            },
        },
        "has_file": {
            "operators": BOOLEAN_OPERATORS,
            "result_types": {
                "dataset",
                "dataset_version",
            },
        },
        "created_at": {
            "operators": DATETIME_OPERATORS,
            "result_types": set(SEARCH_RESULT_TYPES),
        },
        "updated_at": {
            "operators": DATETIME_OPERATORS,
            "result_types": set(SEARCH_RESULT_TYPES),
        },
    }

    @classmethod
    def get_field_definition(cls, field_name: str) -> dict:
        try:
            return cls.FIELD_DEFINITIONS[field_name]
        except KeyError as e:
            raise serializers.ValidationError(f"Unsupported field: {field_name}") from e

    SORT_FIELDS = ["relevance", "updated_at", "created_at"]

    DEFAULT_TYPE_SORT = [{"field": "updated_at", "direction": "desc"}]

    DEFAULT_SORT_CONDITIONS = [
        {
            "when": "positive_text_input_present",
            "sort": [{"field": "relevance", "direction": "desc"}],
        },
        {
            "when": "otherwise",
            "sort": [{"field": "updated_at", "direction": "desc"}],
        },
    ]

    @classmethod
    def field_ids(cls) -> list[str]:
        return list(cls.FIELD_DEFINITIONS.keys())

    @classmethod
    def operator_ids(cls) -> list[str]:
        return sorted(
            {operator for definition in cls.FIELD_DEFINITIONS.values() for operator in definition["operators"]},
        )

    @classmethod
    def metadata_field_type_ids(cls) -> list[str]:
        return sorted(cls.SUPPORTED_METADATA_VALUE_FIELD_TYPES)

    @classmethod
    def sort_fields(cls) -> list[str]:
        return list(cls.SORT_FIELDS)

    @classmethod
    def _build_field_entry(cls, field_id: str, definition: dict) -> dict:
        return {
            "id": field_id,
            "description": cls.FIELD_DESCRIPTIONS.get(field_id, ""),
            "operators": sorted(definition["operators"]),
            "metadata_only": definition.get("metadata_only", False),
            "enum_values": definition.get("enum_values", []),
            "operators_by_field_type": (
                {field_type: sorted(operators) for field_type, operators in METADATA_OPERATORS_BY_FIELD_TYPE.items()}
                if field_id == "metadata.value"
                else {}
            ),
            "value_type": cls.get_field_value_type(field_id),
        }

    @classmethod
    def get_schema(cls) -> dict:
        types = {}
        for result_type in SEARCH_RESULT_TYPES:
            fields = []
            for field_id, definition in cls.FIELD_DEFINITIONS.items():
                field_result_types = definition.get("result_types", set(SEARCH_RESULT_TYPES))
                if result_type in field_result_types:
                    fields.append(cls._build_field_entry(field_id, definition))

            types[result_type] = {
                "fields": fields,
                "sort_fields": cls.sort_fields(),
                "default_sort": list(cls.DEFAULT_TYPE_SORT),
                "default_sort_conditions": list(cls.DEFAULT_SORT_CONDITIONS),
            }

        return {
            "result_types": list(SEARCH_RESULT_TYPES),
            "default_result_types": list(DEFAULT_RESULT_TYPES),
            "sort_directions": [
                "asc",
                "desc",
            ],
            "response_modes": list(SEARCH_RESPONSE_MODES),
            "types": types,
        }

    @classmethod
    def get_field_value_type(cls, field_id: str) -> str:
        if field_id in ENTITY_STRING_FIELD_PATHS or field_id == "description" or field_id == "metadata.key":
            return "string"
        if field_id in ENTITY_IDENTIFIER_FIELD_PATHS or field_id == "metadata.template_id":
            return "uuid"
        if field_id in {
            "my_project_permission",
            "my_folder_permission",
            "metadata.source",
            "metadata.field_type",
            "template_type",
        }:
            return "enum"
        if field_id in {
            "is_published",
            "metadata_is_complete",
            "has_upgradeable_metadata_templates",
            "has_file",
        }:
            return "boolean"
        if field_id in ENTITY_DATETIME_FIELD_PATHS or field_id in {"publication_date", "expiry_date"}:
            return "datetime"
        if field_id == "metadata.value":
            return "mixed"
        return "string"


class SearchFieldRegistry(SearchFieldCatalog):
    TEMPLATE_QUALIFIER_FIELDS = {"metadata.template_name", "metadata.template_id"}

    METADATA_CONDITION_FIELDS = {"metadata.key", "metadata.value"}

    @classmethod
    def contains_template_qualifier(cls, node: dict) -> bool:
        if node["kind"] == "rule":
            return node["field"] in cls.TEMPLATE_QUALIFIER_FIELDS
        return any(cls.contains_template_qualifier(child) for child in node["conditions"])

    @classmethod
    def contains_metadata_key_or_value(cls, node: dict) -> bool:
        if node["kind"] == "rule":
            return node["field"] in cls.METADATA_CONDITION_FIELDS
        return any(cls.contains_metadata_key_or_value(child) for child in node["conditions"])

    @classmethod
    def validate_rule(cls, rule: dict) -> dict:
        field_definition = cls.get_field_definition(rule["field"])
        if rule["operator"] not in field_definition["operators"]:
            raise serializers.ValidationError(
                {rule["field"]: f"Operator '{rule['operator']}' is not supported for this field."},
            )

        if _value_is_missing(rule):
            raise serializers.ValidationError(
                {rule["field"]: "This operator requires a value."},
            )

        if rule["field"] == "my_project_permission":
            cls.validate_permission_value(rule["value"], cls.PROJECT_PERMISSION_VALUES)
        elif rule["field"] == "my_folder_permission":
            cls.validate_permission_value(rule["value"], cls.FOLDER_PERMISSION_VALUES)
        elif rule["field"] == "metadata.field_type":
            cls.validate_enum_value(rule["value"], METADATA_FIELD_TYPE_VALUES)
        elif rule["field"] == "metadata.source":
            cls.validate_enum_value(rule["value"], METADATA_SOURCE_VALUES)
        elif rule["field"] == "template_type":
            cls.validate_enum_value(rule["value"], TEMPLATE_TYPE_VALUES)
        elif "field_type" in rule:
            cls.validate_metadata_value_field_type(rule)

        return field_definition

    @staticmethod
    def validate_permission_value(value, allowed_values: set[str]) -> None:
        values = value if isinstance(value, list) else [value]
        if not values or not all(item in allowed_values for item in values):
            raise serializers.ValidationError("Unsupported permission value.")

    @staticmethod
    def validate_enum_value(value, allowed_values: set[str]) -> None:
        values = value if isinstance(value, list) else [value]
        if not values or not all(isinstance(item, str) and item in allowed_values for item in values):
            raise serializers.ValidationError("Unsupported enum value.")

    @classmethod
    def validate_metadata_value_field_type(cls, rule: dict) -> None:
        field_type = rule.get("field_type")
        if rule["field"] != "metadata.value":
            raise serializers.ValidationError("field_type is only supported for type-aware metadata.value comparisons.")
        if field_type not in METADATA_OPERATORS_BY_FIELD_TYPE:
            raise serializers.ValidationError(
                "field_type must be a supported metadata field type.",
            )
        if rule["operator"] not in METADATA_OPERATORS_BY_FIELD_TYPE[field_type]:
            raise serializers.ValidationError(
                f"Operator '{rule['operator']}' is not supported for metadata field type {field_type}.",
            )

    @classmethod
    def build_entity_predicate(cls, result_type: str, base_queryset: QuerySet, rule: dict) -> Q:
        field_definition = cls.validate_rule(rule)
        if result_type not in field_definition.get("result_types", set()):
            return FALSE_Q

        field_name = rule["field"]
        operator = rule["operator"]
        value = rule.get("value")

        if field_name in ENTITY_STRING_FIELD_PATHS:
            if result_type in {"template", "template_version"} and field_name in {
                "project_name",
                "folder_name",
                "dataset_name",
            }:
                return build_template_scope_name_predicate(result_type, field_name, operator, value)
            return build_string_query(
                ENTITY_STRING_FIELD_PATHS[field_name][result_type],
                operator,
                value,
            )

        if field_name in ENTITY_IDENTIFIER_FIELD_PATHS:
            if result_type in {"template", "template_version"} and field_name in {
                "project_id",
                "folder_id",
                "dataset_id",
            }:
                return build_template_scope_predicate(result_type, field_name, operator, value)
            return build_identifier_query(
                ENTITY_IDENTIFIER_FIELD_PATHS[field_name][result_type],
                operator,
                value,
            )

        if field_name == "description":
            return build_description_predicate(base_queryset, operator, value)

        if field_name == "template_type":
            if result_type == "template_version":
                return build_template_type_predicate(operator, value, prefix="metadata_template_container__")
            return build_template_type_predicate(operator, value)

        if field_name == "my_project_permission":
            return build_project_permission_predicate(result_type, operator, value)

        if field_name == "my_folder_permission":
            return build_folder_permission_predicate(result_type, operator, value)

        if field_name == "is_published":
            return build_is_published_predicate(result_type, operator, value)

        if field_name == "publication_date":
            return build_datetime_query("publication_date", operator, value)

        if field_name == "expiry_date":
            return build_datetime_query("expiry_date", operator, value)

        if field_name == "metadata_is_complete":
            return build_metadata_is_complete_predicate(result_type, operator, value)

        if field_name == "has_upgradeable_metadata_templates":
            return build_has_upgradeable_metadata_templates_predicate(result_type, operator, value)

        if field_name == "has_file":
            return build_has_file_predicate(result_type, operator, value)

        if field_name in ENTITY_DATETIME_FIELD_PATHS:
            return build_datetime_query(
                ENTITY_DATETIME_FIELD_PATHS[field_name],
                operator,
                value,
            )

        raise serializers.ValidationError(f"Unsupported entity field: {field_name}")

    @classmethod
    def build_metadata_queryset(
        cls,
        metadata_queryset: QuerySet,
        metadata_value_context: dict | None = None,
    ) -> QuerySet:
        if metadata_queryset.model is MetadataTemplateField:
            metadata_queryset = annotate_metadata_template_field_text(metadata_queryset)
        else:
            metadata_queryset = annotate_metadata_text(metadata_queryset)
        if metadata_value_context is None:
            return metadata_queryset

        annotation_name = "search_metadata_typed_value"
        value_field = KeyTextTransform("value", "value")
        exact_key = metadata_value_context["exact_key"]
        metadata_value_type = metadata_value_context["field_type"]

        if exact_key is not None:
            metadata_queryset = metadata_queryset.filter(
                Q(custom_key__iexact=exact_key) | Q(field__key__iexact=exact_key),
                field_type=metadata_value_type,
            )
        else:
            metadata_queryset = metadata_queryset.filter(
                field_type=metadata_value_type,
            )

        if metadata_value_type == MetadataFieldType.INTEGER:
            return metadata_queryset.annotate(
                **{annotation_name: Cast(value_field, output_field=IntegerField())},
            )
        if metadata_value_type == MetadataFieldType.DECIMAL:
            return metadata_queryset.annotate(
                **{
                    annotation_name: Cast(
                        value_field,
                        output_field=DecimalField(max_digits=20, decimal_places=10),
                    ),
                },
            )
        if metadata_value_type == MetadataFieldType.DATE:
            return metadata_queryset.annotate(
                **{annotation_name: Cast(value_field, output_field=DateField())},
            )
        if metadata_value_type == MetadataFieldType.DATETIME:
            return metadata_queryset.annotate(
                **{annotation_name: Cast(value_field, output_field=DateTimeField())},
            )

        if metadata_value_type in {
            MetadataFieldType.TIME,
            MetadataFieldType.TEXT,
            MetadataFieldType.WYSIWYG,
            MetadataFieldType.SELECTION,
            MetadataFieldType.MULTI_SELECTION,
            MetadataFieldType.TAG,
        }:
            if metadata_value_type in {MetadataFieldType.MULTI_SELECTION, MetadataFieldType.TAG}:
                return metadata_queryset.annotate(
                    search_metadata_choice_distinct_count=RawSQL(
                        """
                        CASE
                            WHEN jsonb_typeof(value -> 'value') = 'array'
                            THEN (
                                SELECT COUNT(DISTINCT choice_value)
                                FROM jsonb_array_elements_text(value -> 'value') AS choice_value
                            )
                            ELSE NULL
                        END
                        """,
                        [],
                        output_field=IntegerField(),
                    ),
                )
            return metadata_queryset

        raise serializers.ValidationError(
            "Unsupported metadata field type for type-aware comparison.",
        )

    @classmethod
    def build_metadata_predicate(
        cls,
        rule: dict,
        metadata_value_context: dict | None = None,
        result_type: str | None = None,
    ) -> Q:
        field_definition = cls.validate_rule(rule)
        if result_type is not None and result_type not in field_definition.get(
            "result_types",
            set(SEARCH_RESULT_TYPES),
        ):
            return FALSE_Q

        if rule["field"] == "metadata.key":
            return build_string_query(
                "search_metadata_key",
                rule["operator"],
                rule.get("value"),
            )

        if rule["field"] == "metadata.field_type":
            return build_metadata_field_type_query(rule["operator"], rule.get("value"))

        if rule["field"] == "metadata.source":
            return build_metadata_source_query(rule["operator"], rule.get("value"))

        if rule["field"] == "metadata.template_name":
            return build_string_query(
                "metadata_template__metadata_template_container__name",
                rule["operator"],
                rule.get("value"),
            )

        if rule["field"] == "metadata.template_id":
            return build_identifier_query(
                "metadata_template__metadata_template_container__pk",
                rule["operator"],
                rule.get("value"),
            )

        if metadata_value_context is not None:
            metadata_value_type = metadata_value_context["field_type"]
            if rule["operator"] not in METADATA_OPERATORS_BY_FIELD_TYPE[metadata_value_type]:
                raise serializers.ValidationError(
                    f"Operator '{rule['operator']}' is not supported for metadata field type {metadata_value_type}.",
                )
            if metadata_value_type in {
                MetadataFieldType.SELECTION,
                MetadataFieldType.MULTI_SELECTION,
                MetadataFieldType.TAG,
            }:
                return build_choice_metadata_value_query(
                    field_type=metadata_value_type,
                    operator=rule["operator"],
                    value=rule.get("value"),
                )
            if metadata_value_type in {
                MetadataFieldType.INTEGER,
                MetadataFieldType.DECIMAL,
                MetadataFieldType.DATE,
                MetadataFieldType.DATETIME,
            }:
                return cls.build_typed_metadata_value_predicate(
                    operator=rule["operator"],
                    value=rule.get("value"),
                    metadata_value_context=metadata_value_context,
                )

        return build_string_query(
            "search_metadata_value",
            rule["operator"],
            rule.get("value"),
        )

    @classmethod
    def validate_payload_result_type_compatibility(cls, node: dict | None, result_types: list[str]) -> None:
        if node is None:
            return

        if node["kind"] == "rule":
            supported_result_types = cls.get_field_definition(node["field"]).get("result_types")
            if supported_result_types is not None and not (supported_result_types & set(result_types)):
                raise serializers.ValidationError(
                    {node["field"]: "This field is not supported for the selected result_types."},
                )
            return

        for child in node["conditions"]:
            cls.validate_payload_result_type_compatibility(child, result_types)

    @staticmethod
    def build_typed_metadata_value_predicate(operator: str, value, metadata_value_context: dict | None) -> Q:
        if metadata_value_context is None:
            raise serializers.ValidationError("Typed metadata comparison requires an exact metadata key in the group.")

        metadata_value_type = metadata_value_context["field_type"]
        annotation_name = "search_metadata_typed_value"

        if metadata_value_type == MetadataFieldType.INTEGER:
            parsed_value = SearchASTValidator.parse_int_value(value, operator)
        elif metadata_value_type == MetadataFieldType.DECIMAL:
            parsed_value = SearchASTValidator.parse_decimal_value(value, operator)
        elif metadata_value_type == MetadataFieldType.DATE:
            parsed_value = SearchASTValidator.parse_date_value(value, operator)
        elif metadata_value_type == MetadataFieldType.DATETIME:
            parsed_value = SearchASTValidator.parse_datetime_value(value, operator)
        else:
            raise serializers.ValidationError(
                "Typed metadata comparison is only supported for integer, decimal, date, and datetime values.",
            )

        if operator == "is":
            return Q(**{annotation_name: parsed_value})
        if operator == "is_not":
            return ~Q(**{annotation_name: parsed_value})
        if operator == "lt":
            return Q(**{f"{annotation_name}__lt": parsed_value})
        if operator == "lte":
            return Q(**{f"{annotation_name}__lte": parsed_value})
        if operator == "gt":
            return Q(**{f"{annotation_name}__gt": parsed_value})
        if operator == "gte":
            return Q(**{f"{annotation_name}__gte": parsed_value})
        if operator == "between":
            return Q(**{f"{annotation_name}__gte": parsed_value[0], f"{annotation_name}__lte": parsed_value[1]})
        if operator == "is_empty":
            return Q(**{f"{annotation_name}__isnull": True})
        if operator == "is_not_empty":
            return Q(**{f"{annotation_name}__isnull": False})

        raise serializers.ValidationError("Unsupported typed metadata comparison operator.")


class SearchASTValidator:
    @classmethod
    def normalize_payload(cls, payload: dict) -> dict:
        normalized_payload = dict(payload)
        normalized_payload["result_types"] = list(
            dict.fromkeys(normalized_payload.get("result_types") or list(DEFAULT_RESULT_TYPES)),
        )
        normalized_payload.setdefault("sort", None)
        normalized_payload["response_mode"] = normalized_payload.get("response_mode") or DEFAULT_RESPONSE_MODE
        normalized_payload["cursor_direction"] = normalized_payload.get("cursor_direction") or DEFAULT_CURSOR_DIRECTION

        if normalized_payload.get("group") is not None:
            normalized_payload["group"] = cls.normalize_node(normalized_payload["group"], is_root=True)

        return normalized_payload

    @classmethod
    def normalize_node(cls, node: dict, is_root=False) -> dict:
        if not isinstance(node, dict):
            raise serializers.ValidationError("Each condition node must be an object.")

        kind = node.get("kind")
        if kind is None:
            kind = "group" if "conditions" in node else "rule"

        if kind == "group":
            conditions = node.get("conditions")
            if not isinstance(conditions, list) or not conditions:
                raise serializers.ValidationError("Condition groups must contain at least one condition.")

            operator = str(node.get("operator", "")).upper()
            if not operator:
                if len(conditions) == 1:
                    operator = "AND"
                else:
                    raise serializers.ValidationError(
                        "Group operator is required when a condition group contains more than one condition.",
                    )
            elif operator not in {"AND", "OR"}:
                raise serializers.ValidationError("Group operator must be AND or OR.")

            return {
                "kind": "group",
                "operator": operator,
                "conditions": [cls.normalize_node(condition) for condition in conditions],
            }

        if kind == "rule":
            field = node.get("field")
            operator = str(node.get("operator", "")).lower()
            if not isinstance(field, str) or not field:
                raise serializers.ValidationError("Each rule requires a field.")
            if not operator:
                raise serializers.ValidationError("Each rule requires an operator.")

            normalized_rule = {
                "kind": "rule",
                "field": field,
                "operator": operator,
            }
            if "value" in node:
                normalized_rule["value"] = node.get("value")
            if "field_type" in node:
                normalized_rule["field_type"] = node.get("field_type")

            SearchFieldRegistry.validate_rule(normalized_rule)
            return normalized_rule

        raise serializers.ValidationError("Unsupported condition node kind.")

    @staticmethod
    def parse_int_value(value, operator: str):
        if operator == "between":
            if not isinstance(value, list) or len(value) != 2:
                raise serializers.ValidationError("Between requires a list with two integer values.")
            try:
                return [int(item) for item in value]
            except (TypeError, ValueError) as e:
                raise serializers.ValidationError("Between requires a list with two integer values.") from e

        try:
            return int(value)
        except (TypeError, ValueError) as e:
            raise serializers.ValidationError("This operator requires an integer value.") from e

    @staticmethod
    def parse_decimal_value(value, operator: str):
        if operator == "between":
            if not isinstance(value, list) or len(value) != 2:
                raise serializers.ValidationError("Between requires a list with two decimal values.")
            try:
                return [Decimal(str(item)) for item in value]
            except (InvalidOperation, TypeError) as e:
                raise serializers.ValidationError("Between requires a list with two decimal values.") from e

        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError) as e:
            raise serializers.ValidationError("This operator requires a decimal value.") from e

    @staticmethod
    def parse_date_value(value, operator: str):
        if operator == "between":
            if not isinstance(value, list) or len(value) != 2:
                raise serializers.ValidationError("Between requires a list with two ISO date values.")
            parsed_value = [parse_date(item) if isinstance(item, str) else None for item in value]
            if any(item is None for item in parsed_value):
                raise serializers.ValidationError("Between requires a list with two ISO date values.")
            return parsed_value

        parsed_value = parse_date(value) if isinstance(value, str) else None
        if parsed_value is None:
            raise serializers.ValidationError("This operator requires an ISO date value.")
        return parsed_value

    @staticmethod
    def parse_datetime_value(value, operator: str):
        if operator == "between":
            if not isinstance(value, list) or len(value) != 2:
                raise serializers.ValidationError("Between requires a list with two ISO datetime values.")
            parsed_value = [parse_datetime(item) if isinstance(item, str) else None for item in value]
            if any(item is None for item in parsed_value):
                raise serializers.ValidationError("Between requires a list with two ISO datetime values.")
            return parsed_value

        parsed_value = parse_datetime(value) if isinstance(value, str) else None
        if parsed_value is None:
            raise serializers.ValidationError("This operator requires an ISO datetime value.")
        return parsed_value


class SearchQueryCompiler:
    def compile(self, spec: ResultTypeSpec, payload: dict) -> QuerySet:
        base_queryset = spec.base_queryset_factory()
        predicates = []

        search_text = payload.get("search_text")
        group = payload.get("group")
        if search_text:
            if spec.result_type == "dataset_version":
                predicates.append(
                    build_dataset_version_search_text_query(
                        search_text,
                        self.extract_metadata_source_constraint(group),
                    ),
                )
            elif spec.result_type == "dataset":
                predicates.append(
                    build_dataset_search_text_query_with_source(
                        search_text,
                        self.extract_metadata_source_constraint(group),
                    ),
                )
            else:
                predicates.append(spec.search_text_query_factory(search_text))

        if group is not None:
            predicates.append(self.compile_entity_node(spec, base_queryset, group))

        if not predicates:
            return base_queryset.distinct()

        return base_queryset.filter(combine_predicates("AND", predicates)).distinct()

    def compile_entity_node(self, spec: ResultTypeSpec, base_queryset: QuerySet, node: dict) -> Q:
        if node["kind"] == "rule":
            if self.is_metadata_only(node):
                return self.compile_metadata_group_to_predicate(spec, base_queryset, self.ensure_group(node))
            return SearchFieldRegistry.build_entity_predicate(
                result_type=spec.result_type,
                base_queryset=base_queryset,
                rule=node,
            )

        entity_predicates = []
        metadata_rule_cluster = []
        for child in node["conditions"]:
            if self.is_metadata_only(child):
                if child["kind"] == "rule":
                    metadata_rule_cluster.append(child)
                else:
                    entity_predicates.append(
                        self.compile_metadata_group_to_predicate(spec, base_queryset, child),
                    )
                continue
            entity_predicates.append(self.compile_entity_node(spec, base_queryset, child))

        if metadata_rule_cluster:
            entity_predicates.append(
                self.compile_metadata_group_to_predicate(
                    spec=spec,
                    base_queryset=base_queryset,
                    group_node={
                        "kind": "group",
                        "operator": node["operator"],
                        "conditions": metadata_rule_cluster,
                    },
                ),
            )

        return combine_predicates(node["operator"], entity_predicates)

    def compile_metadata_group_to_predicate(self, spec: ResultTypeSpec, base_queryset: QuerySet, group_node: dict) -> Q:
        if (
            group_node["operator"] != "AND"
            and SearchFieldRegistry.contains_template_qualifier(group_node)
            and SearchFieldRegistry.contains_metadata_key_or_value(group_node)
        ):
            raise serializers.ValidationError(
                "Template metadata qualifiers combined with metadata.key or metadata.value conditions require an AND group.",
            )

        if self.contains_metadata_only_group_child(group_node):
            return self.compile_metadata_cluster_group_to_predicate(spec, base_queryset, group_node)

        return self.compile_metadata_row_subquery(spec, base_queryset, group_node)

    def compile_metadata_cluster_group_to_predicate(
        self,
        spec: ResultTypeSpec,
        base_queryset: QuerySet,
        group_node: dict,
    ) -> Q:
        entity_predicates = []
        metadata_rule_cluster = []

        for child in group_node["conditions"]:
            if not self.is_metadata_only(child):
                raise serializers.ValidationError("Metadata groups can only contain metadata conditions.")
            if child["kind"] == "rule":
                metadata_rule_cluster.append(child)
            else:
                entity_predicates.append(
                    self.compile_metadata_group_to_predicate(spec, base_queryset, child),
                )

        if metadata_rule_cluster:
            entity_predicates.append(
                self.compile_metadata_row_subquery(
                    spec=spec,
                    base_queryset=base_queryset,
                    group_node={
                        "kind": "group",
                        "operator": group_node["operator"],
                        "conditions": metadata_rule_cluster,
                    },
                ),
            )

        return combine_predicates(group_node["operator"], entity_predicates)

    @staticmethod
    def contains_metadata_only_group_child(group_node: dict) -> bool:
        return any(child["kind"] == "group" for child in group_node["conditions"])

    def compile_metadata_row_subquery(self, spec: ResultTypeSpec, base_queryset: QuerySet, group_node: dict) -> Q:
        template_metadata_queryset = self.get_template_metadata_queryset(spec, base_queryset)
        if spec.result_type in {"template", "template_version"}:
            metadata_value_context = self.get_metadata_value_context(
                group_node,
                Metadata.objects.none(),
                template_metadata_queryset,
            )
            negative_choice_predicate = self.compile_negative_choice_subquery(
                spec,
                base_queryset,
                group_node,
                metadata_value_context,
            )
            if negative_choice_predicate is not None:
                return negative_choice_predicate
            template_metadata_queryset = SearchFieldRegistry.build_metadata_queryset(
                template_metadata_queryset,
                metadata_value_context,
            )
            metadata_predicate = self.compile_metadata_node(group_node, metadata_value_context, spec.result_type)
            values_field = (
                "metadata_template__metadata_template_container_id"
                if spec.result_type == "template"
                else "metadata_template_id"
            )
            return Q(
                pk__in=Subquery(
                    template_metadata_queryset.filter(metadata_predicate).values(values_field),
                ),
            )

        if spec.result_type == "dataset":
            latest_version_ids = base_queryset.exclude(
                search_latest_version_pk__isnull=True,
            ).values("search_latest_version_pk")
            has_template_qualifier = SearchFieldRegistry.contains_template_qualifier(group_node)

            template_metadata_queryset = MetadataTemplateField.objects.filter(
                metadata_template__uploads_versions__pk__in=Subquery(latest_version_ids),
            )

            if has_template_qualifier:
                metadata_value_context = self.get_metadata_value_context(
                    group_node,
                    Metadata.objects.none(),
                    template_metadata_queryset,
                )
                negative_choice_predicate = self.compile_negative_choice_subquery(
                    spec,
                    base_queryset,
                    group_node,
                    metadata_value_context,
                )
                if negative_choice_predicate is not None:
                    return negative_choice_predicate
                metadata_predicate = self.compile_metadata_node(group_node, metadata_value_context, spec.result_type)
                template_metadata_queryset = SearchFieldRegistry.build_metadata_queryset(
                    template_metadata_queryset,
                    metadata_value_context,
                )
                return Q(
                    pk__in=Subquery(
                        UploadsVersion.objects.filter(
                            pk__in=Subquery(latest_version_ids),
                            metadata_templates__metadata_template_fields__pk__in=Subquery(
                                template_metadata_queryset.filter(metadata_predicate).values("pk"),
                            ),
                        ).values("dataset_id"),
                    ),
                )

            latest_file_ids = base_queryset.exclude(
                search_latest_version_file_pk__isnull=True,
            ).values("search_latest_version_file_pk")
            version_content_type = ContentType.objects.get_for_model(UploadsVersion)
            file_content_type = ContentType.objects.get_for_model(UploadsVersionFile)
            metadata_queryset = Metadata.objects.filter(
                Q(
                    assigned_to_content_type=version_content_type,
                    assigned_to_object_id__in=Subquery(latest_version_ids),
                )
                | Q(
                    assigned_to_content_type=file_content_type,
                    assigned_to_object_id__in=Subquery(latest_file_ids),
                ),
            )
            version_metadata_queryset = Metadata.objects.filter(
                assigned_to_content_type=version_content_type,
                assigned_to_object_id__in=Subquery(latest_version_ids),
            )
            file_metadata_queryset = Metadata.objects.filter(
                assigned_to_content_type=file_content_type,
                assigned_to_object_id__in=Subquery(latest_file_ids),
            )
            metadata_value_context = self.get_metadata_value_context(
                group_node,
                metadata_queryset,
                template_metadata_queryset,
            )
            negative_choice_predicate = self.compile_negative_choice_subquery(
                spec,
                base_queryset,
                group_node,
                metadata_value_context,
            )
            if negative_choice_predicate is not None:
                return negative_choice_predicate
            metadata_predicate = self.compile_metadata_node(group_node, metadata_value_context, spec.result_type)

            version_metadata_queryset = SearchFieldRegistry.build_metadata_queryset(
                version_metadata_queryset,
                metadata_value_context,
            )
            file_metadata_queryset = SearchFieldRegistry.build_metadata_queryset(
                file_metadata_queryset,
                metadata_value_context,
            )
            template_metadata_queryset = SearchFieldRegistry.build_metadata_queryset(
                template_metadata_queryset,
                metadata_value_context,
            )

            return (
                Q(
                    pk__in=Subquery(
                        UploadsVersion.objects.filter(
                            pk__in=Subquery(
                                version_metadata_queryset.filter(metadata_predicate).values("assigned_to_object_id"),
                            ),
                        ).values("dataset_id"),
                    ),
                )
                | Q(
                    pk__in=Subquery(
                        UploadsVersion.objects.filter(
                            pk__in=Subquery(latest_version_ids),
                            version_file_id__in=Subquery(
                                file_metadata_queryset.filter(metadata_predicate).values("assigned_to_object_id"),
                            ),
                        ).values("dataset_id"),
                    ),
                )
                | Q(
                    pk__in=Subquery(
                        UploadsVersion.objects.filter(
                            pk__in=Subquery(latest_version_ids),
                            metadata_templates__metadata_template_fields__pk__in=Subquery(
                                template_metadata_queryset.filter(metadata_predicate).values("pk"),
                            ),
                        ).values("dataset_id"),
                    ),
                )
            )

        has_template_qualifier = SearchFieldRegistry.contains_template_qualifier(group_node)

        if has_template_qualifier and template_metadata_queryset is not None:
            metadata_value_context = self.get_metadata_value_context(
                group_node,
                Metadata.objects.none(),
                template_metadata_queryset,
            )
            negative_choice_predicate = self.compile_negative_choice_subquery(
                spec,
                base_queryset,
                group_node,
                metadata_value_context,
            )
            if negative_choice_predicate is not None:
                return negative_choice_predicate
            metadata_predicate = self.compile_metadata_node(group_node, metadata_value_context, spec.result_type)
            template_metadata_queryset = SearchFieldRegistry.build_metadata_queryset(
                template_metadata_queryset,
                metadata_value_context,
            )
            return Q(
                pk__in=Subquery(
                    spec.model.objects.filter(
                        pk__in=Subquery(base_queryset.values("pk")),
                        metadata_templates__metadata_template_fields__pk__in=Subquery(
                            template_metadata_queryset.filter(metadata_predicate).values("pk"),
                        ),
                    ).values("pk"),
                ),
            )

        metadata_queryset = Metadata.objects.filter(
            assigned_to_content_type=spec.content_type,
            assigned_to_object_id__in=Subquery(base_queryset.values("pk")),
        )
        metadata_value_context = self.get_metadata_value_context(
            group_node,
            metadata_queryset,
            template_metadata_queryset,
        )
        negative_choice_predicate = self.compile_negative_choice_subquery(
            spec,
            base_queryset,
            group_node,
            metadata_value_context,
        )
        if negative_choice_predicate is not None:
            return negative_choice_predicate
        metadata_queryset = SearchFieldRegistry.build_metadata_queryset(metadata_queryset, metadata_value_context)
        metadata_predicate = self.compile_metadata_node(group_node, metadata_value_context, spec.result_type)
        combined_predicate = Q(
            pk__in=Subquery(
                metadata_queryset.filter(metadata_predicate).values("assigned_to_object_id"),
            ),
        )
        if template_metadata_queryset is not None:
            template_metadata_queryset = SearchFieldRegistry.build_metadata_queryset(
                template_metadata_queryset,
                metadata_value_context,
            )
            combined_predicate |= Q(
                pk__in=Subquery(
                    spec.model.objects.filter(
                        pk__in=Subquery(base_queryset.values("pk")),
                        metadata_templates__metadata_template_fields__pk__in=Subquery(
                            template_metadata_queryset.filter(metadata_predicate).values("pk"),
                        ),
                    ).values("pk"),
                ),
            )

        return combined_predicate

    def compile_negative_choice_subquery(
        self,
        spec: ResultTypeSpec,
        base_queryset: QuerySet,
        group_node: dict,
        metadata_value_context: dict | None,
    ) -> Q | None:
        negative_choice_rule = self.extract_negative_choice_rule(group_node, metadata_value_context)
        if negative_choice_rule is None:
            return None

        candidate_group, forbidden_group = self.split_negative_choice_group(
            group_node,
            negative_choice_rule,
        )
        candidate_predicate = self.compile_metadata_row_subquery(spec, base_queryset, candidate_group)
        forbidden_predicate = self.compile_metadata_row_subquery(spec, base_queryset, forbidden_group)
        return candidate_predicate & ~forbidden_predicate

    @staticmethod
    def extract_negative_choice_rule(
        group_node: dict,
        metadata_value_context: dict | None,
    ) -> dict | None:
        resolved_field_type = metadata_value_context["field_type"] if metadata_value_context else None
        candidate_negative_rules = [
            condition
            for condition in group_node["conditions"]
            if condition["kind"] == "rule"
            and condition["field"] == "metadata.value"
            and condition["operator"] in NEGATIVE_CHOICE_OPERATOR_POSITIVE
        ]
        if group_node["operator"] != "AND":
            if candidate_negative_rules and resolved_field_type in {
                MetadataFieldType.SELECTION,
                MetadataFieldType.MULTI_SELECTION,
                MetadataFieldType.TAG,
            }:
                raise serializers.ValidationError(
                    "Negative choice comparisons require an AND metadata predicate cluster.",
                )
            return None

        sibling_choice_types = {
            condition.get("value")
            for condition in group_node["conditions"]
            if condition["kind"] == "rule"
            and condition["field"] == "metadata.field_type"
            and condition["operator"] == "is"
            and condition.get("value")
            in {MetadataFieldType.SELECTION, MetadataFieldType.MULTI_SELECTION, MetadataFieldType.TAG}
        }
        negative_rules = []
        for condition in candidate_negative_rules:
            explicit_type = condition.get("field_type")
            if (
                explicit_type
                in {
                    MetadataFieldType.SELECTION,
                    MetadataFieldType.MULTI_SELECTION,
                    MetadataFieldType.TAG,
                }
                or len(sibling_choice_types) == 1
                or resolved_field_type
                in {
                    MetadataFieldType.SELECTION,
                    MetadataFieldType.MULTI_SELECTION,
                    MetadataFieldType.TAG,
                }
            ):
                negative_rules.append(condition)

        if len(negative_rules) > 1:
            raise serializers.ValidationError(
                "A metadata predicate cluster supports only one negative choice value comparison.",
            )
        return negative_rules[0] if negative_rules else None

    @staticmethod
    def split_negative_choice_group(group_node: dict, negative_rule: dict) -> tuple[dict, dict]:
        candidate_conditions = [condition for condition in group_node["conditions"] if condition is not negative_rule]
        if negative_rule.get("field_type") and not any(
            condition["kind"] == "rule" and condition["field"] == "metadata.field_type"
            for condition in candidate_conditions
        ):
            candidate_conditions.append(
                {
                    "kind": "rule",
                    "field": "metadata.field_type",
                    "operator": "is",
                    "value": negative_rule["field_type"],
                },
            )

        positive_rule = {
            **negative_rule,
            "operator": NEGATIVE_CHOICE_OPERATOR_POSITIVE[negative_rule["operator"]],
        }
        forbidden_conditions = [
            positive_rule if condition is negative_rule else condition for condition in group_node["conditions"]
        ]
        return (
            {
                "kind": "group",
                "operator": "AND",
                "conditions": candidate_conditions,
            },
            {
                "kind": "group",
                "operator": "AND",
                "conditions": forbidden_conditions,
            },
        )

    def compile_metadata_node(self, node: dict, metadata_value_context: dict | None, result_type: str) -> Q:
        if node["kind"] == "rule":
            return SearchFieldRegistry.build_metadata_predicate(
                rule=node,
                metadata_value_context=metadata_value_context,
                result_type=result_type,
            )

        return combine_predicates(
            node["operator"],
            [self.compile_metadata_node(child, metadata_value_context, result_type) for child in node["conditions"]],
        )

    @staticmethod
    def ensure_group(node: dict) -> dict:
        if node["kind"] == "group":
            return node
        return {
            "kind": "group",
            "operator": "AND",
            "conditions": [node],
        }

    @classmethod
    def is_metadata_only(cls, node: dict) -> bool:
        if node["kind"] == "rule":
            return SearchFieldRegistry.get_field_definition(node["field"]).get("metadata_only", False)

        return all(cls.is_metadata_only(child) for child in node["conditions"])

    @classmethod
    def extract_metadata_source_constraint(cls, node: dict | None) -> set[str] | None:
        if node is None:
            return None

        full_source_set = {
            "instance",
            "template",
            "system",
        }

        if node["kind"] == "rule":
            if node["field"] != "metadata.source":
                return None
            return cls.resolve_metadata_source_rule(node, full_source_set)

        child_constraints = [cls.extract_metadata_source_constraint(child) for child in node["conditions"]]
        if node["operator"] == "AND":
            constrained_children = [
                set(child_constraint) for child_constraint in child_constraints if child_constraint is not None
            ]
            if not constrained_children:
                return None
            allowed_values = set.intersection(*constrained_children)
        else:
            if any(child_constraint is None for child_constraint in child_constraints):
                return None
            allowed_values = set().union(*(set(child_constraint) for child_constraint in child_constraints))

        if allowed_values == full_source_set:
            return None
        return allowed_values

    @staticmethod
    def resolve_metadata_source_rule(rule: dict, full_source_set: set[str]) -> set[str]:
        raw_values = rule.get("value")
        values = raw_values if isinstance(raw_values, list) else [raw_values]
        value_set = set(values)
        if "either" in value_set:
            value_set = set(full_source_set)

        if rule["operator"] in {"is", "in"}:
            return value_set
        if rule["operator"] in {"is_not", "not_in"}:
            return full_source_set - value_set
        return set(full_source_set)

    @staticmethod
    def get_template_metadata_queryset(spec: ResultTypeSpec, base_queryset: QuerySet) -> QuerySet | None:
        if spec.result_type == "dataset_version":
            return MetadataTemplateField.objects.filter(
                metadata_template__uploads_versions__pk__in=Subquery(base_queryset.values("pk")),
            )
        if spec.result_type == "template":
            return MetadataTemplateField.objects.filter(
                metadata_template_id__in=Subquery(base_queryset.values("search_latest_template_pk")),
            )
        if spec.result_type == "template_version":
            return MetadataTemplateField.objects.filter(
                metadata_template_id__in=Subquery(base_queryset.values("pk")),
            )
        return None

    def get_metadata_value_context(
        self,
        node: dict,
        metadata_queryset: QuerySet,
        template_metadata_queryset: QuerySet | None = None,
    ) -> dict | None:
        type_aware_rules = self.extract_type_aware_metadata_value_rules(node)
        if not type_aware_rules:
            return None

        exact_key_values = self.extract_exact_metadata_key_values(node)
        field_type_condition_values = self.extract_metadata_field_type_condition_values(node)
        has_explicit_field_type = any(rule.get("field_type") is not None for rule in type_aware_rules)
        has_field_type_condition = len(field_type_condition_values) == 1
        requires_strict_type_aware_context = any(
            rule["operator"] in STRICT_TYPE_AWARE_METADATA_OPERATORS for rule in type_aware_rules
        )
        requires_type_aware_context = (
            requires_strict_type_aware_context
            or bool(exact_key_values)
            or has_explicit_field_type
            or has_field_type_condition
        )

        if not requires_type_aware_context:
            return None

        has_key = len(exact_key_values) == 1
        is_keyless = not exact_key_values and (has_explicit_field_type or has_field_type_condition)

        if has_key:
            if node["operator"] != "AND":
                raise serializers.ValidationError(
                    "Type-aware metadata comparisons require an AND metadata group with an exact metadata.key rule.",
                )

            exact_key = exact_key_values.pop()
            explicit_field_type = self.extract_explicit_metadata_field_type(node)

            key_match_predicate = Q(custom_key__iexact=exact_key) | Q(field__key__iexact=exact_key)

            def get_matching_field_types(queryset: QuerySet | None) -> set[str]:
                if queryset is None:
                    return set()
                return set(queryset.filter(key_match_predicate).values_list("field_type", flat=True).distinct())

            matching_field_types = get_matching_field_types(metadata_queryset) | get_matching_field_types(
                template_metadata_queryset,
            )

            if explicit_field_type is None:
                if len(matching_field_types) > 1:
                    raise serializers.ValidationError(
                        "Type-aware metadata comparisons require a single unambiguous metadata field type for the selected key.",
                    )

                # A key that doesn't exist for this result type can't match anything here, so it must not make the
                # whole search fail when other selected result types (e.g. subfolders) do have it.
                metadata_value_type = (
                    next(iter(matching_field_types)) if matching_field_types else MetadataFieldType.TEXT
                )
            else:
                if matching_field_types and explicit_field_type not in matching_field_types:
                    raise serializers.ValidationError(
                        f"metadata.value field_type {explicit_field_type} does not match the selected metadata.key.",
                    )
                metadata_value_type = explicit_field_type

            if has_field_type_condition:
                condition_field_type = next(iter(field_type_condition_values))
                if condition_field_type != metadata_value_type:
                    raise serializers.ValidationError(
                        "metadata.field_type condition does not match the resolved metadata value type.",
                    )
        elif is_keyless:
            if has_field_type_condition and node["operator"] != "AND":
                raise serializers.ValidationError(
                    "Type-aware metadata comparisons with a metadata.field_type condition require an AND group.",
                )

            explicit_field_type = self.extract_explicit_metadata_field_type(node)
            condition_field_type = next(iter(field_type_condition_values)) if has_field_type_condition else None

            if explicit_field_type and condition_field_type and explicit_field_type != condition_field_type:
                raise serializers.ValidationError(
                    "metadata.value field_type does not match the metadata.field_type condition.",
                )

            metadata_value_type = explicit_field_type or condition_field_type
            exact_key = None
        else:
            raise serializers.ValidationError(
                "Type-aware metadata comparisons require exactly one metadata.key 'is' rule in the same group, "
                "an explicit field_type on the metadata.value condition, "
                "or a metadata.field_type 'is' condition in the same AND group.",
            )

        if metadata_value_type not in METADATA_OPERATORS_BY_FIELD_TYPE:
            raise serializers.ValidationError(
                "Unsupported metadata field type for type-aware comparison.",
            )

        return {
            "field_type": metadata_value_type,
            "exact_key": exact_key,
        }

    @classmethod
    def contains_type_aware_metadata_value_rule(cls, node: dict) -> bool:
        if node["kind"] == "rule":
            return node["field"] == "metadata.value" and node["operator"] in TYPE_AWARE_METADATA_OPERATORS

        return any(cls.contains_type_aware_metadata_value_rule(child) for child in node["conditions"])

    @classmethod
    def extract_type_aware_metadata_value_rules(cls, node: dict) -> list[dict]:
        if node["kind"] == "rule":
            if node["field"] == "metadata.value" and node["operator"] in TYPE_AWARE_METADATA_OPERATORS:
                return [node]
            return []

        rules = []
        for child in node["conditions"]:
            rules.extend(cls.extract_type_aware_metadata_value_rules(child))
        return rules

    @classmethod
    def extract_exact_metadata_key_values(cls, node: dict) -> set[str]:
        if node["kind"] == "rule":
            if node["field"] == "metadata.key" and node["operator"] == "is" and isinstance(node.get("value"), str):
                return {node["value"]}
            return set()

        values = set()
        for child in node["conditions"]:
            values |= cls.extract_exact_metadata_key_values(child)
        return values

    @classmethod
    def extract_metadata_field_type_condition_values(cls, node: dict) -> set[str]:
        if node["kind"] == "rule":
            if (
                node["field"] == "metadata.field_type"
                and node["operator"] == "is"
                and isinstance(node.get("value"), str)
            ):
                return {node["value"]}
            return set()

        values = set()
        for child in node["conditions"]:
            values |= cls.extract_metadata_field_type_condition_values(child)
        return values

    @classmethod
    def extract_explicit_metadata_field_type(cls, node: dict) -> str | None:
        if node["kind"] == "rule":
            if node["field"] == "metadata.value" and node["operator"] in TYPE_AWARE_METADATA_OPERATORS:
                return node.get("field_type")
            return None

        field_types = {
            child_field_type
            for child in node["conditions"]
            for child_field_type in [cls.extract_explicit_metadata_field_type(child)]
            if child_field_type is not None
        }
        if len(field_types) > 1:
            raise serializers.ValidationError(
                "Type-aware metadata comparisons in the same group must use the same field_type.",
            )
        return next(iter(field_types), None)


class SearchExecutor:
    def __init__(self):
        self.compiler = SearchQueryCompiler()

    RELEVANCE_SORT = [{"field": "relevance", "direction": "desc"}]
    UPDATED_AT_SORT = [{"field": "updated_at", "direction": "desc"}]

    TEXT_MATCH_OPERATORS = {"contains", "starts_with", "is"}

    @classmethod
    def has_positive_text_input(cls, payload: dict) -> bool:
        if payload.get("search_text"):
            return True
        group = payload.get("group")
        if group is not None:
            return cls._group_has_text_match(group)
        return False

    @classmethod
    def _group_has_text_match(cls, node: dict) -> bool:
        if node.get("kind") == "rule":
            field_def = SearchFieldRegistry.FIELD_DEFINITIONS.get(node.get("field", ""), {})
            is_text_field = field_def.get("operators", set()) == STRING_OPERATORS
            return is_text_field and node.get("operator") in cls.TEXT_MATCH_OPERATORS
        for child in node.get("conditions", []):
            if cls._group_has_text_match(child):
                return True
        return False

    def resolve_sort(self, payload: dict) -> list[dict]:
        explicit_sort = payload.get("sort")
        if explicit_sort is not None:
            return explicit_sort
        if self.has_positive_text_input(payload):
            return list(self.RELEVANCE_SORT)
        return list(self.UPDATED_AT_SORT)

    def execute(self, payload: dict, request) -> dict:
        projection_querysets = []
        total_count = 0
        response_mode = payload.get("response_mode", DEFAULT_RESPONSE_MODE)
        sort_rules = self.resolve_sort(payload)
        cursor_payload = self.decode_cursor(payload)
        cursor_direction = payload.get("cursor_direction", DEFAULT_CURSOR_DIRECTION)
        reverse_pagination = cursor_direction == "previous"
        effective_ordering = self.get_ordering_components(sort_rules, reverse=reverse_pagination)
        for result_type in payload["result_types"]:
            spec = RESULT_TYPE_SPECS[result_type]
            filtered_queryset = self.compiler.compile(spec, payload)
            total_count += filtered_queryset.count()
            projection_querysets.append(
                self.build_projection(
                    spec,
                    filtered_queryset,
                    payload.get("search_text"),
                    effective_ordering,
                    cursor_payload=cursor_payload,
                ),
            )

        if not projection_querysets:
            return {
                "count": 0,
                "next_cursor": None,
                "previous_cursor": None,
                "results": [],
            }

        combined_queryset = projection_querysets[0]
        for queryset in projection_querysets[1:]:
            combined_queryset = combined_queryset.union(queryset, all=True)

        limit = payload["limit"]
        ordered_queryset = combined_queryset.order_by(*self.get_ordering(effective_ordering))
        fetched_rows = list(ordered_queryset[: limit + 1])
        has_more_in_requested_direction = len(fetched_rows) > limit
        if has_more_in_requested_direction:
            fetched_rows = fetched_rows[:limit]

        displayed_rows = list(reversed(fetched_rows)) if reverse_pagination else fetched_rows
        results = self.hydrate_rows(displayed_rows, request, response_mode=response_mode)
        has_previous = has_more_in_requested_direction if reverse_pagination else cursor_payload is not None
        has_next = cursor_payload is not None if reverse_pagination else has_more_in_requested_direction

        return {
            "count": total_count,
            "next_cursor": (self.encode_cursor(displayed_rows[-1], payload) if has_next and displayed_rows else None),
            "previous_cursor": (
                self.encode_cursor(displayed_rows[0], payload) if has_previous and displayed_rows else None
            ),
            "results": results,
        }

    def build_projection(
        self,
        spec: ResultTypeSpec,
        queryset: QuerySet,
        search_text: str | None,
        ordering_components: list[tuple[str, str]],
        cursor_payload: dict | None = None,
    ):
        if search_text:
            relevance = Case(
                When(
                    spec.primary_search_query_factory(search_text),
                    then=Value(2),
                ),
                default=Value(1),
                output_field=IntegerField(),
            )
        else:
            relevance = Value(0, output_field=IntegerField())

        projected_queryset = queryset.annotate(
            result_type=Value(spec.result_type, output_field=CharField()),
            entity_pk=Cast("pk", output_field=CharField()),
            relevance=relevance,
            updated_at=Cast("last_modification_date", output_field=DateTimeField()),
            created_at=Cast("creation_date", output_field=DateTimeField()),
        )
        if cursor_payload is not None:
            projected_queryset = projected_queryset.filter(
                self.build_cursor_boundary_predicate(
                    cursor_payload["boundary"],
                    ordering_components,
                ),
            )

        return projected_queryset.values(
            "result_type",
            "entity_pk",
            "relevance",
            "updated_at",
            "created_at",
        )

    @staticmethod
    def get_ordering_components(
        sort_rules: list[dict] | None,
        reverse: bool = False,
    ) -> list[tuple[str, str]]:
        ordering_components = []
        for sort_rule in sort_rules or DEFAULT_SORT:
            field = sort_rule["field"]
            direction = sort_rule["direction"]
            if field not in {"relevance", "updated_at", "created_at"}:
                raise serializers.ValidationError(f"Unsupported sort field: {field}")
            if reverse:
                direction = "asc" if direction == "desc" else "desc"
            ordering_components.append((field, direction))

        ordering_components += [
            (
                "result_type",
                "desc" if reverse else "asc",
            ),
            (
                "entity_pk",
                "desc" if reverse else "asc",
            ),
        ]
        return ordering_components

    @staticmethod
    def get_ordering(ordering_components: list[tuple[str, str]]) -> list[str]:
        ordering = []
        for field, direction in ordering_components:
            prefix = "-" if direction == "desc" else ""
            ordering.append(f"{prefix}{field}")
        return ordering

    def hydrate_rows(self, rows: list[dict], request, response_mode: str = DEFAULT_RESPONSE_MODE) -> list[dict]:
        grouped_ids = defaultdict(list)
        for row in rows:
            grouped_ids[row["result_type"]].append(row["entity_pk"])

        hydrated_objects = {}
        for result_type, entity_ids in grouped_ids.items():
            spec = RESULT_TYPE_SPECS[result_type]
            objects = list(spec.hydration_queryset_factory(entity_ids))
            if result_type == "dataset":
                self.attach_latest_versions(objects)
            if result_type == "template":
                self.attach_latest_templates(objects)
                self.attach_template_assignments(objects)
            if result_type == "template_version":
                self.attach_template_version_assignments(objects)
            hydrated_objects[result_type] = {str(obj.pk): obj for obj in objects}

        results = []
        for row in rows:
            result_type = row["result_type"]
            item = hydrated_objects[result_type].get(row["entity_pk"])
            if item is None:
                continue

            if response_mode == "compact":
                serialized_item = self.build_compact_item(result_type, item)
            else:
                serializer_class = SEARCH_RESULT_SERIALIZERS[result_type]
                serialized_item = serializer_class(item, context={"request": request}).data
            results.append(
                {
                    "result_type": result_type,
                    "item": serialized_item,
                },
            )

        return results

    @staticmethod
    def build_compact_item(result_type: str, item) -> dict:
        if result_type == "project":
            return {
                "pk": str(item.pk),
                "name": item.name,
            }

        if result_type == "folder":
            return {
                "pk": str(item.pk),
                "name": item.name,
                "project": {
                    "pk": str(item.project.pk) if item.project_id else None,
                    "name": item.project.name if item.project_id else None,
                },
            }

        if result_type == "subfolder":
            return {
                "pk": str(item.pk),
                "name": item.name,
                "path": [{"pk": str(subfolder.pk), "name": subfolder.name} for subfolder in item.get_path()],
                "folder": {
                    "pk": str(item.folder.pk),
                    "name": item.folder.name,
                    "project": {
                        "pk": str(item.folder.project.pk) if item.folder.project_id else None,
                        "name": item.folder.project.name if item.folder.project_id else None,
                    },
                },
            }

        if result_type == "dataset":
            latest_version = getattr(item, "search_latest_version", None)
            latest_version_file = (
                latest_version.version_file if latest_version and latest_version.version_file_id else None
            )
            return {
                "pk": str(item.pk),
                "name": item.name,
                "display_name": item.display_name,
                "is_published": bool(item.publication_date),
                "metadata_is_complete": getattr(item, "search_latest_version_metadata_is_complete", None),
                "has_upgradeable_metadata_templates": item.has_upgradeable_metadata_templates,
                "has_file": bool(latest_version_file),
                "folder": {
                    "pk": str(item.folder.pk) if item.folder_id else None,
                    "name": item.folder.name if item.folder_id else None,
                    "project": {
                        "pk": str(item.folder.project.pk) if item.folder_id and item.folder.project_id else None,
                        "name": item.folder.project.name if item.folder_id and item.folder.project_id else None,
                    },
                },
                "latest_version": (
                    {
                        "pk": str(latest_version.pk),
                        "name": latest_version.name,
                        "version_file": (
                            {
                                "pk": str(latest_version_file.pk),
                                "name": latest_version_file.name,
                            }
                            if latest_version_file
                            else None
                        ),
                    }
                    if latest_version
                    else None
                ),
            }

        if result_type == "dataset_version":
            return {
                "pk": str(item.pk),
                "name": item.name,
                "is_published": bool(item.publication_date),
                "metadata_is_complete": item.metadata_is_complete,
                "has_file": bool(item.version_file_id),
                "dataset": {
                    "pk": str(item.dataset.pk) if item.dataset_id else None,
                    "name": item.dataset.name if item.dataset_id else None,
                    "display_name": item.dataset.display_name if item.dataset_id else None,
                },
                "version_file": (
                    {
                        "pk": str(item.version_file.pk),
                        "name": item.version_file.name,
                    }
                    if item.version_file_id
                    else None
                ),
            }

        if result_type == "template_version":
            return {
                "pk": str(item.pk),
                "name": getattr(item, "search_template_version_name", None)
                or item.name
                or item.metadata_template_container.name,
                "template_type": getattr(item, "search_template_type", "global"),
                "field_count": len(item.metadata_template_fields.all()),
                "assigned_to": getattr(item, "search_assigned_to_summary", None),
                "template": {
                    "pk": str(item.metadata_template_container.pk),
                    "name": getattr(item.metadata_template_container, "search_template_name", None)
                    or item.metadata_template_container.name,
                },
            }

        if result_type == "template":
            latest_template = getattr(item, "search_latest_template", None)
            return {
                "pk": str(item.pk),
                "name": getattr(item, "search_template_name", None) or item.name,
                "template_type": getattr(item, "search_template_type", "global"),
                "field_count": (
                    len(latest_template.metadata_template_fields.all()) if latest_template is not None else 0
                ),
                "assigned_to": getattr(item, "search_assigned_to_summary", None),
                "latest_template": (
                    {
                        "pk": str(latest_template.pk),
                        "name": (
                            latest_template.name
                            or getattr(item, "search_latest_template_name", None)
                            or getattr(item, "search_template_name", None)
                            or item.name
                        ),
                    }
                    if latest_template is not None
                    else None
                ),
            }

        if result_type == "file":
            return {
                "pk": str(item.pk),
                "name": item.name,
                "is_published": bool(item.publication_date),
                "uploads_versions": [
                    {
                        "pk": str(version.pk),
                        "name": version.name,
                        "dataset": {
                            "pk": str(version.dataset.pk) if version.dataset_id else None,
                            "name": version.dataset.name if version.dataset_id else None,
                        },
                    }
                    for version in item.uploads_versions.all()
                ],
            }

        return {
            "pk": str(item.pk),
            "name": item.name,
            "is_published": bool(item.publication_date),
        }

    @staticmethod
    def attach_latest_versions(datasets: list[UploadsDataset]) -> None:
        for dataset in datasets:
            dataset.search_latest_version = None

        latest_version_ids = [
            dataset.search_latest_version_pk
            for dataset in datasets
            if getattr(dataset, "search_latest_version_pk", None)
        ]
        if not latest_version_ids:
            return

        latest_versions = {
            str(version.pk): version
            for version in UploadsVersion.objects.filter(pk__in=latest_version_ids).select_related("version_file")
        }
        for dataset in datasets:
            latest_version_pk = getattr(dataset, "search_latest_version_pk", None)
            dataset.search_latest_version = latest_versions.get(str(latest_version_pk)) if latest_version_pk else None

    @staticmethod
    def attach_latest_templates(template_containers: list[MetadataTemplateContainer]) -> None:
        for container in template_containers:
            container.search_latest_template = None

        latest_template_ids = [
            container.search_latest_template_pk
            for container in template_containers
            if getattr(container, "search_latest_template_pk", None)
        ]
        if not latest_template_ids:
            return

        latest_templates = {
            str(template.pk): template
            for template in MetadataTemplate.objects.filter(pk__in=latest_template_ids).prefetch_related(
                "metadata_template_fields",
            )
        }
        for container in template_containers:
            latest_template_pk = getattr(container, "search_latest_template_pk", None)
            container.search_latest_template = (
                latest_templates.get(str(latest_template_pk)) if latest_template_pk else None
            )

    @staticmethod
    def attach_template_assignments(template_containers: list[MetadataTemplateContainer]) -> None:
        project_ids = []
        folder_ids = []
        dataset_ids = []

        project_content_type = ContentType.objects.get_for_model(Project)
        folder_content_type = ContentType.objects.get_for_model(Folder)
        dataset_content_type = ContentType.objects.get_for_model(UploadsDataset)

        for container in template_containers:
            container.search_template_type = "global"
            container.search_assigned_to_summary = None
            if not container.assigned_to_content_type_id or not container.assigned_to_object_id:
                continue

            if container.assigned_to_content_type == project_content_type:
                container.search_template_type = "project"
                project_ids.append(container.assigned_to_object_id)
            elif container.assigned_to_content_type == folder_content_type:
                container.search_template_type = "folder"
                folder_ids.append(container.assigned_to_object_id)
            elif container.assigned_to_content_type == dataset_content_type:
                container.search_template_type = "dataset"
                dataset_ids.append(container.assigned_to_object_id)

        projects = {str(project.pk): project for project in Project.objects.filter(pk__in=project_ids)}
        folders = {
            str(folder.pk): folder for folder in Folder.objects.filter(pk__in=folder_ids).select_related("project")
        }
        datasets = {
            str(dataset.pk): dataset
            for dataset in UploadsDataset.objects.filter(pk__in=dataset_ids).select_related("folder", "folder__project")
        }

        for container in template_containers:
            if not container.assigned_to_object_id:
                continue

            object_id = str(container.assigned_to_object_id)
            if container.search_template_type == "project":
                project = projects.get(object_id)
                if project is not None:
                    container.search_assigned_to_summary = {
                        "result_type": "project",
                        "pk": str(project.pk),
                        "name": project.name,
                    }
            elif container.search_template_type == "folder":
                folder = folders.get(object_id)
                if folder is not None:
                    container.search_assigned_to_summary = {
                        "result_type": "folder",
                        "pk": str(folder.pk),
                        "name": folder.name,
                    }
            elif container.search_template_type == "dataset":
                dataset = datasets.get(object_id)
                if dataset is not None:
                    container.search_assigned_to_summary = {
                        "result_type": "dataset",
                        "pk": str(dataset.pk),
                        "name": dataset.display_name or dataset.name,
                    }

    @classmethod
    def attach_template_version_assignments(cls, template_versions: list[MetadataTemplate]) -> None:
        containers = []
        seen_container_ids = set()
        for template_version in template_versions:
            template_version.search_assigned_to_summary = None
            container = template_version.metadata_template_container
            if str(container.pk) in seen_container_ids:
                continue
            seen_container_ids.add(str(container.pk))
            containers.append(container)

        cls.attach_template_assignments(containers)
        container_map = {str(container.pk): container for container in containers}

        for template_version in template_versions:
            container = container_map.get(str(template_version.metadata_template_container_id))
            if container is None:
                continue
            template_version.search_template_type = getattr(container, "search_template_type", "global")
            template_version.search_assigned_to_summary = getattr(container, "search_assigned_to_summary", None)

    @staticmethod
    def normalize_payload_for_cursor(payload: dict) -> dict:
        normalized_payload = {}
        for key, value in payload.items():
            if key in {
                "cursor",
                "cursor_direction",
                "response_mode",
            }:
                continue
            normalized_payload[key] = value
        return normalized_payload

    @classmethod
    def encode_cursor(cls, row: dict, payload: dict) -> str:
        boundary = {
            "relevance": row["relevance"],
            "updated_at": cls.serialize_cursor_datetime(row["updated_at"]),
            "created_at": cls.serialize_cursor_datetime(row["created_at"]),
            "result_type": row["result_type"],
            "entity_pk": row["entity_pk"],
        }
        token_payload = {
            "sort": payload.get("sort") or list(DEFAULT_SORT),
            "boundary": boundary,
            "payload_digest": cls.build_payload_digest(payload),
        }
        return signing.dumps(token_payload, salt=SEARCH_CURSOR_SALT)

    @classmethod
    def decode_cursor(cls, payload: dict) -> dict | None:
        raw_cursor = payload.get("cursor")
        if not raw_cursor:
            return None

        try:
            cursor_payload = signing.loads(raw_cursor, salt=SEARCH_CURSOR_SALT)
        except signing.BadSignature as e:
            raise serializers.ValidationError("Invalid cursor.") from e

        required_boundary_fields = {
            "relevance",
            "updated_at",
            "created_at",
            "result_type",
            "entity_pk",
        }
        boundary = cursor_payload.get("boundary")
        if not isinstance(boundary, dict) or not required_boundary_fields.issubset(boundary):
            raise serializers.ValidationError("Invalid cursor.")

        if cursor_payload.get("payload_digest") != cls.build_payload_digest(payload):
            raise serializers.ValidationError("Cursor does not match the current search payload.")

        if cursor_payload.get("sort") != (payload.get("sort") or list(DEFAULT_SORT)):
            raise serializers.ValidationError("Cursor does not match the current sort order.")

        try:
            return {
                "sort": cursor_payload["sort"],
                "payload_digest": cursor_payload["payload_digest"],
                "boundary": {
                    "relevance": int(boundary["relevance"]),
                    "updated_at": cls.deserialize_cursor_datetime(boundary["updated_at"]),
                    "created_at": cls.deserialize_cursor_datetime(boundary["created_at"]),
                    "result_type": str(boundary["result_type"]),
                    "entity_pk": str(boundary["entity_pk"]),
                },
            }
        except (TypeError, ValueError) as e:
            raise serializers.ValidationError("Invalid cursor.") from e

    @classmethod
    def build_payload_digest(cls, payload: dict) -> str:
        normalized_payload = cls.normalize_payload_for_cursor(payload)
        return hashlib.sha256(
            json.dumps(
                normalized_payload,
                sort_keys=True,
                separators=(",", ":"),
                default=cls.json_default,
            ).encode("utf-8"),
        ).hexdigest()

    @staticmethod
    def json_default(value):
        if hasattr(value, "isoformat"):
            return value.isoformat()
        raise TypeError(f"Unsupported value in cursor payload: {value!r}")

    @staticmethod
    def serialize_cursor_datetime(value) -> str | None:
        return value.isoformat() if value is not None else None

    @staticmethod
    def deserialize_cursor_datetime(value):
        if value is None:
            return None
        parsed_value = parse_datetime(value) if isinstance(value, str) else None
        if parsed_value is None:
            raise serializers.ValidationError("Invalid cursor.")
        return parsed_value

    @classmethod
    def build_cursor_boundary_predicate(
        cls,
        boundary: dict,
        ordering_components: list[tuple[str, str]],
    ) -> Q:
        predicates = []
        equality_predicate = Q()
        for field, direction in ordering_components:
            lookup = "lt" if direction == "desc" else "gt"
            predicates.append(equality_predicate & Q(**{f"{field}__{lookup}": boundary[field]}))
            equality_predicate &= Q(**{field: boundary[field]})
        return combine_predicates("OR", predicates)
