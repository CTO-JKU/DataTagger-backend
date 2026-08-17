import django_filters
from rest_framework import authentication, filters, pagination, permissions, serializers, viewsets
from rest_framework.utils.field_mapping import get_url_kwargs
from rest_framework.viewsets import GenericViewSet

from fdm.core.rest.authentication import JWTCookieAuthentication
from fdm.core.rest.relations import ContextHyperlinkedIdentityField

__all__ = [
    "BaseFilter",
    "BaseGenericViewSet",
    "BaseModelViewSet",
    "BaseModelSerializer",
]


class BaseFilter(django_filters.FilterSet):
    NUMBER_FILTER = (
        "exact",
        "gt",
        "gte",
        "lt",
        "lte",
    )
    TEXT_FILTER = (
        "exact",
        "icontains",
    )
    BOOL_FILTER = ("exact",)
    KEY_FILTER = BOOL_FILTER
    DATE_FILTER = NUMBER_FILTER
    DATE_TIME_FILTER = NUMBER_FILTER


class BaseGenericViewSet(viewsets.GenericViewSet):
    authentication_classes = (
        JWTCookieAuthentication,
        authentication.SessionAuthentication,
    )
    permission_classes = (permissions.IsAuthenticated,)


class BaseModelViewSet(GenericViewSet):
    filter_backends = (
        django_filters.rest_framework.DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    )
    authentication_classes = (
        JWTCookieAuthentication,
        authentication.SessionAuthentication,
    )
    permission_classes = (permissions.IsAuthenticated,)
    pagination_class = pagination.LimitOffsetPagination

    def get_serializer(self, *args, **kwargs):
        many = kwargs.pop("many", isinstance(kwargs.get("data"), (list, tuple)))
        return super().get_serializer(*args, many=many, **kwargs)


class BaseModelSerializer(serializers.ModelSerializer):
    serializer_url_field = ContextHyperlinkedIdentityField

    def build_relational_field(self, field_name, relation_info):
        """
        Create fields for forward and reverse relationships.
        """
        field_class, field_kwargs = super().build_relational_field(field_name, relation_info)

        model_field = relation_info.model_field
        related_model = relation_info.related_model
        to_many = relation_info.to_many

        # do not make many-to-many relations mandatory
        if related_model and to_many:
            field_kwargs["required"] = False
            field_kwargs["allow_empty"] = True

        # do not make reverse relations mandatory
        if not model_field and related_model:
            field_kwargs["required"] = False
            field_kwargs["allow_null"] = True

        return field_class, field_kwargs

    def build_url_field(self, field_name, model_class):
        """
        Create a field representing the object"s own URL.
        """
        field_class = self.serializer_url_field
        field_kwargs = get_url_kwargs(model_class)

        # if the serializer is working on a view, and the view has `context_params` set
        # then get these `context_params`. else just pass an empty tuple of `context_params`.
        if "view" in self._context:
            context_params = tuple(getattr(self._context["view"], "context_params", tuple()))
        else:
            context_params = tuple()

        # if the url field type is a subclass of an hyperlinked identity field that can
        # handle `context_params`, then add the `context_params` to the constructor kwargs
        # for the field.
        if issubclass(field_class, (ContextHyperlinkedIdentityField,)):
            field_kwargs["context_params"] = context_params

        return field_class, field_kwargs
