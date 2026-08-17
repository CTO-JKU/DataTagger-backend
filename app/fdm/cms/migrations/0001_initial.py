import uuid

import django.db.models.deletion
import django_userforeignkey.models.fields
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Content",
            fields=[
                ("creation_date", models.DateTimeField(auto_now_add=True, verbose_name="Creation date")),
                ("last_modification_date", models.DateTimeField(auto_now=True, verbose_name="Last modification date")),
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(db_index=True, max_length=128, verbose_name="Name of the content")),
                ("slug", models.SlugField(max_length=40, verbose_name="URL slug for this content")),
                ("publication_date", models.DateTimeField(blank=True, null=True, verbose_name="Publication date")),
                ("text_de", models.TextField(blank=True, null=True, verbose_name="German text")),
                ("text_en", models.TextField(blank=True, null=True, verbose_name="English Text")),
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
                "verbose_name": "Content Page",
                "verbose_name_plural": "Content Pages",
            },
        ),
    ]
