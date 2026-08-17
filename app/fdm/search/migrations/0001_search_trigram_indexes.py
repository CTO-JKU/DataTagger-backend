from django.db import migrations


def create_search_indexes(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    statements = [
        "CREATE EXTENSION IF NOT EXISTS pg_trgm",
        ("CREATE INDEX IF NOT EXISTS search_project_name_trgm_idx ON projects_project USING gin (name gin_trgm_ops)"),
        (
            "CREATE INDEX IF NOT EXISTS search_project_description_trgm_idx "
            "ON projects_project USING gin (((description)::text) gin_trgm_ops)"
        ),
        ("CREATE INDEX IF NOT EXISTS search_folder_name_trgm_idx ON folders_folder USING gin (name gin_trgm_ops)"),
        (
            "CREATE INDEX IF NOT EXISTS search_folder_description_trgm_idx "
            "ON folders_folder USING gin (((description)::text) gin_trgm_ops)"
        ),
        (
            "CREATE INDEX IF NOT EXISTS search_dataset_name_trgm_idx "
            "ON uploads_uploadsdataset USING gin (name gin_trgm_ops)"
        ),
        (
            "CREATE INDEX IF NOT EXISTS search_dataset_display_name_trgm_idx "
            "ON uploads_uploadsdataset USING gin (display_name gin_trgm_ops)"
        ),
        (
            "CREATE INDEX IF NOT EXISTS search_version_name_trgm_idx "
            "ON uploads_uploadsversion USING gin (name gin_trgm_ops)"
        ),
        (
            "CREATE INDEX IF NOT EXISTS search_file_uploaded_path_trgm_idx "
            "ON uploads_uploadsversionfile USING gin (((uploaded_file)::text) gin_trgm_ops)"
        ),
        (
            "CREATE INDEX IF NOT EXISTS search_metadata_custom_key_trgm_idx "
            "ON metadata_metadata USING gin (custom_key gin_trgm_ops)"
        ),
        (
            "CREATE INDEX IF NOT EXISTS search_metadata_value_text_trgm_idx "
            "ON metadata_metadata USING gin (((value ->> 'value')) gin_trgm_ops)"
        ),
    ]

    with schema_editor.connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)


def drop_search_indexes(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    statements = [
        "DROP INDEX IF EXISTS search_metadata_value_text_trgm_idx",
        "DROP INDEX IF EXISTS search_metadata_custom_key_trgm_idx",
        "DROP INDEX IF EXISTS search_file_uploaded_path_trgm_idx",
        "DROP INDEX IF EXISTS search_version_name_trgm_idx",
        "DROP INDEX IF EXISTS search_dataset_display_name_trgm_idx",
        "DROP INDEX IF EXISTS search_dataset_name_trgm_idx",
        "DROP INDEX IF EXISTS search_folder_description_trgm_idx",
        "DROP INDEX IF EXISTS search_folder_name_trgm_idx",
        "DROP INDEX IF EXISTS search_project_description_trgm_idx",
        "DROP INDEX IF EXISTS search_project_name_trgm_idx",
    ]

    with schema_editor.connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("folders", "0027_migrate_folder_storage_size"),
        ("metadata", "0047_alter_metadata_field_type_and_more"),
        ("projects", "0016_migrate_project_metadata_templates_count"),
        ("uploads", "0041_uploadsdataset_deletable_until_date"),
    ]

    operations = [
        migrations.RunPython(
            create_search_indexes,
            reverse_code=drop_search_indexes,
        ),
    ]
