from django.contrib import admin

from fdm.projects.filters import HasMemberWithCanCreateProjectsPermissionFilter
from fdm.projects.models import Project, ProjectMembership

__all__ = [
    "ProjectAdmin",
    "ProjectMembershipAdmin",
]


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "created_by",
        "members_count",
        "folders_count",
        "metadata_templates_count",
        "is_deletable",
    ]

    list_filter = [
        "is_deletable",
        HasMemberWithCanCreateProjectsPermissionFilter,
        "created_by",
    ]

    search_fields = [
        "pk",
        "name",
    ]

    readonly_fields = [
        "members_count",
        "folders_count",
        "metadata_templates_count",
        "is_deletable",
    ]

    exclude = [
        "description",
    ]


@admin.register(ProjectMembership)
class ProjectMembershipAdmin(admin.ModelAdmin):
    list_display = [
        "member",
        "project",
        "is_project_admin",
        "can_create_folders",
    ]

    list_filter = [
        "project",
        "member",
        "is_project_admin",
        "can_create_folders",
    ]

    search_fields = [
        "pk",
        "project__pk",
        "project__name",
        "member__pk",
    ]
