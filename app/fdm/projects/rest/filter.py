from uuid import UUID

from django.db import models
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework
from django_userforeignkey.request import get_current_user
from rest_framework.exceptions import ValidationError

from fdm.projects.models import Project, ProjectMembership

__all__ = [
    "ProjectCreatorFilter",
    "ProjectMembershipFilter",
]

MAX_PROJECT_PK_FILTER_VALUES = 50


class ProjectCreatorFilter(rest_framework.FilterSet):
    class Creator(models.TextChoices):
        ME = "me", _("Me")
        OTHERS = "others", _("Others")

    class MembershipStatus(models.TextChoices):
        ADMIN = "admin", _("Admin")
        MEMBER = "member", _("Member")

    created_by = rest_framework.ChoiceFilter(
        label=_("Filter by creator"),
        method="filter_by_creator",
        choices=Creator.choices,
    )

    pk = rest_framework.Filter(
        label=_("Filter by project IDs"),
        method="filter_by_pk",
    )

    membership = rest_framework.ChoiceFilter(
        label=_("Filter by membership status"),
        method="filter_by_membership_status",
        choices=MembershipStatus.choices,
    )

    class Meta:
        model = Project
        fields = [
            "pk",
            "created_by",
            "membership",
            "is_deletable",
        ]

    def filter_by_pk(self, queryset, name, value):
        values = self.request.query_params.getlist(name)

        if len(values) > MAX_PROJECT_PK_FILTER_VALUES:
            raise ValidationError(
                {
                    name: _(
                        "Ensure this list has no more than %(limit)s values.",
                    )
                    % {
                        "limit": MAX_PROJECT_PK_FILTER_VALUES,
                    },
                },
            )

        project_ids = []

        for raw_value in values:
            try:
                project_ids.append(str(UUID(raw_value)))
            except (TypeError, ValueError):
                raise ValidationError(
                    {
                        name: _("Enter a valid UUID."),
                    },
                )

        return queryset.filter(pk__in=list(dict.fromkeys(project_ids)))

    def filter_by_creator(self, queryset, name, value):
        user = get_current_user()

        if not user.is_authenticated:
            return queryset

        if value == self.Creator.ME:
            return queryset.filter(created_by=user)
        elif value == self.Creator.OTHERS:
            return queryset.exclude(created_by=user)

        return queryset

    def filter_by_membership_status(self, queryset, name, value):
        user = get_current_user()

        if not user.is_authenticated:
            return queryset

        if value == self.MembershipStatus.ADMIN:
            return queryset.filter(
                project_members__member=user,
                project_members__is_project_admin=True,
            )
        elif value == self.MembershipStatus.MEMBER:
            return queryset.filter(
                project_members__member=user,
                project_members__is_project_admin=False,
            )

        return queryset


class ProjectMembershipFilter(rest_framework.FilterSet):
    class Member(models.TextChoices):
        ME = "@me", _("Me")

    member = rest_framework.Filter(
        label=_("Filter by member"),
        method="filter_by_member",
    )

    class Meta:
        model = ProjectMembership
        fields = [
            "project",
            "member",
            "member__email",
        ]

    def filter_by_member(self, queryset, name, value):
        user = get_current_user()

        if not user.is_authenticated:
            return queryset

        if value == self.Member.ME:
            return queryset.filter(member=user)
        elif value:
            try:
                value = int(value)
                return queryset.filter(member=value)
            except ValueError:
                return queryset.none()

        return queryset
