from django.contrib.admin import SimpleListFilter
from django.db.models import Subquery
from django.utils.translation import gettext_lazy as _

from fdm.projects.models import ProjectMembership

__all__ = [
    "HasMemberWithCanCreateProjectsPermissionFilter",
]


class HasMemberWithCanCreateProjectsPermissionFilter(SimpleListFilter):
    title = "has member with 'can create projects' permission"

    parameter_name = "has_member_with_can_create_projects_permission"

    def lookups(self, request, model_admin):
        return (
            ("1", _("Yes")),
            ("0", _("No")),
        )

    def queryset(self, request, queryset):
        if self.value() == "1":
            return queryset.filter(
                pk__in=Subquery(
                    ProjectMembership.objects.filter(
                        member__can_create_projects=True,
                    ).values_list(
                        "project__pk",
                        flat=True,
                    ),
                ),
            )
        elif self.value() == "0":
            return queryset.exclude(
                pk__in=Subquery(
                    ProjectMembership.objects.filter(
                        member__can_create_projects=True,
                    ).values_list(
                        "project__pk",
                        flat=True,
                    ),
                ),
            )

        return queryset
