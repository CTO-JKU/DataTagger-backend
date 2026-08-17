from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema_field
from rest_framework import fields, serializers

from fdm.core.helpers import get_or_create_user
from fdm.core.rest.base import BaseModelSerializer
from fdm.users.models import Group

User = get_user_model()

__all__ = [
    "GroupSerializer",
    "UserSerializer",
    "UserSettingsUpdateSerializer",
    "MinimalUserSerializer",
    "GetOrCreateUserSerializer",
]


class GroupSerializer(BaseModelSerializer):
    class Meta:
        model = Group
        fields = [
            "pk",
            "name",
        ]


class UserSerializer(BaseModelSerializer):
    full_name = serializers.SerializerMethodField()

    permissions = fields.SerializerMethodField()

    groups = GroupSerializer(
        read_only=True,
        many=True,
    )

    class Meta:
        model = User
        fields = [
            "pk",
            "username",
            "email",
            "first_name",
            "last_name",
            "full_name",
            "is_active",
            "is_superuser",
            "is_anonymized",
            "groups",
            "permissions",
            "can_create_projects",
            "can_hard_delete_datasets",
            "is_global_metadata_template_admin",
            "is_global_approval_queue_admin",
            "authentication_provider",
            "given_name",
            "sn",
            "edu_person_affiliation",
            "im_org_zug_mitarbeiter",
            "im_org_zug_gast",
            "im_org_zug_student",
            "im_akademischer_grad",
            "im_titel_anrede",
            "im_titel_pre",
            "im_titel_post",
            "settings",
        ]

    def get_full_name(self, instance) -> str:
        return " ".join([instance.first_name, instance.last_name]).strip()

    def get_permissions(self, user) -> list[str]:
        permissions = user.get_all_permissions()

        if user.is_authenticated:
            permissions = permissions.union({"__is_authenticated__"})

        if user.is_superuser:
            permissions = permissions.union({"__is_superuser__"})

        return permissions


class UserSettingsUpdateSerializer(BaseModelSerializer):
    class Meta:
        model = User
        fields = ["settings"]

    def validate_settings(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Settings value must be of type dict.")
        return value


class MinimalUserSerializer(UserSerializer):
    class Meta:
        model = User
        fields = [
            "pk",
            "username",
            "email",
            "first_name",
            "last_name",
            "full_name",
            "is_active",
            "is_superuser",
            "is_anonymized",
        ]


@extend_schema_field(
    {
        "oneOf": [
            {
                "type": "string",
                "format": "email",
            },
            {
                "type": "integer",
            },
        ],
    },
)
class GetOrCreateUserSerializer(serializers.Field):
    def to_representation(self, value):
        try:
            return MinimalUserSerializer(value).data
        except AttributeError:
            user = User.objects.get(username=value)
            return MinimalUserSerializer(user).data

    def to_internal_value(self, data):
        if isinstance(data, int):
            try:
                return User.objects.get(pk=data)
            except User.DoesNotExist:
                raise serializers.ValidationError("No user with this ID exists.")

        if isinstance(data, str) and data.isdigit():
            try:
                return User.objects.get(pk=int(data))
            except User.DoesNotExist:
                raise serializers.ValidationError("No user with this ID exists.")

        try:
            return get_or_create_user(data)
        except Exception as e:
            raise serializers.ValidationError(str(e))
