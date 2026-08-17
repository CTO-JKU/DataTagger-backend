import json
import logging

from django.utils.translation import gettext_lazy as _
from django_fernet.fernet import *
from drf_spectacular import types as drf_spectacular_types
from drf_spectacular import utils as drf_spectacular_utils
from rest_framework import serializers

__all__ = [
    "DecryptedFernetTextField",
    "JSONDictTextField",
]

logger = logging.getLogger(__name__)


@drf_spectacular_utils.extend_schema_field(drf_spectacular_types.OpenApiTypes.STR)
class DecryptedFernetTextField(serializers.Field):
    default_error_messages = {
        "invalid": _("Not a valid string."),
    }

    def __init__(self, secret=None, **kwargs):
        self.secret = secret

        super().__init__(**kwargs)

    def to_internal_value(self, data):
        """
        Takes the raw input date from the user and converts it to its internal representation (with possible
        validation).

        :param data: Raw input object
        :return: Data mapping
        """
        # we explicitly only allow strings or `None` as an input value here.
        if not isinstance(data, (str, type(None))):
            self.fail("invalid")

        if data is None:
            return None

        fernet_data = FernetTextFieldData()
        fernet_data.encrypt(data, self.secret)

        return fernet_data

    def to_representation(self, instance):
        """
        Takes the model instance, gets the relevant data from it, and returns the converted field data from it for
        representation in the API.

        :param instance: Model instance
        :return: Field data
        """
        if not instance:
            return None

        return instance.decrypt(self.secret)


@drf_spectacular_utils.extend_schema_field(drf_spectacular_types.OpenApiTypes.STR)
class JSONDictTextField(serializers.Field):
    default_error_messages = {
        "invalid": _("Not a valid string."),
    }

    def __init__(self, value_transform=None, **kwargs):
        self.value_transform = value_transform

        kwargs.setdefault("default", "{}")

        super().__init__(**kwargs)

    def to_internal_value(self, data):
        """
        Takes the raw input date from the user and converts it to its internal representation (with possible
        validation).

        :param data: Raw input object
        :return: Data mapping
        """
        # we explicitly only allow strings or `None` as an input value here.
        if not isinstance(data, (str, type(None))):
            self.fail("invalid")

        try:
            loaded_data = json.loads(data) or {}

            if not isinstance(loaded_data, (dict,)):
                loaded_data = {}

            if self.value_transform:
                for data_key, data_value in loaded_data.items():
                    loaded_data[data_key] = self.value_transform(data_value)

        except json.JSONDecodeError as exc:
            logger.warning("Could not decode JSON.", exc_info=exc)
            loaded_data = {}

        return json.dumps(loaded_data)

    def to_representation(self, instance):
        """
        Takes the model instance, gets the relevant data from it, and returns the converted field data from it for
        representation in the API.

        :param instance: Model instance
        :return: Field data
        """
        return instance or "{}"

    def validate_empty_values(self, data):
        is_empty_value, data = super().validate_empty_values(data)

        if is_empty_value:
            return is_empty_value, self.default

        return is_empty_value, data
