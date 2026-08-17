import json
import zipfile

import pytest
from conftest import auth_user
from django.urls import reverse
from rest_framework import status

from fdm.core.helpers import get_content_type_for_object, set_request_for_user
from fdm.metadata.enums import MetadataFieldType
from fdm.metadata.models import MetadataField, MetadataTemplate, MetadataTemplateContainer, MetadataTemplateField
from fdm.projects.models import Project


def _create_container_with_versions(user, field_key="GLOBAL_FIELD"):
    set_request_for_user(user)
    project = Project.objects.create(name="Project")
    container = MetadataTemplateContainer.objects.create(
        name="Portable template",
        assigned_to_content_type=project.get_content_type(),
        assigned_to_object_id=project.pk,
    )
    old_template = MetadataTemplate.objects.create(
        metadata_template_container=container,
    )
    old_field = MetadataTemplateField.objects.create(
        metadata_template=old_template,
        custom_key="old_custom_key",
        field_type=MetadataFieldType.TEXT,
    )
    old_field.set_value("old value")

    metadata_field = MetadataField.objects.create(
        key=field_key,
        field_type=MetadataFieldType.TEXT,
    )
    latest_template = MetadataTemplate.objects.create(
        metadata_template_container=container,
    )
    global_template_field = MetadataTemplateField.objects.create(
        metadata_template=latest_template,
        field=metadata_field,
        field_type=MetadataFieldType.TEXT,
        mandatory=True,
        order=1,
        previous_version_field=old_field,
    )
    global_template_field.set_value("global value")
    custom_template_field = MetadataTemplateField.objects.create(
        metadata_template=latest_template,
        custom_key="custom_field",
        field_type=MetadataFieldType.INTEGER,
        mandatory=False,
        order=2,
        config={"unit": "items"},
    )
    custom_template_field.set_value("7")

    return container, metadata_field


@pytest.mark.django_db
def test_user_can_export_one_metadata_template_container_as_portable_json_zip(
    settings,
    tmp_path,
    client,
    initial_users,
):
    settings.MEDIA_ROOT = tmp_path
    user = initial_users["user_1"]
    container, metadata_field = _create_container_with_versions(user)

    response = client.post(
        reverse("metadata-template-export-list"),
        {
            "metadata_template_containers": [str(container.pk)],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_202_ACCEPTED
    assert response.data["status"] == "SCHEDULED"

    from fdm.exports.models import MetadataTemplateExportJob
    from fdm.exports.tasks import build_metadata_template_export_zip

    export_job = MetadataTemplateExportJob.objects.get(pk=response.data["pk"])
    assert export_job.created_by == user

    build_metadata_template_export_zip(str(export_job.pk))
    export_job.refresh_from_db()

    assert export_job.status == MetadataTemplateExportJob.Status.FINISHED

    download_response = client.get(
        reverse(
            "metadata-template-export-download",
            kwargs={
                "pk": export_job.pk,
            },
        ),
    )

    assert download_response.status_code == status.HTTP_200_OK

    zip_path = tmp_path / export_job.zip_file.name
    with zipfile.ZipFile(zip_path) as archive:
        assert archive.namelist() == ["Portable template.json"]
        payload = json.loads(archive.read("Portable template.json"))

    assert payload == {
        "name": "Portable template",
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
                "config": {"unit": "items"},
                "mandatory": False,
                "order": 2,
            },
        ],
    }
    assert "assigned_to_content_type" not in payload
    assert "assigned_to_object_id" not in payload
    assert get_content_type_for_object(container.assigned_to_content_type) not in json.dumps(payload)


@pytest.mark.django_db
def test_user_can_export_multiple_metadata_template_containers_with_suffixed_json_names(
    settings,
    tmp_path,
    client,
    initial_users,
):
    settings.MEDIA_ROOT = tmp_path
    user = initial_users["user_1"]
    container_1, _ = _create_container_with_versions(user, field_key="GLOBAL_FIELD_1")
    container_2, _ = _create_container_with_versions(user, field_key="GLOBAL_FIELD_2")

    response = client.post(
        reverse("metadata-template-export-list"),
        {
            "metadata_template_containers": [
                str(container_1.pk),
                str(container_1.pk),
                str(container_2.pk),
            ],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_202_ACCEPTED

    from fdm.exports.models import MetadataTemplateExportJob
    from fdm.exports.tasks import build_metadata_template_export_zip

    export_job = MetadataTemplateExportJob.objects.get(pk=response.data["pk"])
    assert export_job.metadata_template_container_ids == [str(container_1.pk), str(container_2.pk)]

    build_metadata_template_export_zip(str(export_job.pk))
    export_job.refresh_from_db()

    with zipfile.ZipFile(tmp_path / export_job.zip_file.name) as archive:
        assert archive.namelist() == [
            "Portable template.json",
            "Portable template (1).json",
        ]


@pytest.mark.django_db
def test_user_cannot_export_non_viewable_metadata_template_container(client, initial_users):
    user_1 = initial_users["user_1"]
    user_2 = initial_users["user_2"]
    container, _ = _create_container_with_versions(user_1)
    auth_user(client, user_2)

    response = client.post(
        reverse("metadata-template-export-list"),
        {
            "metadata_template_containers": [str(container.pk)],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_user_cannot_export_metadata_template_container_without_latest_template(client, initial_users):
    user = initial_users["user_1"]
    set_request_for_user(user)
    container = MetadataTemplateContainer.objects.create(name="Empty")

    response = client.post(
        reverse("metadata-template-export-list"),
        {
            "metadata_template_containers": [str(container.pk)],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_metadata_template_export_job_sends_ready_email(settings, tmp_path, initial_users, mocker):
    settings.MEDIA_ROOT = tmp_path
    user = initial_users["user_1"]
    container, _ = _create_container_with_versions(user)
    send_mail_mock = mocker.patch("fdm.users.models.User.send_mail")

    from fdm.exports.models import MetadataTemplateExportJob
    from fdm.exports.tasks import build_metadata_template_export_zip

    export_job = MetadataTemplateExportJob.objects.create(
        metadata_template_container_ids=[str(container.pk)],
        created_by=user,
        last_modified_by=user,
    )

    build_metadata_template_export_zip(str(export_job.pk))

    send_mail_mock.assert_called_once()
