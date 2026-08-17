from django import forms
from django.contrib import admin

from fdm.cms.models.models import Content
from fdm.core.widgets import EasyMDEWidget


class ContentForm(forms.ModelForm):
    class Meta:
        model = Content
        fields = "__all__"
        widgets = {
            "text_de": EasyMDEWidget,
            "text_en": EasyMDEWidget,
        }


@admin.register(Content)
class ContentAdmin(admin.ModelAdmin):
    form = ContentForm
    actions = []  # Removes the delete action
    add_permissions = []  # Removes the ability to add new entries
    delete_permissions = []  # Removes the ability to delete entries

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    class Media:
        css = {
            "screen": [
                "cms/admin/custom.css",
            ],
        }

    def get_readonly_fields(self, request, obj=None):
        # Make fields readonly for existing entries only
        if obj:
            return list(self.readonly_fields) + [
                "slug",
                "name",
            ]

        return self.readonly_fields

    def get_prepopulated_fields(self, request, obj=None):
        # Prepopulation works for new objects only
        if not obj:
            return {
                "slug": [
                    "question",
                ],
            }

        return {}

    fields = [
        "name",
        "slug",
        "published",
        "text_de",
        "text_en",
    ]

    list_display = [
        "name",
        "slug",
        "published",
        "last_modified_by",
        "last_modification_date",
    ]

    search_fields = [
        "name",
        "text_de",
        "text_en",
        "slug",
    ]
