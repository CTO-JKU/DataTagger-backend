from django.contrib import admin

from fdm.file_parser.models import FileParser, FileParserConfiguration


@admin.register(FileParserConfiguration)
class FileParserConfigurationAdmin(admin.ModelAdmin):
    list_display = [
        "parser_type",
        "assigned_to_content_type",
        "assigned_to_object_id",
        "output_metadata_key",
        "enabled",
        "creation_date",
        "last_modification_date",
    ]
    list_filter = [
        "parser_type",
        "enabled",
        "assigned_to_content_type",
    ]
    search_fields = [
        "output_metadata_key",
        "assigned_to_object_id",
    ]


@admin.register(FileParser)
class FileParserAdmin(admin.ModelAdmin):
    list_display = [
        "parser_type",
        "file",
        "configuration",
        "status",
        "creation_date",
        "last_modification_date",
    ]
    list_filter = [
        "parser_type",
        "status",
        "configuration__enabled",
    ]
    search_fields = [
        "file__uploaded_file",
        "configuration__output_metadata_key",
    ]
