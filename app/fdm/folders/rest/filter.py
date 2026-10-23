from django.db import models
from django.db.models import Subquery
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework
from django_userforeignkey.request import get_current_user

from fdm.folders.models import Folder, FolderPermission, Subfolder

__all__ = [
    "FolderFilter",
    "FolderPermissionFilter",
    "SubfolderFilter",
]


class FolderFilter(rest_framework.FilterSet):
    folder_permission__can_edit = rest_framework.BooleanFilter(
        label=_("Filter by 'can edit' permission"),
        method="filter_by_can_edit_permission",
    )

    class Meta:
        model = Folder
        fields = [
            "project",
            "metadata_template",
            "folder_permission__can_edit",
        ]

    def filter_by_can_edit_permission(self, queryset, name, value):
        user = get_current_user()

        if not user.is_authenticated:
            return queryset

        if value is True:
            return queryset.filter(
                pk__in=Subquery(
                    FolderPermission.objects.filter(
                        project_membership__member=user,
                        can_edit=True,
                    ).values_list(
                        "folder__pk",
                        flat=True,
                    ),
                ),
            )
        elif value is False:
            return queryset.filter(
                pk__in=Subquery(
                    FolderPermission.objects.filter(
                        project_membership__member=user,
                        can_edit=False,
                    ).values_list(
                        "folder__pk",
                        flat=True,
                    ),
                ),
            )

        return queryset


class FolderPermissionFilter(rest_framework.FilterSet):
    class Member(models.TextChoices):
        ME = "@me", _("Me")

    project_membership__member = rest_framework.Filter(
        label=_("Filter by member"),
        method="filter_by_project_membership_member",
    )

    class Meta:
        model = FolderPermission
        fields = [
            "folder",
            "project_membership__member",
            "project_membership__member__email",
        ]

    def filter_by_project_membership_member(self, queryset, name, value):
        user = get_current_user()

        if not user.is_authenticated:
            return queryset

        if value == self.Member.ME:
            return queryset.filter(project_membership__member=user)
        elif value:
            try:
                value = int(value)
                return queryset.filter(project_membership__member=value)
            except ValueError:
                return queryset.none()

        return queryset


class SubfolderFilter(rest_framework.FilterSet):
    parent__isnull = rest_framework.BooleanFilter(
        label=_("Filter by subfolders on the top level of the folder"),
        field_name="parent",
        lookup_expr="isnull",
    )

    class Meta:
        model = Subfolder
        fields = [
            "folder",
            "parent",
            "parent__isnull",
        ]
