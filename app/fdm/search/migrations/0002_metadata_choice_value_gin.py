from django.db import migrations

INDEX_NAME = "search_metadata_choice_value_gin_idx"


def create_choice_value_index(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT indisvalid
            FROM pg_class
            JOIN pg_index ON pg_index.indexrelid = pg_class.oid
            WHERE pg_class.relname = %s
            """,
            [INDEX_NAME],
        )
        existing_index = cursor.fetchone()
        if existing_index and not existing_index[0]:
            cursor.execute(f"DROP INDEX CONCURRENTLY {INDEX_NAME}")
            existing_index = None

        if existing_index is None:
            cursor.execute(
                f"""
                CREATE INDEX CONCURRENTLY {INDEX_NAME}
                ON metadata_metadata
                USING GIN ((value -> 'value') jsonb_path_ops)
                WHERE field_type IN ('SELECTION', 'MULTI_SELECTION', 'TAG')
                """,
            )

        cursor.execute(
            """
            SELECT indisvalid
            FROM pg_class
            JOIN pg_index ON pg_index.indexrelid = pg_class.oid
            WHERE pg_class.relname = %s
            """,
            [INDEX_NAME],
        )
        if cursor.fetchone() != (True,):
            raise RuntimeError(f"Index {INDEX_NAME} was not created in a valid state.")

        cursor.execute("ANALYZE metadata_metadata")


def drop_choice_value_index(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("metadata", "0048_metadatatag"),
        ("search", "0001_search_trigram_indexes"),
    ]

    operations = [
        migrations.RunPython(
            create_choice_value_index,
            reverse_code=drop_choice_value_index,
        ),
    ]
