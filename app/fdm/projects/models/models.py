import uuid

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.fields import GenericRelation
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q, QuerySet
from django.utils.translation import gettext_lazy as _
from django_userforeignkey.request import get_current_user

from fdm.core.models import BaseModel, ByUserMixin, LockMixin, TimestampMixin
from fdm.metadata.models import Metadata, MetadataTemplate, MetadataTemplateContainer

User = get_user_model()

__all__ = [
    "Project",
    "ProjectMembership",
]


class Project(BaseModel, TimestampMixin, ByUserMixin, LockMixin):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    name = models.CharField(
        max_length=255,
        blank=False,
        null=False,
    )

    description = models.JSONField(
        blank=True,
        null=False,
        default=dict,
    )

    metadata_template = models.ForeignKey(
        MetadataTemplate,
        blank=True,
        null=True,
        related_name="project",
        on_delete=models.SET_NULL,
    )

    is_deletable = models.BooleanField(
        default=True,
    )

    members_count = models.IntegerField(
        blank=False,
        null=False,
        default=0,
    )

    metadata_templates_count = models.IntegerField(
        blank=False,
        null=False,
        default=0,
    )

    metadata = GenericRelation(
        Metadata,
        content_type_field="assigned_to_content_type",
        object_id_field="assigned_to_object_id",
    )

    @property
    def folders(self):
        user = get_current_user()

        if not user.is_anonymous:
            return self.folder.filter(
                folderpermission__project_membership__member=user.pk,
            )

        return self.folder

    @property
    def folders_count(self) -> int:
        return self.folders.count()

    def update_members_count(self) -> None:
        from fdm.websockets.consumers.handlers import send_model_changed_websocket_message

        new_count = ProjectMembership.objects.filter(
            project=self,
        ).count()

        # We must use .update() to avoid running into a lock
        Project.objects.filter(pk=self.pk).update(members_count=new_count)
        self.members_count = new_count

        send_model_changed_websocket_message(self)

    def update_metadata_templates_count(self) -> None:
        # We are currently intentionally ignoring global templates and counting all metadata template versions
        new_count = MetadataTemplate.objects.filter(
            # Metadata templates assigned to the project itself
            Q(
                metadata_template_container__assigned_to_content_type=self.get_content_type(),
                metadata_template_container__assigned_to_object_id=self.pk,
            ),
        ).count()

        # We must use .update() to avoid running into a lock
        Project.objects.filter(pk=self.pk).update(metadata_templates_count=new_count)
        self.metadata_templates_count = new_count

    def __str__(self):
        return self.name

    def clean(self):
        from fdm.folders.models import Folder
        from fdm.uploads.models import UploadsDataset

        if not isinstance(self.description, dict):
            raise ValidationError(_("Description contains invalid JSON data"))

        # Make sure that the metadata template container of the active metadata template is not already an active
        # metadata template container elsewhere in the hierarchy of project > folder > dataset.
        if self.metadata_template and (
            Folder.objects.filter(
                project=self,
                metadata_template__metadata_template_container=self.metadata_template.metadata_template_container,
            ).exists()
            or UploadsDataset.objects.filter(
                folder__project=self,
                metadata_template__metadata_template_container=self.metadata_template.metadata_template_container,
            ).exists()
        ):
            raise ValidationError(
                _("A metadata template can only be actively assigned to one instance in the hierarchy at a time"),
            )

    def get_available_metadata_template_container(self) -> QuerySet:
        return MetadataTemplateContainer.objects.filter(
            # Metadata template container assigned to the project itself
            Q(
                assigned_to_content_type=self.get_content_type(),
                assigned_to_object_id=self.pk,
            )
            # Globally available metadata template container
            | Q(
                assigned_to_content_type__isnull=True,
                assigned_to_object_id__isnull=True,
            ),
        ).order_by(
            "name",
            "creation_date",
        )

    def get_available_metadata_templates(self) -> QuerySet:
        return MetadataTemplate.objects.filter(
            # Metadata templates assigned to the project itself
            Q(
                metadata_template_container__assigned_to_content_type=self.get_content_type(),
                metadata_template_container__assigned_to_object_id=self.pk,
            )
            # Globally available metadata templates
            | Q(
                metadata_template_container__assigned_to_content_type__isnull=True,
                metadata_template_container__assigned_to_object_id__isnull=True,
            ),
        ).order_by(
            "metadata_template_container__name",
            "creation_date",
        )


class ProjectMembership(BaseModel):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    project = models.ForeignKey(
        Project,
        blank=False,
        null=False,
        related_name="project_members",
        on_delete=models.CASCADE,
    )

    member = models.ForeignKey(
        User,
        blank=False,
        null=False,
        related_name="project_members",
        on_delete=models.CASCADE,
    )

    is_project_admin = models.BooleanField(
        default=False,
    )

    can_create_folders = models.BooleanField(
        default=False,
    )

    is_metadata_template_admin = models.BooleanField(
        default=False,
    )

    def delete(self, using=None, keep_parents=False):
        from fdm.core.handlers import cascading_delete

        with cascading_delete():
            super().delete(using=using, keep_parents=keep_parents)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "member"],
                name="unique_project_member",
            ),
        ]

    def __str__(self):
        return f"{self.member.email} (Project: {self.project.name})"
