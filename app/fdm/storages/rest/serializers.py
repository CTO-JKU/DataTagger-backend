from django.conf import settings
from rest_framework import serializers

from fdm.core.rest.serializers import BaseModelWithByUserSerializer
from fdm.storages.models import *
from fdm.storages.rest.fernet_serializer_fields import DecryptedFernetTextField, JSONDictTextField

__all__ = [
    "StorageSerializer",
]


class StorageSerializer(BaseModelWithByUserSerializer):
    # This is an example implementation of a fernet encrypted string
    # This could be used to store a password for example
    local_private_dss_path = DecryptedFernetTextField(
        source="local_private_dss_path_encrypted",
        secret=settings.SECRET_KEY,
    )

    # This is an example implementation of a fernet encrypted JSON/Dict stored in a fernet text field
    # This could be used to hold a complete credentials object
    # text_encrypted_example = JSONDictTextField(
    #     value_transform=str,
    #     required=False,
    #     allow_null=True,
    # )

    class Meta:
        model = DynamicStorage
        fields = [
            "pk",
            "name",
            "description",
            "storage_type",
            "default",
            "local_private_dss_path",
        ]


class StorageCreatePayloadSerializer(serializers.Serializer):
    name = serializers.CharField(
        required=True,
        allow_null=False,
    )

    description = serializers.JSONField(
        allow_null=False,
        default=dict,
    )

    storage_type = serializers.CharField(
        required=True,
        allow_null=False,
    )

    local_private_dss_path = DecryptedFernetTextField(
        source="local_private_dss_path_encrypted",
        secret=settings.SECRET_KEY,
    )

    def validate_storage_type(self, value):
        """
        Validate that storage_type is 'PRIVATE_DSS'
        """
        if value != "private_dss":
            raise serializers.ValidationError("Only 'private_dss' is allowed as the storage type for now.")
        return value
