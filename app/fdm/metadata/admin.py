from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from fdm.metadata.models import *

__all__ = [
    "MetadataFieldAdmin",
    "MetadataTagAdmin",
    "MetadataTemplateInlineAdmin",
    "MetadataTemplateFieldInlineAdmin",
    "MetadataTemplateAdmin",
    "MetadataTemplateFieldAdmin",
    "MetadataTemplateContainerAdmin",
]


@admin.register(MetadataField)
class MetadataFieldAdmin(admin.ModelAdmin):
    list_display = [
        "key",
        "field_type",
        "read_only",
    ]

    list_filter = [
        "field_type",
        "read_only",
    ]

    search_fields = [
        "pk",
        "key",
    ]

    def get_readonly_fields(self, request, obj=None):
        # Make fields readonly for existing entries only which are already in use
        if (
            obj
            and Metadata.objects.filter(field=obj).exists()
            or MetadataTemplateField.objects.filter(field=obj).exists()
        ):
            return list(self.readonly_fields) + [
                "field_type",
            ]

        return self.readonly_fields

    def has_delete_permission(self, request, obj=None):
        # Fields which are already in use must not be deleted
        if Metadata.objects.filter(field=obj).exists() or MetadataTemplateField.objects.filter(field=obj).exists():
            return False

        return True


class MetadataTemplateInlineAdmin(admin.TabularInline):
    model = MetadataTemplate

    can_delete = False

    show_change_link = True

    fields = [
        "pk",
        "name",
        "creation_date",
    ]

    readonly_fields = [
        "pk",
        "name",
        "creation_date",
    ]

    ordering = [
        "-creation_date",
    ]

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class MetadataTemplateFieldInlineAdmin(admin.TabularInline):
    model = MetadataTemplateField

    can_delete = False

    show_change_link = True

    fields = [
        "pk",
        "field",
        "custom_key",
        "field_type",
        "value",
        "mandatory",
        "previous_version_field",
        "order",
    ]

    readonly_fields = [
        "pk",
        "field",
        "custom_key",
        "field_type",
        "value",
        "mandatory",
        "previous_version_field",
        "order",
    ]

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MetadataTemplate)
class MetadataTemplateAdmin(admin.ModelAdmin):
    list_display = [
        "get_metadata_template_name",
        "get_metadata_template_container_name",
        "get_template_type",
        "created_by",
    ]

    list_filter = [
        "metadata_template_container__assigned_to_content_type",
        "created_by",
    ]

    search_fields = [
        "pk",
        "name",
        "metadata_template_container__name",
    ]

    readonly_fields = [
        "created_by",
        "metadata_template_container",
    ]

    inlines = [
        MetadataTemplateFieldInlineAdmin,
    ]

    def get_fields(self, request, obj=None):
        fields = list(super().get_fields(request, obj))
        if "created_by" in fields and "name" in fields:
            fields.remove("created_by")
            fields.insert(fields.index("name"), "created_by")
        return fields

    def get_metadata_template_name(self, obj):
        return str(obj)

    get_metadata_template_name.short_description = _("Name")

    def get_metadata_template_container_name(self, obj):
        return obj.metadata_template_container.name

    get_metadata_template_container_name.short_description = _("Metadata Template Container")

    def get_template_type(self, obj):
        if bool(obj.metadata_template_container.assigned_to_content_type) ^ bool(
            obj.metadata_template_container.assigned_to_object_id,
        ):
            return _("Invalid template type")
        elif (
            obj.metadata_template_container.assigned_to_content_type
            and obj.metadata_template_container.assigned_to_object_id
        ):
            model = obj.metadata_template_container.assigned_to_content_type.model_class()
            try:
                model_object = model.objects.get(pk=obj.metadata_template_container.assigned_to_object_id)
            except model.DoesNotExist:
                return _("Invalid template type") + ": " + _("An object type with this object id does not exist.")

            return f"{model.__name__}: {model_object}"
        else:
            return _("Global")

    get_template_type.short_description = _("Template type")

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MetadataTemplateField)
class MetadataTemplateFieldAdmin(admin.ModelAdmin):
    list_display = [
        "metadata_template",
        "field",
        "custom_key",
        "value",
        "mandatory",
        "previous_version_field",
        "order",
    ]

    list_filter = [
        "field",
        "mandatory",
    ]

    search_fields = [
        "pk",
        "field__key",
        "custom_key",
        "value",
        "metadata_template__name",
        "metadata_template__metadata_template_container__name",
    ]

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MetadataTemplateContainer)
class MetadataTemplateContainerAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "get_template_type",
        "created_by",
    ]

    list_filter = [
        "assigned_to_content_type",
        "created_by",
    ]

    search_fields = [
        "pk",
        "name",
    ]

    inlines = [
        MetadataTemplateInlineAdmin,
    ]

    readonly_fields = [
        "created_by",
    ]

    def get_fields(self, request, obj=None):
        fields = list(super().get_fields(request, obj))
        if "created_by" in fields and "locked_by" in fields:
            fields.remove("created_by")
            fields.insert(fields.index("locked_by"), "created_by")
        return fields

    def get_template_type(self, obj):
        if bool(obj.assigned_to_content_type) ^ bool(obj.assigned_to_object_id):
            return _("Invalid template type")
        elif obj.assigned_to_content_type and obj.assigned_to_object_id:
            model = obj.assigned_to_content_type.model_class()
            try:
                model_object = model.objects.get(pk=obj.assigned_to_object_id)
            except model.DoesNotExist:
                return _("Invalid template type") + ": " + _("An object type with this object id does not exist.")

            return f"{model.__name__}: {model_object}"
        else:
            return _("Global")

    get_template_type.short_description = _("Template type")

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MetadataTag)
class MetadataTagAdmin(admin.ModelAdmin):
    list_display = [
        "value",
        "folder",
        "get_project_name",
    ]

    list_filter = [
        "folder",
        "folder__project",
    ]

    search_fields = [
        "=pk",
        "value",
        "=folder__pk",
        "folder__name",
        "=folder__project__pk",
        "folder__project__name",
    ]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("folder__project")

    def get_project_name(self, obj):
        return obj.folder.project.name

    get_project_name.short_description = _("Project")
    get_project_name.admin_order_field = "folder__project__name"

    def has_add_permission(self, request):
        return False
