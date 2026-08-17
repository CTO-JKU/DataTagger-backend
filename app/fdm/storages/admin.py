__all__ = [
    "DynamicStorageAdmin",
]

from django import forms
from django.conf import settings
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.http import HttpResponseRedirect
from django.utils.translation import gettext_lazy as _
from django_fernet.fernet import *

from fdm.core.helpers import check_empty_value
from fdm.storages.models import DynamicStorage


class DecryptedFernetFormField(forms.CharField):
    def __init__(self, secret=None, *args, **kwargs):
        self.secret = secret
        super().__init__(*args, **kwargs)

    def prepare_value(self, value):
        """Convert the database value for display in the form"""
        if not value:
            return None

        if isinstance(value, FernetTextFieldData):
            return value.decrypt(self.secret)
        return value

    def clean(self, value):
        """Convert the form value for storage in the database"""
        value = super().clean(value)

        if not isinstance(value, (str, type(None))):
            raise ValidationError(_("Not a valid string."))

        if check_empty_value(value) is None:
            return None

        fernet_data = FernetTextFieldData()
        fernet_data.encrypt(value, self.secret)
        return fernet_data


class DynamicStorageAdminForm(forms.ModelForm):
    local_private_dss_path = DecryptedFernetFormField(
        secret=settings.SECRET_KEY,
        required=False,
        label="Local Private DSS Path",
        help_text="Path to the local private DSS storage",
    )

    class Meta:
        model = DynamicStorage
        fields = [
            "name",
            "storage_type",
            "default",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.local_private_dss_path_encrypted:
            self.fields["local_private_dss_path"].initial = self.instance.local_private_dss_path_encrypted

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.local_private_dss_path_encrypted = self.cleaned_data.get("local_private_dss_path")
        if commit:
            instance.save()
        return instance


@admin.register(DynamicStorage)
class DynamicStorageAdmin(admin.ModelAdmin):
    form = DynamicStorageAdminForm

    list_display = [
        "name",
        "storage_type",
        "default",
        "approved",
        "mounted",
        "created_by",
        "creation_date",
    ]

    list_filter = [
        "storage_type",
        "default",
        "approved",
        "mounted",
        "created_by",
        "creation_date",
        "last_modification_date",
    ]

    search_fields = [
        "name",
        "description",
    ]

    exclude = [
        "description",
        "local_private_dss_path_encrypted",
    ]

    fields = [
        "name",
        "storage_type",
        "local_private_dss_path",
        "default",
        "created_by",
        "creation_date",
        "last_modified_by",
        "last_modification_date",
        "approved",
        "mounted",
    ]

    readonly_fields = [
        "created_by",
        "creation_date",
        "last_modified_by",
        "last_modification_date",
        "approved",
        "mounted",
        "default",
    ]

    ordering = ["-creation_date"]

    def response_change(self, request, obj):
        """
        Handles redirect back to Approval Queue changelist after approval.
        """
        if "_approve" in request.GET:
            ct_id = request.GET.get("ct_id")  # Get from URL

            # Construct the URL to ApprovalQueue changelist with content_type filter
            changelist_url = f"/admin/approval_queue/approvalqueue/?content_type__id__exact={ct_id}"

            return HttpResponseRedirect(changelist_url)

        return super().response_change(request, obj)
