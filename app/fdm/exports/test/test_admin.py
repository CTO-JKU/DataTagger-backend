import uuid

import pytest
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory
from django.urls import reverse
from rest_framework import status

from fdm.core.helpers import get_content_type_for_model
from fdm.exports.admin import ExportJobAdmin
from fdm.exports.models import ExportJob
from fdm.uploads.models import UploadsDataset
from fdm.users.models import User


@pytest.fixture
def staff_user():
    user = User.objects.create_user("staff", "staff@example.com", "password")
    user.is_active = True
    user.is_staff = True
    user.save()
    return user


@pytest.fixture
def inactive_staff_user():
    user = User.objects.create_user("inactive_staff", "inactive-staff@example.com", "password")
    user.is_active = False
    user.is_staff = True
    user.save()
    return user


@pytest.fixture
def export_job(initial_users):
    return _create_export_job(initial_users["user_1"], status=ExportJob.Status.FINISHED)


@pytest.fixture
def export_job_admin():
    return ExportJobAdmin(ExportJob, AdminSite())


def _request(user):
    request = RequestFactory().get("/")
    request.user = user
    return request


def _create_export_job(user, status=ExportJob.Status.FINISHED):
    return ExportJob.objects.create(
        export_type=get_content_type_for_model(UploadsDataset),
        object_ids=[str(uuid.uuid4())],
        metadata_format=ExportJob.MetadataFormat.JSON,
        status=status,
        created_by=user,
        last_modified_by=user,
    )


def _add_zip_file(settings, tmp_path, export_job, name="export.zip"):
    settings.MEDIA_ROOT = tmp_path
    zip_path = tmp_path / "exports" / str(export_job.pk) / name
    zip_path.parent.mkdir(parents=True)
    zip_path.write_bytes(b"zip-content")
    export_job.zip_file.name = f"exports/{export_job.pk}/{name}"
    export_job.save()
    return zip_path


@pytest.mark.django_db
def test_staff_user_can_view_export_jobs_without_model_permissions(client, staff_user, export_job):
    assert not staff_user.user_permissions.exists()
    assert client.login(email="staff@example.com", password="password")

    changelist_response = client.get(reverse("admin:exports_exportjob_changelist"))
    detail_response = client.get(reverse("admin:exports_exportjob_change", args=[export_job.pk]))

    assert changelist_response.status_code == status.HTTP_200_OK
    assert detail_response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_non_staff_user_cannot_access_export_job_admin(client, initial_users, export_job):
    assert client.login(email=initial_users["regular_user"].email, password="password")

    response = client.get(reverse("admin:exports_exportjob_changelist"))

    assert response.status_code == status.HTTP_302_FOUND


@pytest.mark.django_db
def test_export_job_admin_add_and_change_are_disabled(staff_user, export_job, export_job_admin):
    request = _request(staff_user)

    assert export_job_admin.has_add_permission(request) is False
    assert export_job_admin.has_change_permission(request, export_job) is False
    assert export_job_admin.has_view_permission(request, export_job) is True


@pytest.mark.django_db
def test_inactive_staff_user_cannot_use_export_job_admin(inactive_staff_user, export_job, export_job_admin):
    request = _request(inactive_staff_user)

    assert export_job_admin.has_module_permission(request) is False
    assert export_job_admin.has_view_permission(request, export_job) is False
    assert export_job_admin.has_delete_permission(request, export_job) is False


@pytest.mark.django_db
def test_single_admin_delete_removes_finished_job_and_zip_file(settings, tmp_path, client, staff_user, export_job):
    zip_path = _add_zip_file(settings, tmp_path, export_job)
    assert client.login(email="staff@example.com", password="password")

    response = client.post(reverse("admin:exports_exportjob_delete", args=[export_job.pk]), {"post": "yes"})

    assert response.status_code == status.HTTP_302_FOUND
    assert not zip_path.exists()
    assert not ExportJob.objects.filter(pk=export_job.pk).exists()


@pytest.mark.django_db
def test_single_admin_delete_preserves_zip_file_when_another_job_reuses_it(
    settings,
    tmp_path,
    client,
    staff_user,
    initial_users,
):
    source_job = _create_export_job(initial_users["user_1"], status=ExportJob.Status.FINISHED)
    zip_path = _add_zip_file(settings, tmp_path, source_job)
    reused_job = _create_export_job(initial_users["user_2"], status=ExportJob.Status.FINISHED)
    reused_job.source_export_job = source_job
    reused_job.zip_file.name = source_job.zip_file.name
    reused_job.save(update_fields=["source_export_job", "zip_file"])
    assert client.login(email="staff@example.com", password="password")

    response = client.post(reverse("admin:exports_exportjob_delete", args=[source_job.pk]), {"post": "yes"})

    assert response.status_code == status.HTTP_302_FOUND
    assert zip_path.exists()
    assert not ExportJob.objects.filter(pk=source_job.pk).exists()
    assert ExportJob.objects.filter(pk=reused_job.pk).exists()

    response = client.post(reverse("admin:exports_exportjob_delete", args=[reused_job.pk]), {"post": "yes"})

    assert response.status_code == status.HTTP_302_FOUND
    assert not zip_path.exists()
    assert not ExportJob.objects.filter(pk=reused_job.pk).exists()


@pytest.mark.django_db
def test_bulk_admin_delete_removes_finished_and_error_jobs_and_zip_files(
    settings,
    tmp_path,
    client,
    staff_user,
    initial_users,
):
    finished_job = _create_export_job(initial_users["user_1"], status=ExportJob.Status.FINISHED)
    error_job = _create_export_job(initial_users["user_1"], status=ExportJob.Status.ERROR)
    finished_zip_path = _add_zip_file(settings, tmp_path, finished_job, name="finished.zip")
    error_zip_path = _add_zip_file(settings, tmp_path, error_job, name="error.zip")

    assert client.login(email="staff@example.com", password="password")
    response = client.post(
        reverse("admin:exports_exportjob_changelist"),
        {
            "action": "cleanup_selected_export_jobs",
            "_selected_action": [str(finished_job.pk), str(error_job.pk)],
        },
    )

    assert response.status_code == status.HTTP_302_FOUND
    assert not finished_zip_path.exists()
    assert not error_zip_path.exists()
    assert not ExportJob.objects.filter(pk__in=[finished_job.pk, error_job.pk]).exists()


@pytest.mark.django_db
def test_admin_delete_error_job_without_zip_succeeds(client, staff_user, initial_users):
    export_job = _create_export_job(initial_users["user_1"], status=ExportJob.Status.ERROR)
    assert client.login(email="staff@example.com", password="password")

    response = client.post(reverse("admin:exports_exportjob_delete", args=[export_job.pk]), {"post": "yes"})

    assert response.status_code == status.HTTP_302_FOUND
    assert not ExportJob.objects.filter(pk=export_job.pk).exists()


@pytest.mark.django_db
def test_admin_delete_missing_zip_file_does_not_block_job_deletion(settings, tmp_path, client, staff_user, export_job):
    settings.MEDIA_ROOT = tmp_path
    missing_zip_path = tmp_path / "exports" / str(export_job.pk) / "missing.zip"
    export_job.zip_file.name = f"exports/{export_job.pk}/missing.zip"
    export_job.save()
    assert client.login(email="staff@example.com", password="password")

    response = client.post(reverse("admin:exports_exportjob_delete", args=[export_job.pk]), {"post": "yes"})

    assert response.status_code == status.HTTP_302_FOUND
    assert not missing_zip_path.exists()
    assert not ExportJob.objects.filter(pk=export_job.pk).exists()


@pytest.mark.django_db
def test_admin_delete_does_not_remove_zip_file_outside_exports(settings, tmp_path, client, staff_user, export_job):
    settings.MEDIA_ROOT = tmp_path
    outside_path = tmp_path / "outside.zip"
    outside_path.write_bytes(b"zip-content")
    export_job.zip_file.name = "../outside.zip"
    export_job.save()
    assert client.login(email="staff@example.com", password="password")

    response = client.post(reverse("admin:exports_exportjob_delete", args=[export_job.pk]), {"post": "yes"})

    assert response.status_code == status.HTTP_302_FOUND
    assert outside_path.exists()
    assert not ExportJob.objects.filter(pk=export_job.pk).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("job_status", [ExportJob.Status.SCHEDULED, ExportJob.Status.IN_PROGRESS])
def test_scheduled_and_in_progress_jobs_are_not_single_deletable(
    staff_user, initial_users, export_job_admin, job_status
):
    export_job = _create_export_job(initial_users["user_1"], status=job_status)

    assert export_job_admin.has_delete_permission(_request(staff_user), export_job) is False


@pytest.mark.django_db
def test_bulk_admin_delete_skips_scheduled_and_in_progress_jobs(client, staff_user, initial_users):
    scheduled_job = _create_export_job(initial_users["user_1"], status=ExportJob.Status.SCHEDULED)
    in_progress_job = _create_export_job(initial_users["user_1"], status=ExportJob.Status.IN_PROGRESS)
    assert client.login(email="staff@example.com", password="password")

    response = client.post(
        reverse("admin:exports_exportjob_changelist"),
        {
            "action": "cleanup_selected_export_jobs",
            "_selected_action": [str(scheduled_job.pk), str(in_progress_job.pk)],
        },
    )

    assert response.status_code == status.HTTP_302_FOUND
    assert ExportJob.objects.filter(pk__in=[scheduled_job.pk, in_progress_job.pk]).count() == 2


@pytest.mark.django_db
def test_export_job_api_still_does_not_expose_delete(client, export_job):
    response = client.delete(reverse("export-detail", kwargs={"pk": export_job.pk}))

    assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
