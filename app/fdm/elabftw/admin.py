from django.contrib import admin

from fdm.elabftw.models import ElabftwConnection, ExperimentLink

__all__ = [
    "ElabftwConnectionAdmin",
    "ExperimentLinkAdmin",
]


@admin.register(ElabftwConnection)
class ElabftwConnectionAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "url",
        "verify_tls",
    ]

    search_fields = [
        "user__email",
        "url",
    ]

    # Never show personal API keys in the admin
    exclude = [
        "api_key",
    ]


@admin.register(ExperimentLink)
class ExperimentLinkAdmin(admin.ModelAdmin):
    list_display = [
        "experiment_id",
        "title",
        "target_type",
        "target_id",
        "created_by",
    ]

    list_filter = [
        "target_type",
    ]

    search_fields = [
        "title",
        "target_id",
    ]
