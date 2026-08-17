from __future__ import annotations

import uuid

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from fdm.core.helpers import clean_assigned_content_type
from fdm.core.models import BaseModel, ByUserMixin, LockMixin, OrderMixin, TimestampMixin
from fdm.metadata.enums import MetadataFieldType

__all__ = [
    "MetadataField",
    "Metadata",
    "MetadataTag",
    "MetadataTemplateContainer",
    "MetadataTemplate",
    "MetadataTemplateField",
]


class MetadataField(BaseModel, TimestampMixin, ByUserMixin):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    key = models.CharField(
        max_length=128,
        unique=True,
    )

    field_type = models.CharField(
        max_length=16,
        choices=MetadataFieldType.choices,
        default=MetadataFieldType.TEXT,
    )

    read_only = models.BooleanField(
        default=False,
    )

    def __str__(self):
        return f"{self.key} ({self.field_type})"


class Metadata(BaseModel, TimestampMixin, ByUserMixin, OrderMixin):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    field = models.ForeignKey(
        MetadataField,
        blank=True,
        null=True,
        related_name="metadata",
        on_delete=models.CASCADE,
    )

    custom_key = models.CharField(
        max_length=128,
        blank=True,
        null=True,
    )

    field_type = models.CharField(
        max_length=16,
        choices=MetadataFieldType.choices,
        default=MetadataFieldType.TEXT,
    )

    read_only = models.BooleanField(
        default=False,
    )

    value = models.JSONField(
        blank=True,
        null=False,
        default=dict,
    )

    config = models.JSONField(
        blank=True,
        null=False,
        default=dict,
    )

    metadata_template_field = models.ForeignKey(
        "MetadataTemplateField",
        blank=True,
        null=True,
        related_name="metadata",
        on_delete=models.SET_NULL,
    )

    previous_version_field = models.ForeignKey(
        "Metadata",
        blank=True,
        null=True,
        related_name="metadata",
        on_delete=models.SET_NULL,
    )

    assigned_to_content_type = models.ForeignKey(
        ContentType,
        blank=True,
        null=True,
        on_delete=models.CASCADE,
    )

    assigned_to_object_id = models.UUIDField(
        blank=True,
        null=True,
    )

    assigned_to_content_object = GenericForeignKey(
        ct_field="assigned_to_content_type",
        fk_field="assigned_to_object_id",
    )

    class Meta:
        ordering = [
            "order",
            "custom_key",
        ]

        indexes = [
            models.Index(
                fields=[
                    "assigned_to_content_type",
                    "assigned_to_object_id",
                ],
            ),
        ]

    def clean(self):
        if self.field and self.custom_key:
            raise ValidationError(_("You must not link a metadata field and declare a custom key together."))

        if not self.field and not self.custom_key:
            raise ValidationError(_("You must either link a metadata field or declare a custom key."))

        if self.get_value() is not None:
            self.is_valid(raise_exception=True)

        from fdm.folders.models import Folder
        from fdm.projects.models import Project
        from fdm.uploads.models import UploadsVersion, UploadsVersionFile

        clean_assigned_content_type(
            assigned_to_content_type=self.assigned_to_content_type,
            assigned_to_object_id=self.assigned_to_object_id,
            allowed_content_models=[
                Project,
                Folder,
                UploadsVersion,
                UploadsVersionFile,
            ],
        )

    def save(self, *args, **kwargs):
        if self.field and self.field.read_only:
            self.read_only = True

        from fdm.metadata.choice_values import canonicalize_choice_field
        from fdm.metadata.helpers import get_metadata_structure_for_type

        canonical_value, self.config = canonicalize_choice_field(
            field_type=self.field_type,
            value=self.get_value(),
            config=self.config,
        )
        self.value = get_metadata_structure_for_type(self.field_type, canonical_value)

        super().save(*args, **kwargs)

    def is_valid(self, raise_exception=False):
        from fdm.metadata.helpers import validate_metadata_value

        try:
            return validate_metadata_value(
                field=self.field,
                field_type=self.field_type,
                custom_key=self.custom_key,
                value=self.get_value(),
                config=self.config,
                raise_exception=raise_exception,
            )
        except ValidationError as e:
            if raise_exception:
                raise ValidationError(
                    [m.format(key=self.field.key if self.field else self.custom_key) for m in e],
                )

    def set_value(self, value):
        from fdm.metadata.helpers import get_metadata_structure_for_type

        self.value = get_metadata_structure_for_type(self.field_type, value)
        self.save()

    def get_value(self):
        from fdm.metadata.helpers import get_metadata_value_for_type

        return get_metadata_value_for_type(self.field_type, self.value)

    def get_converted_value(self, to_field_type: str):
        from fdm.metadata.helpers import convert_metadata_value, get_metadata_structure_for_type

        return get_metadata_structure_for_type(
            to_field_type,
            convert_metadata_value(
                self.get_value(),
                self.metadata_template_field.field_type if self.metadata_template_field else self.field_type,
                to_field_type,
            ),
        )

    def __str__(self):
        return f"{self.assigned_to_content_type} ({self.assigned_to_object_id}): {self.custom_key or self.field.key}"

    def get_past_version_fields(self) -> list[Metadata]:
        previous_version_field = self.previous_version_field
        fields = []

        while previous_version_field:
            fields.append(previous_version_field)
            previous_version_field = previous_version_field.previous_version_field

        return fields

    def get_future_version_fields(self) -> list[Metadata]:
        previous_version_field = self
        fields = []

        while True:
            try:
                future_version_field = Metadata.objects.get(previous_version_field=previous_version_field)
                fields.append(future_version_field)
                previous_version_field = future_version_field
                continue
            except Metadata.DoesNotExist:
                break

        return fields

    def is_version_of_past_field(self, field: Metadata | str) -> bool:
        field_pk = str(field.pk) if isinstance(field, Metadata) else field
        past_fields = self.get_past_version_fields()

        if field_pk in [str(past_field.pk) for past_field in past_fields]:
            return True

        return False

    def is_version_of_future_field(self, field: Metadata | str) -> bool:
        field_pk = str(field.pk) if isinstance(field, Metadata) else field
        past_fields = self.get_future_version_fields()

        if field_pk in [str(past_field.pk) for past_field in past_fields]:
            return True

        return False

    def is_related_field(self, field: Metadata | str) -> bool:
        field_pk = str(field.pk) if isinstance(field, Metadata) else field

        if self.is_version_of_past_field(field_pk) or self.is_version_of_future_field(field_pk):
            return True

        return False


class MetadataTag(BaseModel):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    folder = models.ForeignKey(
        "folders.Folder",
        on_delete=models.CASCADE,
        related_name="metadata_tags",
    )

    value = models.CharField(
        max_length=512,
    )

    class Meta:
        ordering = ["value"]
        constraints = [
            models.UniqueConstraint(
                fields=["folder", "value"],
                name="unique_tag_per_folder",
            ),
        ]

    def __str__(self):
        return f"{self.value} (Folder: {self.folder})"

    def save(self, *args, **kwargs):
        from fdm.metadata.choice_values import canonicalize_choice_literal

        self.value = canonicalize_choice_literal(self.value)
        super().save(*args, **kwargs)


class MetadataTemplateContainer(BaseModel, TimestampMixin, ByUserMixin, LockMixin):
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

    assigned_to_content_type = models.ForeignKey(
        ContentType,
        blank=True,
        null=True,
        on_delete=models.CASCADE,
    )

    assigned_to_object_id = models.UUIDField(
        blank=True,
        null=True,
    )

    assigned_to_content_object = GenericForeignKey(
        ct_field="assigned_to_content_type",
        fk_field="assigned_to_object_id",
    )

    projects = models.ManyToManyField(
        "projects.project",
        blank=True,
        related_name="metadata_template_container",
    )

    folder = models.ManyToManyField(
        "folders.folder",
        blank=True,
        related_name="metadata_template_container",
    )

    class Meta:
        indexes = [
            models.Index(
                fields=[
                    "assigned_to_content_type",
                    "assigned_to_object_id",
                ],
            ),
        ]

    @property
    def get_sorted_metadata_templates(self):
        return self.metadata_templates.order_by("-creation_date")

    @property
    def latest_metadata_template(self):
        return self.get_sorted_metadata_templates.first()

    def __str__(self):
        return self.name

    def restore_version(self, metadata_template: any = None):
        if not metadata_template:
            raise ValidationError(_("You can't restore a specific metadata template without providing a primary key."))

        if self.locked and not self.is_locked_by_myself():
            raise PermissionDenied(_("You must not edit an element which has been locked by another user."))

        if self.metadata_templates.count() < 2:
            raise PermissionDenied(
                _(
                    "A metadata template container must have at least two metadata templates before you can restore a specific metadata template.",
                ),
            )

        if isinstance(metadata_template, str):
            try:
                metadata_template = MetadataTemplate.objects.get(
                    pk=metadata_template,
                )
            except MetadataTemplate.DoesNotExist:
                raise ValidationError(_("The metadata template you want to restore does not exist."))

        if self.latest_metadata_template == metadata_template:
            raise PermissionDenied(
                _("You can't restore the latest version of a metadata template container."),
            )

        if metadata_template.metadata_template_container != self:
            raise PermissionDenied(
                _("The metadata template you want to restore is not part of the metadata template container."),
            )

        new_metadata_template = MetadataTemplate.objects.create(
            metadata_template_container=self,
        )

        for metadata_template_field in metadata_template.metadata_template_fields.all():
            MetadataTemplateField.objects.create(
                metadata_template=new_metadata_template,
                field=metadata_template_field.field,
                custom_key=metadata_template_field.custom_key,
                field_type=metadata_template_field.field_type,
                config=metadata_template_field.config,
                mandatory=metadata_template_field.mandatory,
                order=metadata_template_field.order,
                previous_version_field=metadata_template_field.previous_version_field,
                value=metadata_template_field.value,
            )

        return new_metadata_template

    def delete(self, *args, **kwargs):
        for metadata_template in self.metadata_templates.all():
            metadata_template.delete()

        super().delete(*args, **kwargs)

    def clean(self):
        from fdm.folders.models import Folder
        from fdm.projects.models import Project
        from fdm.uploads.models import UploadsDataset

        clean_assigned_content_type(
            assigned_to_content_type=self.assigned_to_content_type,
            assigned_to_object_id=self.assigned_to_object_id,
            allowed_content_models=[
                Project,
                Folder,
                UploadsDataset,
            ],
        )


class MetadataTemplate(BaseModel, TimestampMixin, ByUserMixin):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    name = models.CharField(
        max_length=255,
        blank=True,
        null=True,
    )

    metadata_template_container = models.ForeignKey(
        MetadataTemplateContainer,
        blank=True,
        null=True,
        related_name="metadata_templates",
        on_delete=models.SET_NULL,
    )

    @property
    def locked(self):
        return self.metadata_template_container.locked

    @property
    def locked_by(self):
        return self.metadata_template_container.locked_by

    @property
    def locked_at(self):
        return self.metadata_template_container.locked_at

    @property
    def assigned_to_content_type(self):
        return self.metadata_template_container.assigned_to_content_type

    @property
    def assigned_to_object_id(self):
        return self.metadata_template_container.assigned_to_object_id

    @property
    def assigned_to_content_object(self):
        return self.metadata_template_container.assigned_to_content_object

    def __str__(self):
        return self.name or str(self.creation_date)

    def clean(self):
        from fdm.folders.models import Folder
        from fdm.projects.models import Project
        from fdm.uploads.models import UploadsDataset

        clean_assigned_content_type(
            assigned_to_content_type=self.metadata_template_container.assigned_to_content_type,
            assigned_to_object_id=self.metadata_template_container.assigned_to_object_id,
            allowed_content_models=[
                Project,
                Folder,
                UploadsDataset,
            ],
        )


class MetadataTemplateField(BaseModel, OrderMixin):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    metadata_template = models.ForeignKey(
        MetadataTemplate,
        blank=False,
        null=False,
        related_name="metadata_template_fields",
        on_delete=models.CASCADE,
    )

    field = models.ForeignKey(
        MetadataField,
        blank=True,
        null=True,
        related_name="metadata_template_fields",
        on_delete=models.CASCADE,
    )

    custom_key = models.CharField(
        max_length=128,
        blank=True,
        null=True,
    )

    field_type = models.CharField(
        max_length=16,
        choices=MetadataFieldType.choices,
        default=MetadataFieldType.TEXT,
    )

    value = models.JSONField(
        blank=True,
        null=False,
        default=dict,
    )

    config = models.JSONField(
        blank=True,
        null=False,
        default=dict,
    )

    mandatory = models.BooleanField(
        default=False,
    )

    previous_version_field = models.ForeignKey(
        "MetadataTemplateField",
        blank=True,
        null=True,
        related_name="metadata_template_field",
        on_delete=models.SET_NULL,
    )

    class Meta:
        ordering = [
            "order",
            "custom_key",
        ]

    def clean(self):
        if self.field and self.custom_key:
            raise ValidationError(_("You must not link a metadata field and declare a custom key together."))

        if not self.field and not self.custom_key:
            raise ValidationError(_("You must either link a metadata field or declare a custom key."))

        if self.get_value() is not None:
            from fdm.metadata.helpers import validate_metadata_value

            validate_metadata_value(
                field=self.field,
                field_type=self.field_type,
                custom_key=self.custom_key,
                value=self.get_value(),
                config=self.config,
            )

        if (
            self.previous_version_field
            and self.previous_version_field.metadata_template.metadata_template_container
            != self.metadata_template.metadata_template_container
        ):
            raise ValidationError(
                _("Previous version field must be part of the same metadata template container."),
            )

    def save(self, *args, **kwargs):
        from fdm.metadata.choice_values import canonicalize_choice_field
        from fdm.metadata.helpers import get_metadata_structure_for_type

        canonical_value, self.config = canonicalize_choice_field(
            field_type=self.field_type,
            value=self.get_value(),
            config=self.config,
        )
        self.value = get_metadata_structure_for_type(self.field_type, canonical_value)
        super().save(*args, **kwargs)

    def set_value(self, value):
        from fdm.metadata.helpers import get_metadata_structure_for_type

        self.value = get_metadata_structure_for_type(self.field_type, value)
        self.save()

    def get_value(self):
        from fdm.metadata.helpers import get_metadata_value_for_type

        return get_metadata_value_for_type(self.field_type, self.value)

    def get_past_version_fields(self, metadata_template=None) -> list[MetadataTemplateField]:
        previous_version_field = self.previous_version_field
        fields = []

        while previous_version_field:
            fields.append(previous_version_field)

            # Check if we should only retrieve fields until we meet a certain metadata template assignment
            if metadata_template and previous_version_field.metadata_template == metadata_template:
                break

            previous_version_field = previous_version_field.previous_version_field

        return fields

    def get_future_version_fields(self, metadata_template=None) -> list[MetadataTemplateField]:
        if metadata_template:
            metadata_template_pk = (
                metadata_template.pk if isinstance(metadata_template, MetadataTemplate) else metadata_template
            )
            if str(self.metadata_template_id) == str(metadata_template_pk):
                return []

            # Targeted resolution walks backward from the requested template because restored histories can branch.
            metadata_template_fields = (
                metadata_template.metadata_template_fields.all()
                if isinstance(metadata_template, MetadataTemplate)
                else MetadataTemplateField.objects.filter(metadata_template=metadata_template)
            )
            matching_paths = []
            for metadata_template_field in metadata_template_fields:
                past_version_fields = metadata_template_field.get_past_version_fields()
                past_version_field_pks = [str(field.pk) for field in past_version_fields]

                try:
                    source_index = past_version_field_pks.index(str(self.pk))
                except ValueError:
                    continue

                matching_paths.append(list(reversed(past_version_fields[:source_index])) + [metadata_template_field])

            if len(matching_paths) > 1:
                raise ValidationError(_("Ambiguous metadata template field version history found."))

            return matching_paths[0] if matching_paths else []

        previous_version_field = self
        fields = []

        while True:
            try:
                future_version_field = MetadataTemplateField.objects.get(previous_version_field=previous_version_field)
                fields.append(future_version_field)
                previous_version_field = future_version_field

                # Check if we should only retrieve fields until we meet a certain metadata template assignment
                if metadata_template and future_version_field.metadata_template == metadata_template:
                    break

                continue
            except MetadataTemplateField.DoesNotExist:
                break

        return fields

    def get_latest_version_field(self, metadata_template=None) -> MetadataTemplateField:
        future_version_fields = self.get_future_version_fields(metadata_template=metadata_template)
        return future_version_fields[-1] if future_version_fields else self

    def is_version_of_past_field(self, field: MetadataTemplateField | str) -> bool:
        field_pk = str(field.pk) if isinstance(field, MetadataTemplateField) else field
        past_fields = self.get_past_version_fields()

        if field_pk in [str(past_field.pk) for past_field in past_fields]:
            return True

        return False

    def is_version_of_future_field(self, field: MetadataTemplateField | str) -> bool:
        field_pk = str(field.pk) if isinstance(field, MetadataTemplateField) else field
        past_fields = self.get_future_version_fields()

        if field_pk in [str(past_field.pk) for past_field in past_fields]:
            return True

        return False

    def is_related_field(self, field: MetadataTemplateField | str) -> bool:
        field_pk = str(field.pk) if isinstance(field, MetadataTemplateField) else field

        if self.is_version_of_past_field(field_pk) or self.is_version_of_future_field(field_pk):
            return True

        return False

    def __str__(self):
        return f"{self.metadata_template}: {self.custom_key or self.field}"
