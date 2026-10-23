from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from fdm.core.rest.base import BaseModelSerializer
from fdm.core.rest.serializers import BaseModelWithByUserSerializer
from fdm.elabftw.models import ElabftwConnection, ExperimentLink

__all__ = [
    "ElabftwConnectionSerializer",
    "ElabftwConnectionTestSerializer",
    "ElabftwExperimentSerializer",
    "ExperimentLinkSerializer",
    "ExperimentLinkCreateSerializer",
    "LinkedExperimentPlaceSerializer",
    "LinkedExperimentSerializer",
]


class ElabftwConnectionSerializer(BaseModelSerializer):
    # Write only: the key is stored on the server and never sent back to the browser
    api_key = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=False,
        trim_whitespace=True,
    )

    has_api_key = serializers.SerializerMethodField()

    class Meta:
        model = ElabftwConnection
        fields = [
            "url",
            "api_key",
            "has_api_key",
            "verify_tls",
        ]

    def get_has_api_key(self, obj) -> bool:
        return bool(obj.api_key)

    def validate(self, attrs):
        if not self.instance and not attrs.get("api_key"):
            raise serializers.ValidationError({"api_key": "Please enter your eLabFTW API key."})

        return attrs


class ElabftwConnectionTestSerializer(serializers.Serializer):
    fullname = serializers.CharField()

    email = serializers.CharField(
        allow_null=True,
    )


class ElabftwExperimentSerializer(serializers.Serializer):
    id = serializers.IntegerField()

    title = serializers.CharField(
        allow_blank=True,
    )

    date = serializers.CharField(
        allow_null=True,
    )

    category = serializers.CharField(
        allow_null=True,
    )

    status = serializers.CharField(
        allow_null=True,
    )

    owner = serializers.CharField(
        allow_null=True,
    )

    url = serializers.CharField()


class ExperimentLinkInheritedFromSerializer(serializers.Serializer):
    target_type = serializers.ChoiceField(
        choices=ExperimentLink.TargetType.choices,
    )

    target_id = serializers.UUIDField()

    name = serializers.CharField()


class ExperimentLinkSerializer(BaseModelWithByUserSerializer):
    experiment_url = serializers.CharField(
        read_only=True,
    )

    # Set when the link was made on a subfolder or folder containing the requested target
    inherited_from = serializers.SerializerMethodField()

    class Meta:
        model = ExperimentLink
        fields = [
            "pk",
            "target_type",
            "target_id",
            "experiment_id",
            "title",
            "elabftw_url",
            "experiment_url",
            "inherited_from",
        ]

    @extend_schema_field(ExperimentLinkInheritedFromSerializer(allow_null=True))
    def get_inherited_from(self, obj):
        source = getattr(obj, "inherited_from", None)
        if source is None:
            return None

        return {
            "target_type": obj.target_type,
            "target_id": source.pk,
            "name": source.name,
        }


class ExperimentLinkCreateSerializer(serializers.Serializer):
    target_type = serializers.ChoiceField(
        choices=ExperimentLink.TargetType.choices,
    )

    target_id = serializers.UUIDField()

    experiment_id = serializers.IntegerField(
        min_value=1,
    )


class LinkedExperimentPlaceSerializer(serializers.Serializer):
    link_id = serializers.UUIDField()

    target_type = serializers.ChoiceField(
        choices=ExperimentLink.TargetType.choices,
    )

    target_id = serializers.UUIDField()

    name = serializers.CharField()

    path = serializers.CharField()

    project_id = serializers.UUIDField(
        allow_null=True,
    )

    folder_id = serializers.UUIDField(
        allow_null=True,
    )

    subfolder_id = serializers.UUIDField(
        allow_null=True,
    )


class LinkedExperimentSerializer(serializers.Serializer):
    elabftw_url = serializers.CharField()

    experiment_id = serializers.IntegerField()

    title = serializers.CharField(
        allow_blank=True,
    )

    experiment_url = serializers.CharField()

    link_ids = serializers.ListField(
        child=serializers.UUIDField(),
    )

    places = LinkedExperimentPlaceSerializer(
        many=True,
    )

    files_count = serializers.IntegerField()
