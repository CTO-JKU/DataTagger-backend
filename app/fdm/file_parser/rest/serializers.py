from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from fdm.core.rest.serializers import BaseModelWithByUserSerializer, ContentTypeSerializer
from fdm.file_parser.models import FileParser, FileParserConfiguration

__all__ = [
    "FileParserAvailableTypeSerializer",
    "FileParserConfigurationSerializer",
    "FileParserProcessingSerializer",
]


class FileParserAvailableTypeSerializer(serializers.Serializer):
    parser_type = serializers.CharField()

    label = serializers.CharField()

    config_schema = serializers.JSONField()


class FileParserConfigurationSerializer(BaseModelWithByUserSerializer):
    assigned_to_content_type = ContentTypeSerializer()

    class Meta:
        model = FileParserConfiguration
        fields = [
            "pk",
            "parser_type",
            "assigned_to_content_type",
            "assigned_to_object_id",
            "enabled",
            "config",
            "created_by",
            "creation_date",
            "last_modified_by",
            "last_modification_date",
        ]

    def validate(self, attrs):
        data = {}
        for field_name in [
            "parser_type",
            "assigned_to_content_type",
            "assigned_to_object_id",
            "enabled",
            "config",
        ]:
            data[field_name] = attrs.get(field_name, getattr(self.instance, field_name, None))

        instance = FileParserConfiguration(**data)
        if self.instance:
            instance.pk = self.instance.pk

        try:
            instance.clean()
        except DjangoValidationError as e:
            raise serializers.ValidationError(getattr(e, "message_dict", None) or e.messages)

        return attrs


class FileParserProcessingSerializer(BaseModelWithByUserSerializer):
    configuration = serializers.PrimaryKeyRelatedField(
        read_only=True,
    )

    class Meta:
        model = FileParser
        fields = [
            "pk",
            "parser_type",
            "configuration",
            "configuration_snapshot",
            "status",
            "created_by",
            "creation_date",
            "last_modified_by",
            "last_modification_date",
        ]
