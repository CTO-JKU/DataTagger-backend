import uuid

import django.db.models.deletion
import django_userforeignkey.models.fields
from django.conf import settings
from django.db import migrations, models

import fdm.exports.models.models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ExportJob",
            fields=[
                ("creation_date", models.DateTimeField(auto_now_add=True, verbose_name="Creation date")),
                ("last_modification_date", models.DateTimeField(auto_now=True, verbose_name="Last modification date")),
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("export_type", models.CharField(max_length=255)),
                ("object_ids", models.JSONField(default=list)),
                (
                    "metadata_format",
                    models.CharField(
                        choices=[("json", "JSON"), ("csv", "CSV"), ("xml", "XML")],
                        default="json",
                        max_length=16,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("SCHEDULED", "Scheduled"),
                            ("IN_PROGRESS", "In progress"),
                            ("ERROR", "Error"),
                            ("FINISHED", "Finished"),
                        ],
                        default="SCHEDULED",
                        max_length=16,
                    ),
                ),
                (
                    "zip_file",
                    models.FileField(
                        blank=True,
                        null=True,
                        upload_to=fdm.exports.models.models.get_export_upload_to_path,
                    ),
                ),
                ("download_token_hash", models.CharField(blank=True, max_length=255, null=True)),
                ("download_base_url", models.CharField(blank=True, max_length=2048, null=True)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("error_message", models.TextField(blank=True, null=True)),
                (
                    "created_by",
                    django_userforeignkey.models.fields.UserForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_created",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="User who created this element",
                    ),
                ),
                (
                    "last_modified_by",
                    django_userforeignkey.models.fields.UserForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_modified",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="User who last modified this element",
                    ),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
    ]
