from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django_userforeignkey.request import get_current_user
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from fdm.core.helpers import get_or_create_user
from fdm.core.rest.base import BaseModelSerializer
from fdm.core.rest.serializers import BaseModelWithByUserSerializer
from fdm.folders.models import FolderPermission
from fdm.folders.rest.serializers import MetadataTemplateGetOrCreateSerializer, MinimalFolderSerializer
from fdm.metadata.helpers import set_metadata_for_relation
from fdm.metadata.rest.serializers import (
    MetadataPayloadSerializer,
    MetadataTemplateSerializer,
    MinimalMetadataSerializer,
    MinimalMetadataTemplateSerializer,
)
from fdm.projects.models import *
from fdm.users.rest.serializers import GetOrCreateUserSerializer, MinimalUserSerializer

User = get_user_model()

__all__ = [
    "ProjectUsersSerializer",
    "ProjectUsersWithFolderPermissionsSerializer",
    "ProjectMembersActionPayloadSerializer",
    "ProjectCreatePayloadSerializer",
    "ProjectCreateSerializer",
    "ProjectUpdatePayloadSerializer",
    "ProjectUpdateSerializer",
    "ProjectListSerializer",
    "ProjectSerializer",
    "MinimalProjectSerializer",
    "ProjectSearchSerializer",
    "ProjectMembershipCreateSerializer",
    "ProjectMembershipUpdateSerializer",
    "ProjectMembershipSerializer",
]


class ProjectUsersSerializer(serializers.Serializer):
    email = serializers.EmailField()

    is_project_admin = serializers.BooleanField()

    is_metadata_template_admin = serializers.BooleanField()

    can_create_folders = serializers.BooleanField()


class ProjectUsersWithFolderPermissionsSerializer(serializers.Serializer):
    email = serializers.EmailField()

    is_project_admin = serializers.BooleanField()

    is_project_metadata_template_admin = serializers.BooleanField()

    can_create_folders = serializers.BooleanField()

    is_folder_admin = serializers.BooleanField()

    is_folder_metadata_template_admin = serializers.BooleanField()

    can_edit_folder = serializers.BooleanField()

    can_view_folder = serializers.BooleanField()


class ProjectMembersActionPayloadSerializer(serializers.Serializer):
    project_users = ProjectUsersSerializer(
        many=True,
        required=True,
    )


class ProjectCreatePayloadSerializer(serializers.Serializer):
    name = serializers.CharField(
        required=True,
        allow_null=False,
    )

    description = serializers.JSONField(
        allow_null=False,
        default=dict,
    )

    folder_name = serializers.CharField(
        required=False,
        allow_null=True,
    )

    project_users = ProjectUsersWithFolderPermissionsSerializer(
        many=True,
        required=False,
    )

    metadata_template = MetadataTemplateGetOrCreateSerializer(
        required=False,
        allow_null=True,
    )

    metadata = MetadataPayloadSerializer(
        required=False,
    )


class ProjectCreateSerializer(BaseModelWithByUserSerializer):
    folder_name = serializers.CharField(
        required=False,
        allow_null=True,
    )

    project_users = ProjectUsersWithFolderPermissionsSerializer(
        many=True,
        required=False,
    )

    metadata_template = MetadataTemplateGetOrCreateSerializer(
        required=False,
        allow_null=True,
    )

    metadata = MinimalMetadataSerializer(
        many=True,
        required=False,
    )

    class Meta:
        model = Project
        fields = [
            "name",
            "description",
            "folder_name",
            "project_users",
            "metadata_template",
            "metadata",
        ]

    def create(self, validated_data):
        folder_name = validated_data.pop("folder_name", None)
        project_users_data = validated_data.pop("project_users", [])
        metadata_template = validated_data.get("metadata_template", None)
        metadata_template_payload = self.context["request"].data.get("metadata_template", None)
        metadata = None

        if "metadata" in validated_data:
            metadata = self.context["request"].data.get("metadata", [])
            validated_data.pop("metadata", [])

        project = Project.objects.create(**validated_data)

        # Rename the automatically created folder to the custom name provided by the user
        if folder_name:
            folder = project.folder.first()
            folder.name = folder_name
            folder.save()

        for project_user in project_users_data:
            try:
                user = get_or_create_user(project_user["email"])
            except Exception as e:
                raise serializers.ValidationError(str(e))

            project_membership = ProjectMembership.objects.create(
                project=project,
                member=user,
                is_project_admin=project_user["is_project_admin"],
                is_metadata_template_admin=project_user["is_project_admin"]
                or project_user["is_project_metadata_template_admin"],
                can_create_folders=project_user["can_create_folders"],
            )

            if project_user["can_view_folder"]:
                try:
                    FolderPermission.objects.create(
                        folder=project.folder.first(),
                        project_membership=project_membership,
                        is_folder_admin=project_user["is_folder_admin"],
                        is_metadata_template_admin=project_user["is_folder_admin"]
                        or project_user["is_project_metadata_template_admin"]
                        or project_user["is_folder_metadata_template_admin"],
                        can_edit=project_user["can_edit_folder"],
                    )
                except ValidationError:
                    pass

        if metadata is not None:
            set_metadata_for_relation(
                metadata_list=metadata,
                relation=project,
            )

        # If the metadata template payload is of type dict then it has just been created and must also be linked
        # to the appropriate content type.
        if isinstance(metadata_template_payload, dict):
            metadata_template.metadata_template_container.assigned_to_content_type = project.get_content_type()
            metadata_template.metadata_template_container.assigned_to_object_id = project.pk
            metadata_template.metadata_template_container.save()

        return project


class ProjectUpdatePayloadSerializer(serializers.Serializer):
    name = serializers.CharField(
        required=True,
        allow_null=False,
    )

    description = serializers.JSONField(
        allow_null=False,
        default=dict,
    )

    metadata_template = MetadataTemplateGetOrCreateSerializer(
        required=False,
        allow_null=True,
    )

    metadata = MetadataPayloadSerializer(
        required=False,
    )


class ProjectUpdateSerializer(BaseModelWithByUserSerializer):
    metadata_template = MetadataTemplateGetOrCreateSerializer(
        required=False,
        allow_null=True,
    )

    metadata = MinimalMetadataSerializer(
        many=True,
        required=False,
    )

    class Meta:
        model = Project
        fields = [
            "name",
            "description",
            "metadata_template",
            "metadata",
        ]

    def update(self, instance, validated_data):
        metadata_template = validated_data.get("metadata_template", None)
        metadata_template_payload = self.context["request"].data.get("metadata_template", None)
        metadata = None

        if "metadata" in validated_data:
            metadata = self.context["request"].data.get("metadata", [])
            validated_data.pop("metadata", [])

        for key, value in validated_data.items():
            setattr(instance, key, value)

        instance.save()

        if metadata is not None:
            set_metadata_for_relation(
                metadata_list=metadata,
                relation=instance,
            )

        # If the metadata template payload is of type dict then it has just been created and must also be linked
        # to the appropriate content type.
        if isinstance(metadata_template_payload, dict):
            metadata_template.metadata_template_container.assigned_to_content_type = instance.get_content_type()
            metadata_template.metadata_template_container.assigned_to_object_id = instance.pk
            metadata_template.metadata_template_container.save()

        return instance

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return super().update(request, *args, **kwargs)


class ProjectDashboardSummarySerializerMixin(serializers.Serializer):
    my_membership = serializers.SerializerMethodField()

    folders_count = serializers.SerializerMethodField()

    datasets_count = serializers.SerializerMethodField()

    def _get_current_user(self):
        request = self.context.get("request")
        request_user = getattr(request, "user", None)

        if request_user and request_user.is_authenticated:
            return request_user

        current_user = get_current_user()

        if current_user and current_user.is_authenticated:
            return current_user

        return None

    def _get_visible_folders(self, project):
        user = self._get_current_user()
        queryset = project.folder.all()

        if not user:
            return queryset.none()

        return queryset.filter(
            folderpermission__project_membership__member=user.pk,
        )

    @extend_schema_field(
        {
            "type": "string",
            "enum": [
                "admin",
                "member",
            ],
        },
    )
    def get_my_membership(self, project):
        user = self._get_current_user()

        if not user:
            return None

        membership = project.project_members.filter(
            member=user,
        ).first()

        if not membership:
            return None

        if membership.is_project_admin:
            return "admin"

        return "member"

    @extend_schema_field(serializers.IntegerField())
    def get_folders_count(self, project):
        return self._get_visible_folders(project).count()

    @extend_schema_field(serializers.IntegerField())
    def get_datasets_count(self, project):
        return (
            self._get_visible_folders(project).aggregate(
                datasets_count=Sum("datasets_count"),
            )["datasets_count"]
            or 0
        )


class ProjectListSerializer(ProjectDashboardSummarySerializerMixin, BaseModelWithByUserSerializer):
    metadata_template = MinimalMetadataTemplateSerializer(
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Project

        fields = [
            "pk",
            "name",
            "metadata_template",
            "is_deletable",
            "members_count",
            "folders_count",
            "datasets_count",
            "metadata_templates_count",
            "my_membership",
        ]

        read_only_fields = [
            "is_deletable",
            "members_count",
            "folders_count",
            "datasets_count",
            "metadata_templates_count",
            "my_membership",
        ]


class ProjectSerializer(ProjectDashboardSummarySerializerMixin, BaseModelWithByUserSerializer):
    metadata_template = MetadataTemplateSerializer(
        required=False,
        allow_null=True,
    )

    metadata = MinimalMetadataSerializer(
        many=True,
        required=False,
    )

    folders = serializers.ListSerializer(
        child=MinimalFolderSerializer(),
        read_only=True,
        required=False,
    )

    class Meta:
        model = Project

        fields = [
            "pk",
            "name",
            "description",
            "metadata_template",
            "is_deletable",
            "metadata",
            "folders",
            "members_count",
            "folders_count",
            "datasets_count",
            "metadata_templates_count",
            "my_membership",
        ]

        read_only_fields = [
            "is_deletable",
            "members_count",
            "folders_count",
            "datasets_count",
            "metadata_templates_count",
            "my_membership",
        ]


class MinimalProjectSerializer(BaseModelSerializer):
    class Meta:
        model = Project

        fields = [
            "pk",
            "name",
        ]


class ProjectSearchSerializer(BaseModelWithByUserSerializer):
    class Meta:
        model = Project
        fields = [
            "pk",
            "name",
        ]


class ProjectMembershipCreateSerializer(BaseModelSerializer):
    member = GetOrCreateUserSerializer()

    class Meta:
        model = ProjectMembership
        fields = [
            "pk",
            "project",
            "member",
            "is_project_admin",
            "can_create_folders",
            "is_metadata_template_admin",
        ]

    def create(self, validated_data):
        """Create a new project membership instance."""
        member_data = validated_data.pop("member")
        project_membership = ProjectMembership.objects.create(member=member_data, **validated_data)
        return project_membership


class ProjectMembershipUpdateSerializer(BaseModelSerializer):
    class Meta:
        model = ProjectMembership
        fields = [
            "pk",
            "is_project_admin",
            "can_create_folders",
            "is_metadata_template_admin",
        ]

    def update(self, instance, validated_data):
        return super().update(instance, validated_data)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return super().update(request, *args, **kwargs)


class ProjectMembershipSerializer(BaseModelSerializer):
    member = MinimalUserSerializer(
        read_only=True,
    )

    class Meta:
        model = ProjectMembership
        fields = [
            "pk",
            "project",
            "member",
            "is_project_admin",
            "can_create_folders",
            "is_metadata_template_admin",
        ]
