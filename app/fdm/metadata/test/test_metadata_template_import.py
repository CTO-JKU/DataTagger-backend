import json

import pytest
from conftest import auth_user
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status

from fdm.core.helpers import get_content_type_for_model, set_request_for_user
from fdm.folders.models import FolderPermission
from fdm.metadata.enums import MetadataFieldType
from fdm.metadata.models import MetadataField, MetadataTemplateContainer, MetadataTemplateField
from fdm.projects.models import Project, ProjectMembership


def _json_file(payload, name="template.json"):
    return SimpleUploadedFile(
        name,
        json.dumps(payload).encode("utf-8"),
        content_type="application/json",
    )


@pytest.mark.django_db
def test_user_can_import_one_global_metadata_template_container_from_json(client, initial_users):
    user = initial_users["user_1"]
    set_request_for_user(user)
    project = Project.objects.create(name="Project")
    folder = project.folders.first()
    metadata_field = MetadataField.objects.create(
        key="GLOBAL_FIELD",
        field_type=MetadataFieldType.TEXT,
    )
    payload = {
        "name": "Imported template",
        "metadata_template_fields": [
            {
                "field": str(metadata_field.pk),
                "field_type": MetadataFieldType.TEXT,
                "value": "global value",
                "config": {},
                "mandatory": True,
                "order": 1,
            },
            {
                "custom_key": "custom_field",
                "field_type": MetadataFieldType.INTEGER,
                "value": "7",
                "config": {},
                "mandatory": False,
                "order": 2,
            },
        ],
    }

    response = client.post(
        reverse("metadata-template-container-import"),
        {
            "file": _json_file(payload),
        },
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["name"] == "Imported template"
    assert response.data["assigned_to_content_type"] is None
    assert response.data["assigned_to_object_id"] is None

    fields = MetadataTemplateField.objects.filter(
        metadata_template=response.data["latest_metadata_template"]["pk"],
    ).order_by("order")
    assert len(fields) == 2
    assert fields[0].field == metadata_field
    assert fields[0].get_value() == "global value"
    assert fields[1].custom_key == "custom_field"
    assert fields[1].get_value() == "7"
    assert not project.metadata_template_container.filter(pk=response.data["pk"]).exists()
    assert not folder.metadata_template_container.filter(pk=response.data["pk"]).exists()


@pytest.mark.django_db
def test_import_rejects_scope_inside_json_and_rolls_back(client):
    payload = {
        "name": "Scoped template",
        "assigned_to_content_type": "projects.project",
        "metadata_template_fields": [
            {
                "custom_key": "custom_field",
                "field_type": MetadataFieldType.TEXT,
            },
        ],
    }

    response = client.post(
        reverse("metadata-template-container-import"),
        {
            "file": _json_file(payload),
        },
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not MetadataTemplateContainer.objects.filter(name="Scoped template").exists()


@pytest.mark.django_db
def test_import_rejects_missing_global_metadata_field_uuid_and_rolls_back(client):
    payload = {
        "name": "Missing field template",
        "metadata_template_fields": [
            {
                "field": "7a49c23c-fa4d-4cb2-a619-abc4c2b3bbb1",
                "field_type": MetadataFieldType.TEXT,
            },
        ],
    }

    response = client.post(
        reverse("metadata-template-container-import"),
        {
            "file": _json_file(payload),
        },
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not MetadataTemplateContainer.objects.filter(name="Missing field template").exists()


@pytest.mark.django_db
def test_import_creates_scoped_container_and_suffixes_duplicate_names(client, initial_users):
    user = initial_users["user_1"]
    set_request_for_user(user)
    project = Project.objects.create(name="Project")
    MetadataTemplateContainer.objects.create(
        name="Imported template",
        assigned_to_content_type=project.get_content_type(),
        assigned_to_object_id=project.pk,
    )
    MetadataTemplateContainer.objects.create(name="Imported template")
    payload = {
        "name": "Imported template",
        "metadata_template_fields": [
            {
                "custom_key": "custom_field",
                "field_type": MetadataFieldType.TEXT,
                "value": "value",
            },
        ],
    }

    response = client.post(
        reverse("metadata-template-container-import"),
        {
            "file": _json_file(payload),
            "assigned_to_content_type": get_content_type_for_model(Project),
            "assigned_to_object_id": str(project.pk),
        },
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["name"] == "Imported template (1)"
    assert response.data["assigned_to_content_type"] == get_content_type_for_model(Project)
    assert response.data["assigned_to_object_id"] == str(project.pk)
    assert project.metadata_template_container.filter(pk=response.data["pk"]).exists()


@pytest.mark.django_db
def test_import_adds_folder_scoped_container_to_pool_for_folder_admin(client, initial_users):
    user = initial_users["user_1"]
    set_request_for_user(user)
    project = Project.objects.create(name="Project")
    folder = project.folders.first()
    payload = {
        "name": "Imported folder template",
        "metadata_template_fields": [],
    }

    response = client.post(
        reverse("metadata-template-container-import"),
        {
            "file": _json_file(payload),
            "assigned_to_content_type": get_content_type_for_model(folder),
            "assigned_to_object_id": str(folder.pk),
        },
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert folder.metadata_template_container.filter(pk=response.data["pk"]).exists()


@pytest.mark.django_db
def test_import_by_template_admin_without_pool_permission_stays_unpooled(client, initial_users):
    owner = initial_users["user_1"]
    importer = initial_users["user_2"]
    set_request_for_user(owner)
    project = Project.objects.create(name="Project")
    folder = project.folders.first()
    membership = ProjectMembership.objects.create(
        project=project,
        member=importer,
        is_project_admin=False,
        is_metadata_template_admin=True,
    )
    FolderPermission.objects.create(
        project_membership=membership,
        folder=folder,
        is_folder_admin=False,
        is_metadata_template_admin=True,
    )
    auth_user(client, importer)
    payload = {
        "name": "Unpooled imported template",
        "metadata_template_fields": [],
    }

    response = client.post(
        reverse("metadata-template-container-import"),
        {
            "file": _json_file(payload),
            "assigned_to_content_type": get_content_type_for_model(folder),
            "assigned_to_object_id": str(folder.pk),
        },
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert not folder.metadata_template_container.filter(pk=response.data["pk"]).exists()


@pytest.mark.django_db
def test_import_rolls_back_when_pool_add_fails(client, initial_users, mocker):
    user = initial_users["user_1"]
    set_request_for_user(user)
    project = Project.objects.create(name="Project")
    containers_before = MetadataTemplateContainer.objects.count()
    mocker.patch(
        "fdm.metadata.services.add_scoped_container_to_pool",
        side_effect=RuntimeError("simulated pool add failure"),
    )

    response = client.post(
        reverse("metadata-template-container-import"),
        {
            "file": _json_file({"name": "Rollback import", "metadata_template_fields": []}),
            "assigned_to_content_type": get_content_type_for_model(Project),
            "assigned_to_object_id": str(project.pk),
        },
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert MetadataTemplateContainer.objects.count() == containers_before


@pytest.mark.django_db
def test_import_rejects_zip_file(client):
    response = client.post(
        reverse("metadata-template-container-import"),
        {
            "file": SimpleUploadedFile(
                "templates.zip",
                b"PK\x03\x04",
                content_type="application/zip",
            ),
        },
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
