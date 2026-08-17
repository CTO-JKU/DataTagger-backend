import uuid

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from fdm._celery import app
from fdm.core.helpers import set_request_for_user
from fdm.metadata.models import MetadataTemplate, MetadataTemplateContainer
from fdm.projects.models import Project
from fdm.uploads.models import UploadsDataset, UploadsVersion, UploadsVersionFile
from fdm.uploads.tasks import (
    process_dataset_metadata_template_upgrade,
    reconcile_dataset_metadata_template_processing,
    refresh_metadata_template_upgrade_availability,
)


@pytest.mark.django_db
def test_availability_coordinator_refreshes_cache_without_changing_dataset_status(initial_users):
    set_request_for_user(initial_users["user_1"])
    dataset = UploadsDataset.objects.create(
        name="Dataset",
        status=UploadsDataset.Status.ERROR,
        metadata_upgrade_availability_status=UploadsDataset.Status.SCHEDULED,
        has_upgradeable_metadata_templates=True,
    )

    refresh_metadata_template_upgrade_availability(str(dataset.pk))

    dataset.refresh_from_db()
    assert dataset.has_upgradeable_metadata_templates is False
    assert dataset.metadata_upgrade_availability_status == UploadsDataset.Status.FINISHED
    assert dataset.status == UploadsDataset.Status.ERROR


@pytest.mark.django_db
def test_availability_coordinator_ignores_duplicate_delivery_with_active_lease(initial_users):
    set_request_for_user(initial_users["user_1"])
    lease_token = uuid.uuid4()
    dataset = UploadsDataset.objects.create(
        name="Dataset",
        metadata_upgrade_availability_status=UploadsDataset.Status.SCHEDULED,
        has_upgradeable_metadata_templates=True,
        metadata_template_processing_lease_token=lease_token,
        metadata_template_processing_lease_expires_at=timezone.now() + timezone.timedelta(minutes=10),
    )

    refresh_metadata_template_upgrade_availability(str(dataset.pk))

    dataset.refresh_from_db()
    assert dataset.has_upgradeable_metadata_templates is True
    assert dataset.metadata_upgrade_availability_status == UploadsDataset.Status.SCHEDULED
    assert dataset.metadata_template_processing_lease_token == lease_token


@pytest.mark.django_db
def test_availability_coordinator_stops_after_bounded_transient_retries(initial_users, mocker):
    set_request_for_user(initial_users["user_1"])
    dataset = UploadsDataset.objects.create(
        name="Dataset",
        metadata_upgrade_availability_status=UploadsDataset.Status.SCHEDULED,
    )
    check_availability = mocker.patch.object(
        UploadsDataset,
        "is_latest_version_upgradeable",
        side_effect=RuntimeError("temporary failure"),
    )

    result = refresh_metadata_template_upgrade_availability.apply(args=(str(dataset.pk),), throw=False)

    dataset.refresh_from_db()
    assert result.failed()
    assert check_availability.call_count == 3
    assert dataset.metadata_upgrade_availability_status == UploadsDataset.Status.ERROR
    assert dataset.metadata_template_processing_lease_token is None


@pytest.mark.django_db
def test_new_template_version_dispatches_availability_refresh_after_commit(
    initial_users,
    mocker,
    django_capture_on_commit_callbacks,
):
    set_request_for_user(initial_users["user_1"])
    container = MetadataTemplateContainer.objects.create(name="Template")
    first_template = MetadataTemplate.objects.create(metadata_template_container=container)
    dataset = UploadsDataset.objects.create(
        name="Dataset",
        status=UploadsDataset.Status.ERROR,
        metadata_template=first_template,
    )
    version = UploadsVersion.objects.create(dataset=dataset)
    version.metadata_templates.add(first_template)
    dispatch = mocker.patch("fdm.uploads.tasks.refresh_metadata_template_upgrade_availability.apply_async")

    with django_capture_on_commit_callbacks(execute=True):
        with transaction.atomic():
            MetadataTemplate.objects.create(metadata_template_container=container)
            dispatch.assert_not_called()

    dataset.refresh_from_db()
    assert dataset.metadata_upgrade_availability_status == UploadsDataset.Status.SCHEDULED
    assert dataset.status == UploadsDataset.Status.ERROR
    dispatch.assert_called_once_with((str(dataset.pk),))


@pytest.mark.django_db
def test_rolled_back_template_version_does_not_schedule_availability_refresh(
    initial_users,
    mocker,
    django_capture_on_commit_callbacks,
):
    set_request_for_user(initial_users["user_1"])
    container = MetadataTemplateContainer.objects.create(name="Template")
    first_template = MetadataTemplate.objects.create(metadata_template_container=container)
    dataset = UploadsDataset.objects.create(name="Dataset", metadata_template=first_template)
    dispatch = mocker.patch("fdm.uploads.tasks.refresh_metadata_template_upgrade_availability.apply_async")

    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(RuntimeError), transaction.atomic():
            MetadataTemplate.objects.create(metadata_template_container=container)
            raise RuntimeError("roll back")

    dataset.refresh_from_db()
    assert dataset.metadata_upgrade_availability_status == UploadsDataset.Status.FINISHED
    dispatch.assert_not_called()


@pytest.mark.django_db
def test_project_template_change_dispatches_availability_refresh_after_commit(
    initial_users,
    mocker,
    django_capture_on_commit_callbacks,
):
    set_request_for_user(initial_users["user_1"])
    project = Project.objects.create(name="Project")
    dataset = UploadsDataset.objects.create(
        name="Dataset",
        folder=project.folders.first(),
        status=UploadsDataset.Status.ERROR,
    )
    container = MetadataTemplateContainer.objects.create(name="Template")
    template = MetadataTemplate.objects.create(metadata_template_container=container)
    dispatch = mocker.patch("fdm.uploads.tasks.refresh_metadata_template_upgrade_availability.apply_async")

    with django_capture_on_commit_callbacks(execute=True):
        with transaction.atomic():
            project.metadata_template = template
            project.save()
            dispatch.assert_not_called()

    dataset.refresh_from_db()
    assert dataset.metadata_upgrade_availability_status == UploadsDataset.Status.SCHEDULED
    assert dataset.status == UploadsDataset.Status.ERROR
    dispatch.assert_called_once_with((str(dataset.pk),))


@pytest.mark.django_db
def test_assignment_dispatch_observes_committed_project_template(
    initial_users,
    mocker,
    django_capture_on_commit_callbacks,
):
    set_request_for_user(initial_users["user_1"])
    project = Project.objects.create(name="Project")
    UploadsDataset.objects.create(name="Dataset", folder=project.folders.first())
    container = MetadataTemplateContainer.objects.create(name="Template")
    template = MetadataTemplate.objects.create(metadata_template_container=container)
    observed_template_ids = []

    def observe_assignment(_args):
        observed_template_ids.append(Project.objects.get(pk=project.pk).metadata_template_id)

    mocker.patch(
        "fdm.uploads.tasks.refresh_metadata_template_upgrade_availability.apply_async",
        side_effect=observe_assignment,
    )

    with django_capture_on_commit_callbacks(execute=True):
        project.metadata_template = template
        project.save()

    assert observed_template_ids == [template.pk]


@pytest.mark.django_db
def test_folder_template_change_dispatches_availability_refresh_after_commit(
    initial_users,
    mocker,
    django_capture_on_commit_callbacks,
):
    set_request_for_user(initial_users["user_1"])
    project = Project.objects.create(name="Project")
    folder = project.folders.first()
    dataset = UploadsDataset.objects.create(name="Dataset", folder=folder)
    container = MetadataTemplateContainer.objects.create(name="Template")
    template = MetadataTemplate.objects.create(metadata_template_container=container)
    dispatch = mocker.patch("fdm.uploads.tasks.refresh_metadata_template_upgrade_availability.apply_async")

    with django_capture_on_commit_callbacks(execute=True):
        with transaction.atomic():
            folder.metadata_template = template
            folder.save()
            dispatch.assert_not_called()

    dataset.refresh_from_db()
    assert dataset.metadata_upgrade_availability_status == UploadsDataset.Status.SCHEDULED
    dispatch.assert_called_once_with((str(dataset.pk),))


@pytest.mark.django_db
def test_dataset_template_change_dispatches_availability_refresh_after_commit(
    initial_users,
    mocker,
    django_capture_on_commit_callbacks,
):
    set_request_for_user(initial_users["user_1"])
    dataset = UploadsDataset.objects.create(name="Dataset", status=UploadsDataset.Status.ERROR)
    container = MetadataTemplateContainer.objects.create(name="Template")
    template = MetadataTemplate.objects.create(metadata_template_container=container)
    dispatch = mocker.patch("fdm.uploads.tasks.refresh_metadata_template_upgrade_availability.apply_async")

    with django_capture_on_commit_callbacks(execute=True):
        with transaction.atomic():
            dataset.metadata_template = template
            dataset.save()
            dispatch.assert_not_called()

    dataset.refresh_from_db()
    assert dataset.metadata_upgrade_availability_status == UploadsDataset.Status.SCHEDULED
    assert dataset.status == UploadsDataset.Status.ERROR
    dispatch.assert_called_once_with((str(dataset.pk),))


@pytest.mark.django_db
def test_bulk_upgrade_dispatches_dataset_upgrade_after_commit(
    client,
    initial_users,
    mocker,
    django_capture_on_commit_callbacks,
):
    set_request_for_user(initial_users["user_1"])
    container = MetadataTemplateContainer.objects.create(name="Template")
    template = MetadataTemplate.objects.create(metadata_template_container=container)
    dataset = UploadsDataset.objects.create(name="Dataset", metadata_template=template)
    UploadsVersion.objects.create(
        dataset=dataset,
        version_file=UploadsVersionFile.objects.create(
            uploaded_file=SimpleUploadedFile("data.txt", b"research data"),
        ),
    )
    dispatch = mocker.patch("fdm.uploads.tasks.process_dataset_metadata_template_upgrade.apply_async")

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            reverse("uploads-dataset-bulk-upgrade-metadata"),
            {"uploads_datasets": [dataset.pk]},
            format="json",
        )

    dataset.refresh_from_db()
    assert response.status_code == status.HTTP_201_CREATED
    assert dataset.metadata_upgrade_status == UploadsDataset.Status.SCHEDULED
    dispatch.assert_called_once_with((str(dataset.pk),))


@pytest.mark.django_db
def test_bulk_upgrade_survives_post_commit_dispatch_failure(
    client,
    initial_users,
    mocker,
    django_capture_on_commit_callbacks,
):
    set_request_for_user(initial_users["user_1"])
    container = MetadataTemplateContainer.objects.create(name="Template")
    template = MetadataTemplate.objects.create(metadata_template_container=container)
    dataset = UploadsDataset.objects.create(name="Dataset", metadata_template=template)
    UploadsVersion.objects.create(dataset=dataset)
    mocker.patch(
        "fdm.uploads.tasks.process_dataset_metadata_template_upgrade.apply_async",
        side_effect=RuntimeError("broker unavailable"),
    )

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            reverse("uploads-dataset-bulk-upgrade-metadata"),
            {"uploads_datasets": [dataset.pk]},
            format="json",
        )

    dataset.refresh_from_db()
    assert response.status_code == status.HTTP_201_CREATED
    assert dataset.metadata_upgrade_status == UploadsDataset.Status.SCHEDULED


@pytest.mark.django_db
def test_dataset_upgrade_coordinator_finishes_requested_upgrade(initial_users):
    set_request_for_user(initial_users["user_1"])
    container = MetadataTemplateContainer.objects.create(name="Template")
    template = MetadataTemplate.objects.create(metadata_template_container=container)
    dataset = UploadsDataset.objects.create(
        name="Dataset",
        metadata_template=template,
        metadata_upgrade_status=UploadsDataset.Status.SCHEDULED,
        has_upgradeable_metadata_templates=True,
    )
    UploadsVersion.objects.create(
        dataset=dataset,
        version_file=UploadsVersionFile.objects.create(
            uploaded_file=SimpleUploadedFile("data.txt", b"research data"),
        ),
    )

    process_dataset_metadata_template_upgrade(str(dataset.pk))

    dataset.refresh_from_db()
    assert dataset.uploads_versions.count() == 2
    assert dataset.metadata_upgrade_status == UploadsDataset.Status.FINISHED
    assert dataset.has_upgradeable_metadata_templates is False
    assert dataset.metadata_template_processing_lease_token is None


@pytest.mark.django_db
def test_dataset_upgrade_coordinator_defers_user_locked_dataset(initial_users):
    set_request_for_user(initial_users["user_1"])
    dataset = UploadsDataset.objects.create(
        name="Dataset",
        metadata_upgrade_status=UploadsDataset.Status.SCHEDULED,
    )
    set_request_for_user(initial_users["user_2"])
    dataset.lock()

    process_dataset_metadata_template_upgrade(str(dataset.pk))

    dataset.refresh_from_db()
    assert dataset.metadata_upgrade_status == UploadsDataset.Status.SCHEDULED
    assert dataset.metadata_template_processing_lease_token is None


@pytest.mark.django_db
def test_reconciliation_dispatches_scheduled_metadata_template_processing(initial_users, mocker):
    set_request_for_user(initial_users["user_1"])
    availability_dataset = UploadsDataset.objects.create(
        name="Availability",
        metadata_upgrade_availability_status=UploadsDataset.Status.SCHEDULED,
    )
    upgrade_dataset = UploadsDataset.objects.create(
        name="Upgrade",
        metadata_upgrade_status=UploadsDataset.Status.SCHEDULED,
    )
    availability_dispatch = mocker.patch(
        "fdm.uploads.tasks.refresh_metadata_template_upgrade_availability.apply_async",
    )
    upgrade_dispatch = mocker.patch("fdm.uploads.tasks.process_dataset_metadata_template_upgrade.apply_async")

    reconcile_dataset_metadata_template_processing()

    availability_dispatch.assert_called_once_with((str(availability_dataset.pk),))
    upgrade_dispatch.assert_called_once_with((str(upgrade_dataset.pk),))


@pytest.mark.django_db
def test_reconciliation_recovers_only_expired_metadata_template_processing(initial_users, mocker):
    set_request_for_user(initial_users["user_1"])
    now = timezone.now()
    expired_dataset = UploadsDataset.objects.create(
        name="Expired",
        metadata_upgrade_availability_status=UploadsDataset.Status.IN_PROGRESS,
        metadata_template_processing_lease_token=uuid.uuid4(),
        metadata_template_processing_lease_expires_at=now - timezone.timedelta(minutes=1),
    )
    active_dataset = UploadsDataset.objects.create(
        name="Active",
        metadata_upgrade_availability_status=UploadsDataset.Status.IN_PROGRESS,
        metadata_template_processing_lease_token=uuid.uuid4(),
        metadata_template_processing_lease_expires_at=now + timezone.timedelta(minutes=10),
    )
    dispatch = mocker.patch("fdm.uploads.tasks.refresh_metadata_template_upgrade_availability.apply_async")

    reconcile_dataset_metadata_template_processing()

    expired_dataset.refresh_from_db()
    active_dataset.refresh_from_db()
    assert expired_dataset.metadata_upgrade_availability_status == UploadsDataset.Status.SCHEDULED
    assert expired_dataset.metadata_template_processing_lease_token is None
    assert active_dataset.metadata_upgrade_availability_status == UploadsDataset.Status.IN_PROGRESS
    dispatch.assert_called_once_with((str(expired_dataset.pk),))


@pytest.mark.django_db
def test_metadata_template_processing_uses_reconciliation_instead_of_normal_polling():
    schedule = app.conf.beat_schedule

    assert "dataset_reconcile_metadata_template_processing" in schedule
    assert "dataset_update_has_upgradeable_metadata_templates_for_datasets" not in schedule
    assert "dataset_upgrade_latest_version_metadata" not in schedule


@pytest.mark.django_db
def test_reconciliation_adopts_legacy_in_progress_upgrade_after_grace(initial_users, mocker, settings):
    set_request_for_user(initial_users["user_1"])
    settings.METADATA_TEMPLATE_PROCESSING_LEGACY_GRACE_SECONDS = 300
    old_dataset = UploadsDataset.objects.create(
        name="Old",
        metadata_upgrade_status=UploadsDataset.Status.IN_PROGRESS,
    )
    recent_dataset = UploadsDataset.objects.create(
        name="Recent",
        metadata_upgrade_status=UploadsDataset.Status.IN_PROGRESS,
    )
    UploadsDataset.objects.filter(pk=old_dataset.pk).update(
        last_modification_date=timezone.now() - timezone.timedelta(minutes=10),
    )
    dispatch = mocker.patch("fdm.uploads.tasks.process_dataset_metadata_template_upgrade.apply_async")

    reconcile_dataset_metadata_template_processing()

    old_dataset.refresh_from_db()
    recent_dataset.refresh_from_db()
    assert old_dataset.metadata_upgrade_status == UploadsDataset.Status.SCHEDULED
    assert recent_dataset.metadata_upgrade_status == UploadsDataset.Status.IN_PROGRESS
    dispatch.assert_called_once_with((str(old_dataset.pk),))
