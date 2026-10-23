from drf_spectacular.utils import PolymorphicProxySerializer, extend_schema_field
from rest_framework import serializers

from fdm.metadata.enums import MetadataFieldType
from fdm.search.services import (
    DEFAULT_CURSOR_DIRECTION,
    DEFAULT_RESPONSE_MODE,
    DEFAULT_RESULT_TYPES,
    DEFAULT_SORT,
    SEARCH_CURSOR_DIRECTIONS,
    SEARCH_RESPONSE_MODES,
    SEARCH_RESULT_SERIALIZERS,
    SEARCH_RESULT_TYPES,
    SearchASTValidator,
    SearchFieldRegistry,
)

__all__ = [
    "SearchRequestSerializer",
    "SearchSchemaResponseSerializer",
    "SearchResponseSerializer",
    "SearchMetadataKeySerializer",
    "SearchMetadataKeysResponseSerializer",
    "SearchMetadataChoiceOptionGroupSerializer",
    "SearchMetadataChoiceOptionsResponseSerializer",
]


class SearchMetadataKeySerializer(serializers.Serializer):
    key = serializers.CharField()
    field_type = serializers.ChoiceField(choices=MetadataFieldType.choices)
    source = serializers.ChoiceField(choices=("project", "folder", "dataset", "template"))
    source_id = serializers.UUIDField()
    is_linked = serializers.BooleanField()


class SearchMetadataKeysResponseSerializer(serializers.Serializer):
    results = SearchMetadataKeySerializer(many=True)


class SearchMetadataChoiceOptionGroupSerializer(serializers.Serializer):
    key = serializers.CharField()
    field_type = serializers.ChoiceField(choices=MetadataFieldType.choices)
    source = serializers.ChoiceField(choices=("project", "folder", "dataset", "template"))
    source_id = serializers.UUIDField()
    metadata_template_id = serializers.UUIDField()
    metadata_template_name = serializers.CharField(allow_null=True)
    metadata_template_container_name = serializers.CharField()
    metadata_template_field_id = serializers.UUIDField()
    source_name = serializers.CharField()
    options = serializers.ListField(child=serializers.CharField())


class SearchMetadataChoiceOptionsResponseSerializer(serializers.Serializer):
    results = SearchMetadataChoiceOptionGroupSerializer(many=True)


@extend_schema_field(
    {
        "type": "object",
        "nullable": True,
        "additionalProperties": True,
        "description": (
            "Optional nested condition tree. A group contains an `operator` (`AND` or `OR`) and a "
            "`conditions` array. Each condition is either a rule (`field`, `operator`, `value`) "
            "or another group."
        ),
    },
)
class SearchGroupField(serializers.JSONField):
    pass


class SearchSortSerializer(serializers.Serializer):
    field = serializers.ChoiceField(
        choices=[
            "relevance",
            "updated_at",
            "created_at",
        ],
        help_text="Sort field. Supported values: relevance, updated_at, created_at.",
    )
    direction = serializers.ChoiceField(
        choices=[
            "asc",
            "desc",
        ],
        help_text="Sort direction.",
    )


class SearchRequestSerializer(serializers.Serializer):
    result_types = serializers.ListField(
        child=serializers.ChoiceField(choices=SEARCH_RESULT_TYPES),
        required=False,
        allow_empty=False,
        default=list(DEFAULT_RESULT_TYPES),
        help_text="Result entity types to search across.",
    )
    search_text = serializers.CharField(
        required=False,
        allow_blank=False,
        help_text="Optional broad text search across the main searchable fields of each selected result type.",
    )
    limit = serializers.IntegerField(
        min_value=1,
        max_value=100,
        default=25,
        help_text="Page size.",
    )
    cursor = serializers.CharField(
        required=False,
        allow_blank=False,
        help_text="Opaque cursor returned by a previous search response.",
    )
    cursor_direction = serializers.ChoiceField(
        choices=SEARCH_CURSOR_DIRECTIONS,
        required=False,
        default=DEFAULT_CURSOR_DIRECTION,
        help_text="Pagination direction when replaying a cursor.",
    )
    sort = SearchSortSerializer(
        many=True,
        required=False,
        help_text="Sort rules applied to the unified mixed-type result list. Omit to use the conditional default sort.",
    )
    group = SearchGroupField(
        required=False,
        allow_null=True,
        help_text=(
            "Optional nested condition tree. A group contains an `operator` (`AND` or `OR`) and a `conditions` array. "
            "Each condition is either a rule (`field`, `operator`, `value`) or another group."
        ),
    )
    response_mode = serializers.ChoiceField(
        choices=SEARCH_RESPONSE_MODES,
        required=False,
        default=DEFAULT_RESPONSE_MODE,
        help_text="Response payload mode. Use `compact` for a lighter result card payload.",
    )

    def validate(self, attrs):
        if "offset" in self.initial_data:
            raise serializers.ValidationError(
                {
                    "offset": "offset is no longer supported. Use cursor pagination instead.",
                },
            )

        attrs = SearchASTValidator.normalize_payload(attrs)
        SearchFieldRegistry.validate_payload_result_type_compatibility(
            attrs.get("group"),
            attrs["result_types"],
        )

        if not attrs.get("search_text") and attrs.get("group") is None:
            raise serializers.ValidationError("Either search_text or group is required.")

        return attrs


class SearchResultSerializer(serializers.Serializer):
    result_type = serializers.ChoiceField(choices=SEARCH_RESULT_TYPES)
    item = serializers.SerializerMethodField()

    @extend_schema_field(
        PolymorphicProxySerializer(
            component_name="SearchResultItem",
            serializers=SEARCH_RESULT_SERIALIZERS,
            resource_type_field_name=None,
        ),
    )
    def get_item(self, obj):
        if isinstance(obj["item"], dict):
            return obj["item"]
        serializer_class = SEARCH_RESULT_SERIALIZERS[obj["result_type"]]
        return serializer_class(obj["item"], context=self.context).data


class SearchResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next_cursor = serializers.CharField(
        allow_null=True,
    )
    previous_cursor = serializers.CharField(
        allow_null=True,
    )
    results = SearchResultSerializer(many=True)


# --- Compact mode OpenAPI schemas (named components for frontend reference) ---


class CompactNestedPkName(serializers.Serializer):
    pk = serializers.UUIDField()
    name = serializers.CharField(allow_null=True)


class CompactProjectItem(serializers.Serializer):
    pk = serializers.UUIDField()
    name = serializers.CharField()


class CompactFolderItem(serializers.Serializer):
    pk = serializers.UUIDField()
    name = serializers.CharField()
    project = CompactNestedPkName()


class CompactSubfolderItem(serializers.Serializer):
    pk = serializers.UUIDField()
    name = serializers.CharField()
    path = CompactNestedPkName(many=True)
    folder = CompactFolderItem()


class CompactDatasetItem(serializers.Serializer):
    pk = serializers.UUIDField()
    name = serializers.CharField()
    display_name = serializers.CharField()
    is_published = serializers.BooleanField()
    metadata_is_complete = serializers.BooleanField(allow_null=True)
    has_upgradeable_metadata_templates = serializers.BooleanField()
    has_file = serializers.BooleanField()
    folder = CompactFolderItem()
    latest_version = serializers.DictField(allow_null=True, help_text="Nested {pk, name, version_file: {pk, name}}")


class CompactDatasetVersionItem(serializers.Serializer):
    pk = serializers.UUIDField()
    name = serializers.CharField()
    is_published = serializers.BooleanField()
    metadata_is_complete = serializers.BooleanField()
    has_file = serializers.BooleanField()
    dataset = serializers.DictField(help_text="Nested {pk, name, display_name}")
    version_file = CompactNestedPkName(allow_null=True)


class CompactFileVersionDataset(serializers.Serializer):
    pk = serializers.UUIDField()
    name = serializers.CharField(allow_null=True)


class CompactFileVersion(serializers.Serializer):
    pk = serializers.UUIDField()
    name = serializers.CharField(allow_null=True)
    dataset = CompactFileVersionDataset(allow_null=True)


class CompactFileItem(serializers.Serializer):
    pk = serializers.UUIDField()
    name = serializers.CharField()
    is_published = serializers.BooleanField()
    uploads_versions = CompactFileVersion(many=True)


class CompactTemplateVersionItem(serializers.Serializer):
    pk = serializers.UUIDField()
    name = serializers.CharField()
    template_type = serializers.CharField()
    field_count = serializers.IntegerField()
    assigned_to = serializers.DictField(allow_null=True)
    template = CompactNestedPkName()


class CompactTemplateItem(serializers.Serializer):
    pk = serializers.UUIDField()
    name = serializers.CharField()
    template_type = serializers.CharField()
    field_count = serializers.IntegerField()
    assigned_to = serializers.DictField(allow_null=True)
    latest_template = CompactNestedPkName(allow_null=True)


CompactSearchResultItemSerializer = PolymorphicProxySerializer(
    component_name="CompactSearchResultItem",
    serializers={
        "project": CompactProjectItem,
        "folder": CompactFolderItem,
        "subfolder": CompactSubfolderItem,
        "dataset": CompactDatasetItem,
        "dataset_version": CompactDatasetVersionItem,
        "file": CompactFileItem,
        "template": CompactTemplateItem,
        "template_version": CompactTemplateVersionItem,
    },
    resource_type_field_name=None,
)


class CompactSearchResultSerializer(serializers.Serializer):
    result_type = serializers.ChoiceField(choices=SEARCH_RESULT_TYPES)
    item = serializers.SerializerMethodField()

    @extend_schema_field(CompactSearchResultItemSerializer)
    def get_item(self, obj):
        return obj["item"]


class CompactSearchResponseSerializer(serializers.Serializer):
    """Compact mode response schema. Included via postprocessing hook for TypeScript codegen."""

    count = serializers.IntegerField()
    next_cursor = serializers.CharField(allow_null=True)
    previous_cursor = serializers.CharField(allow_null=True)
    results = CompactSearchResultSerializer(many=True)


# --- Schema endpoint serializers ---


class SearchSchemaFieldSerializer(serializers.Serializer):
    id = serializers.CharField()
    description = serializers.CharField()
    operators = serializers.ListField(child=serializers.CharField())
    metadata_only = serializers.BooleanField()
    enum_values = serializers.ListField(child=serializers.CharField())
    operators_by_field_type = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
    )
    value_type = serializers.CharField()


class SearchSchemaDefaultSortConditionSortSerializer(serializers.Serializer):
    field = serializers.CharField()
    direction = serializers.CharField()


class SearchSchemaDefaultSortConditionSerializer(serializers.Serializer):
    when = serializers.CharField()
    sort = SearchSchemaDefaultSortConditionSortSerializer(many=True)


class SearchSchemaTypeSerializer(serializers.Serializer):
    fields = SearchSchemaFieldSerializer(many=True)
    sort_fields = serializers.ListField(child=serializers.CharField())
    default_sort = SearchSortSerializer(many=True)
    default_sort_conditions = SearchSchemaDefaultSortConditionSerializer(many=True)


class SearchSchemaResponseSerializer(serializers.Serializer):
    result_types = serializers.ListField(child=serializers.CharField())
    default_result_types = serializers.ListField(child=serializers.CharField())
    sort_directions = serializers.ListField(child=serializers.CharField())
    response_modes = serializers.ListField(child=serializers.CharField())
    types = serializers.DictField(child=SearchSchemaTypeSerializer())
