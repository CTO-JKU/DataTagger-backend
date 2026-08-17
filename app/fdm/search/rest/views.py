from uuid import UUID

from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django_userforeignkey.request import get_current_user
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from fdm.core.rest.base import BaseModelViewSet
from fdm.metadata.enums import MetadataFieldType
from fdm.search.metadata_choice_options.service import collect_metadata_choice_options
from fdm.search.metadata_keys.service import collect_metadata_key_suggestions
from fdm.search.rest.serializers import (
    CompactSearchResponseSerializer,
    SearchMetadataChoiceOptionsResponseSerializer,
    SearchMetadataKeysResponseSerializer,
    SearchRequestSerializer,
    SearchResponseSerializer,
    SearchSchemaResponseSerializer,
)
from fdm.search.services import SEARCH_RESPONSE_MODES, SearchExecutor, SearchFieldCatalog

__all__ = [
    "SearchViewSet",
]


class SearchViewSet(
    BaseModelViewSet,
    viewsets.ViewSet,
):
    throttle_classes = []
    filterset_class = []
    filterset_fields = []
    search_fields = []

    permission_classes_by_action = {
        "search_global": [IsAuthenticated],
        "search_global_schema": [IsAuthenticated],
        "metadata_keys": [IsAuthenticated],
        "metadata_choice_options": [IsAuthenticated],
    }

    SCOPE_KINDS = ("project", "folder", "dataset")

    def get_permissions(self):
        return [permission() for permission in self.permission_classes_by_action.get(self.action, [])]

    @extend_schema(
        description=(
            "Structured global search across projects, folders, datasets, dataset versions, files, templates, and template versions.\n\n"
            "Use `search_text` for broad text search, `group` for Airtable-style structured filters, "
            "or both together. The response is one unified paginated list where each result contains "
            "`result_type` plus the serialized entity under `item`. Pagination uses opaque cursors."
        ),
        parameters=[
            OpenApiParameter(
                name="view",
                type=str,
                location=OpenApiParameter.QUERY,
                enum=SEARCH_RESPONSE_MODES,
                description="Optional response payload mode override. `compact` returns a lighter result item shape.",
            ),
        ],
        request=SearchRequestSerializer,
        responses={
            (200, "application/json"): SearchResponseSerializer,
            (200, "application/json;mode=compact"): OpenApiResponse(
                response=CompactSearchResponseSerializer,
                description="Compact mode response shape (when response_mode='compact').",
            ),
        },
        examples=[
            OpenApiExample(
                "Broad Search",
                summary="Default broad search across all entity types",
                value={
                    "result_types": [
                        "project",
                        "folder",
                        "dataset",
                        "dataset_version",
                        "file",
                        "template",
                        "template_version",
                    ],
                    "search_text": "Horizon 2.5",
                    "limit": 25,
                    "sort": [
                        {
                            "field": "relevance",
                            "direction": "desc",
                        },
                        {
                            "field": "updated_at",
                            "direction": "desc",
                        },
                        {
                            "field": "created_at",
                            "direction": "desc",
                        },
                    ],
                },
                request_only=True,
            ),
            OpenApiExample(
                "Cursor Pagination",
                summary="Continue a previous search page with an opaque cursor",
                value={
                    "result_types": [
                        "dataset",
                        "dataset_version",
                    ],
                    "search_text": "open",
                    "limit": 10,
                    "cursor": "opaque-cursor-from-previous-response",
                    "cursor_direction": "next",
                },
                request_only=True,
            ),
            OpenApiExample(
                "Metadata Match",
                summary="Find dataset versions whose metadata key/value pair matches on the same metadata row",
                value={
                    "result_types": [
                        "dataset_version",
                    ],
                    "group": {
                        "operator": "AND",
                        "conditions": [
                            {
                                "kind": "rule",
                                "field": "metadata.key",
                                "operator": "is",
                                "value": "Author",
                            },
                            {
                                "kind": "rule",
                                "field": "metadata.value",
                                "operator": "contains",
                                "value": "research-team",
                            },
                        ],
                    },
                },
                request_only=True,
            ),
            OpenApiExample(
                "Nested Group",
                summary="Mix nested groups with broad search",
                value={
                    "result_types": [
                        "project",
                        "folder",
                    ],
                    "search_text": "Horizon",
                    "group": {
                        "operator": "AND",
                        "conditions": [
                            {
                                "field": "description",
                                "operator": "contains",
                                "value": "consortium",
                            },
                            {
                                "kind": "group",
                                "operator": "OR",
                                "conditions": [
                                    {
                                        "field": "my_project_permission",
                                        "operator": "is",
                                        "value": "project_admin",
                                    },
                                    {
                                        "field": "my_folder_permission",
                                        "operator": "is",
                                        "value": "editor",
                                    },
                                ],
                            },
                        ],
                    },
                },
                request_only=True,
            ),
            OpenApiExample(
                "Typed Metadata Comparison",
                summary="Numeric comparison on metadata value",
                value={
                    "result_types": [
                        "dataset_version",
                    ],
                    "group": {
                        "operator": "AND",
                        "conditions": [
                            {
                                "field": "metadata.key",
                                "operator": "is",
                                "value": "SampleCount",
                            },
                            {
                                "field": "metadata.value",
                                "operator": "gt",
                                "value": 100,
                                "field_type": "INTEGER",
                            },
                        ],
                    },
                },
                request_only=True,
            ),
            OpenApiExample(
                "Typed Metadata Equality",
                summary="Exact numeric equality on metadata value",
                value={
                    "result_types": [
                        "dataset_version",
                    ],
                    "group": {
                        "operator": "AND",
                        "conditions": [
                            {
                                "field": "metadata.key",
                                "operator": "is",
                                "value": "SampleCount",
                            },
                            {
                                "field": "metadata.value",
                                "operator": "is",
                                "value": 150,
                                "field_type": "INTEGER",
                            },
                        ],
                    },
                },
                request_only=True,
            ),
            OpenApiExample(
                "Scoped Compact Search",
                summary="Search only inside one folder and return compact result cards",
                value={
                    "result_types": [
                        "file",
                    ],
                    "group": {
                        "operator": "AND",
                        "conditions": [
                            {
                                "field": "folder_id",
                                "operator": "is",
                                "value": "714b48a6-24d9-45de-be5f-69b9b1e6f299",
                            },
                            {
                                "field": "file_name",
                                "operator": "ends_with",
                                "value": ".csv",
                            },
                        ],
                    },
                    "response_mode": "compact",
                },
                request_only=True,
            ),
            OpenApiExample(
                "Datasets With Outdated Templates",
                summary="Datasets whose latest version is pinned to an outdated metadata template version",
                value={
                    "result_types": [
                        "dataset",
                    ],
                    "group": {
                        "operator": "AND",
                        "conditions": [
                            {
                                "field": "has_upgradeable_metadata_templates",
                                "operator": "is",
                                "value": True,
                            },
                        ],
                    },
                },
                request_only=True,
            ),
            OpenApiExample(
                "Datasets Needing Attention",
                summary="Datasets whose latest version is both incomplete and pinned to an outdated template",
                value={
                    "result_types": [
                        "dataset",
                    ],
                    "group": {
                        "operator": "AND",
                        "conditions": [
                            {
                                "field": "metadata_is_complete",
                                "operator": "is",
                                "value": False,
                            },
                            {
                                "field": "has_upgradeable_metadata_templates",
                                "operator": "is",
                                "value": True,
                            },
                        ],
                    },
                },
                request_only=True,
            ),
            OpenApiExample(
                "Multi-key Metadata Search",
                summary="Datasets that have both temperature > 30 and humidity > 40 on the latest version",
                description=(
                    "Each `(metadata.key, metadata.value)` cluster is wrapped in its own nested AND group so that "
                    "the executor binds each cluster to a separate metadata row. Filter UIs typically build this "
                    "shape automatically from flat 'Add row' actions."
                ),
                value={
                    "result_types": [
                        "dataset",
                    ],
                    "group": {
                        "operator": "AND",
                        "conditions": [
                            {
                                "kind": "group",
                                "operator": "AND",
                                "conditions": [
                                    {
                                        "field": "metadata.key",
                                        "operator": "is",
                                        "value": "temperature",
                                    },
                                    {
                                        "field": "metadata.value",
                                        "operator": "gt",
                                        "value": 30,
                                        "field_type": "integer",
                                    },
                                ],
                            },
                            {
                                "kind": "group",
                                "operator": "AND",
                                "conditions": [
                                    {
                                        "field": "metadata.key",
                                        "operator": "is",
                                        "value": "humidity",
                                    },
                                    {
                                        "field": "metadata.value",
                                        "operator": "gt",
                                        "value": 40,
                                        "field_type": "integer",
                                    },
                                ],
                            },
                        ],
                    },
                },
                request_only=True,
            ),
            OpenApiExample(
                "Template Metadata Filter",
                summary="Match only template-backed dataset version metadata",
                value={
                    "result_types": [
                        "dataset_version",
                    ],
                    "group": {
                        "operator": "AND",
                        "conditions": [
                            {
                                "field": "metadata.key",
                                "operator": "is",
                                "value": "SampleCount",
                            },
                            {
                                "field": "metadata.source",
                                "operator": "is",
                                "value": "template",
                            },
                        ],
                    },
                },
                request_only=True,
            ),
            OpenApiExample(
                "Template Search",
                summary="Find project or global templates with an integer field",
                value={
                    "result_types": [
                        "template",
                    ],
                    "group": {
                        "operator": "AND",
                        "conditions": [
                            {
                                "field": "template_type",
                                "operator": "in",
                                "value": [
                                    "global",
                                    "project",
                                ],
                            },
                            {
                                "field": "metadata.field_type",
                                "operator": "is",
                                "value": "INTEGER",
                            },
                        ],
                    },
                },
                request_only=True,
            ),
            OpenApiExample(
                "Template Version Search",
                summary="Search historical template versions directly",
                value={
                    "result_types": [
                        "template_version",
                    ],
                    "group": {
                        "operator": "AND",
                        "conditions": [
                            {
                                "field": "template_type",
                                "operator": "is",
                                "value": "folder",
                            },
                            {
                                "field": "metadata.field_type",
                                "operator": "is",
                                "value": "INTEGER",
                            },
                        ],
                    },
                },
                request_only=True,
            ),
        ],
    )
    @action(
        detail=False,
        methods=["POST"],
        url_path="global",
        url_name="global",
        filterset_class=None,
        pagination_class=None,
    )
    def search_global(self, request):
        request_serializer = SearchRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)

        payload = dict(request_serializer.validated_data)
        response_mode_override = request.query_params.get("view")
        if "response_mode" not in request.data and response_mode_override:
            if response_mode_override not in SEARCH_RESPONSE_MODES:
                raise serializers.ValidationError(f"Unsupported view value: {response_mode_override}")
            payload["response_mode"] = response_mode_override

        results = SearchExecutor().execute(payload, request)
        response_serializer = SearchResponseSerializer(results, context={"request": request})
        return Response(
            data=response_serializer.data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        description="Return the searchable field/operator schema for the global search endpoint.",
        responses={
            200: SearchSchemaResponseSerializer,
        },
    )
    @action(
        detail=False,
        methods=["GET"],
        url_path="global/schema",
        url_name="global-schema",
        filterset_class=None,
        pagination_class=None,
    )
    def search_global_schema(self, request):
        response_serializer = SearchSchemaResponseSerializer(SearchFieldCatalog.get_schema())
        return Response(
            data=response_serializer.data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        description=(
            "Return autocomplete-ready metadata keys for the active metadata template on the "
            "given projects, folders, or datasets and their ancestors, or for explicitly-named "
            "metadata template containers via `template_id`. At least one of `project_id`, "
            "`folder_id`, `dataset_id`, `template_id` is required; each may be repeated and any "
            "combination is allowed. When `template_id` is supplied, those templates' fields are "
            "the authoritative key universe; entity-scope rows are kept only when their key also "
            "appears on a supplied template (the same key may therefore appear twice — once "
            "tagged `template`, once tagged with its entity source)."
        ),
        parameters=[
            OpenApiParameter(
                name="project_id",
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
                many=True,
                description="One or more project UUIDs to include in the scope.",
            ),
            OpenApiParameter(
                name="folder_id",
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
                many=True,
                description="One or more folder UUIDs; each pulls in keys from its parent project too.",
            ),
            OpenApiParameter(
                name="dataset_id",
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
                many=True,
                description="One or more dataset UUIDs; each pulls in keys from its parent folder and project.",
            ),
            OpenApiParameter(
                name="template_id",
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
                many=True,
                description=(
                    "One or more metadata template container UUIDs. Rows from these templates are "
                    'tagged `source: "template"`. When combined with entity scopes, entity rows '
                    "are filtered to keys that also appear on a supplied template."
                ),
            ),
        ],
        responses={
            200: SearchMetadataKeysResponseSerializer,
        },
        examples=[
            OpenApiExample(
                "Multi-scope",
                summary="Combine a folder scope and a standalone project scope",
                value={
                    "results": [
                        {
                            "key": "Author",
                            "field_type": "TEXT",
                            "source": "project",
                            "source_id": "11111111-1111-1111-1111-111111111111",
                            "is_linked": True,
                        },
                        {
                            "key": "Notes",
                            "field_type": "TEXT",
                            "source": "folder",
                            "source_id": "22222222-2222-2222-2222-222222222222",
                            "is_linked": False,
                        },
                    ],
                },
                response_only=True,
            ),
            OpenApiExample(
                "Template-only",
                summary="Autocomplete keys from one or more explicitly-named metadata template containers",
                value={
                    "results": [
                        {
                            "key": "CapturedAt",
                            "field_type": "DATE",
                            "source": "template",
                            "source_id": "33333333-3333-3333-3333-333333333333",
                            "is_linked": False,
                        },
                    ],
                },
                response_only=True,
            ),
            OpenApiExample(
                "Folder + Template overlap",
                summary=(
                    "Combine a folder scope with an explicit template_id. Keys are sourced from the "
                    "template; the folder's active-template keys appear as additional rows only where "
                    "they overlap the supplied template's keys."
                ),
                value={
                    "results": [
                        {
                            "key": "TemplateOnlyKey",
                            "field_type": "DATE",
                            "source": "template",
                            "source_id": "33333333-3333-3333-3333-333333333333",
                            "is_linked": False,
                        },
                        {
                            "key": "SharedKey",
                            "field_type": "INTEGER",
                            "source": "template",
                            "source_id": "33333333-3333-3333-3333-333333333333",
                            "is_linked": False,
                        },
                        {
                            "key": "SharedKey",
                            "field_type": "TEXT",
                            "source": "folder",
                            "source_id": "22222222-2222-2222-2222-222222222222",
                            "is_linked": False,
                        },
                    ],
                },
                response_only=True,
            ),
        ],
    )
    @action(
        detail=False,
        methods=["GET"],
        url_path="metadata-keys",
        url_name="metadata-keys",
        filterset_class=None,
        pagination_class=None,
    )
    def metadata_keys(self, request):
        scopes, template_ids = self._extract_scopes(request)
        results = collect_metadata_key_suggestions(scopes, request.user, template_ids=template_ids)
        return Response(
            data={"results": results},
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        description=(
            "Return configured options for bounded metadata keys and one choice field type. "
            "Scopes are optional and use the same permission semantics as metadata-key autocomplete."
        ),
        parameters=[
            OpenApiParameter(
                name="key",
                type=str,
                location=OpenApiParameter.QUERY,
                required=True,
                many=True,
                description="One or more exact metadata keys, in display order.",
            ),
            OpenApiParameter(
                name="field_type",
                type=str,
                location=OpenApiParameter.QUERY,
                required=True,
                enum=[
                    MetadataFieldType.SELECTION,
                    MetadataFieldType.MULTI_SELECTION,
                    MetadataFieldType.TAG,
                ],
            ),
            *[
                OpenApiParameter(
                    name=f"{kind}_id",
                    type=str,
                    location=OpenApiParameter.QUERY,
                    required=False,
                    many=True,
                )
                for kind in (*SCOPE_KINDS, "template")
            ],
        ],
        responses={200: SearchMetadataChoiceOptionsResponseSerializer},
    )
    @action(
        detail=False,
        methods=["GET"],
        url_path="metadata-choice-options",
        url_name="metadata-choice-options",
        filterset_class=None,
        pagination_class=None,
    )
    def metadata_choice_options(self, request):
        keys = list(dict.fromkeys(key for key in request.query_params.getlist("key") if key))
        if not keys:
            raise serializers.ValidationError({"key": "At least one non-empty key is required."})

        field_type = request.query_params.get("field_type")
        if field_type not in {
            MetadataFieldType.SELECTION,
            MetadataFieldType.MULTI_SELECTION,
            MetadataFieldType.TAG,
        }:
            raise serializers.ValidationError(
                {"field_type": "Must be SELECTION, MULTI_SELECTION, or TAG."},
            )

        scopes, template_ids = self._extract_scopes(request, require_any=False)
        results = collect_metadata_choice_options(
            keys=keys,
            field_type=field_type,
            scopes=scopes,
            user=request.user,
            template_ids=template_ids,
        )
        return Response(data={"results": results}, status=status.HTTP_200_OK)

    def _extract_scopes(self, request, *, require_any=True):
        scopes: list[tuple[str, UUID]] = []
        for kind in self.SCOPE_KINDS:
            param = f"{kind}_id"
            for raw in request.query_params.getlist(param):
                try:
                    scopes.append((kind, UUID(raw)))
                except (ValueError, AttributeError):
                    raise serializers.ValidationError({param: "Must be a valid UUID."})

        template_ids: list[UUID] = []
        for raw in request.query_params.getlist("template_id"):
            try:
                template_ids.append(UUID(raw))
            except (ValueError, AttributeError):
                raise serializers.ValidationError({"template_id": "Must be a valid UUID."})

        if require_any and not scopes and not template_ids:
            raise serializers.ValidationError(
                "At least one of project_id, folder_id, dataset_id, template_id is required.",
            )

        return scopes, template_ids
