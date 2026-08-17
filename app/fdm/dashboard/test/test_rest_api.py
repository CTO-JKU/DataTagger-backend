from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status

from fdm.core.helpers import set_request_for_user
from fdm.projects.models import Project, ProjectMembership
from fdm.uploads.models import UploadsDataset, UploadsVersion, UploadsVersionFile

pytestmark = pytest.mark.django_db


class TestDashboardAPI:
    def test_empty_dashboard_snapshot_has_complete_response_shape(self, client):
        response = client.get(reverse("dashboard-list"), format="json")

        assert response.status_code == status.HTTP_200_OK
        assert set(response.data) == {
            "generated_at",
            "resource_counts",
            "upcoming_auto_deletion",
        }
        assert parse_datetime(response.data["generated_at"]) is not None
        assert response.data["resource_counts"] == {
            "draft_datasets": 0,
            "project_datasets": 0,
            "created_projects": 0,
            "collaborating_projects": 0,
            "datasets_missing_files": 0,
        }
        assert response.data["upcoming_auto_deletion"] == {
            "today_or_tomorrow": 0,
            "in_2_to_3_days": 0,
            "in_4_to_7_days": 0,
            "in_8_to_14_days": 0,
        }

    def test_resource_counts_match_current_user_visibility(self, client, initial_users):
        current_user = initial_users["user_1"]
        other_user = initial_users["user_2"]

        set_request_for_user(current_user)
        created_project = Project.objects.create(name="Created project")
        created_folder = created_project.folder.first()
        draft = UploadsDataset.objects.create(
            name="Visible failed draft",
            status=UploadsDataset.Status.ERROR,
        )
        UploadsVersion.objects.create(
            dataset=draft,
            status=UploadsVersion.Status.FINISHED,
        )
        UploadsVersion.objects.create(
            dataset=draft,
            status=UploadsVersion.Status.FINISHED,
        )
        UploadsDataset.objects.create(
            name="Visible in-progress published dataset",
            folder=created_folder,
            publication_date=timezone.now(),
            status=UploadsDataset.Status.IN_PROGRESS,
        )
        UploadsDataset.objects.create(
            name="Unpublished project dataset",
            folder=created_folder,
        )
        placeholder = UploadsDataset.objects.create(name="Hidden internal placeholder")
        UploadsDataset.objects.filter(pk=placeholder.pk).update(locked=True)

        set_request_for_user(other_user)
        collaborating_project = Project.objects.create(name="Collaborating project")
        collaborating_folder = collaborating_project.folder.first()
        hidden_project = Project.objects.create(name="Hidden project")
        hidden_folder = hidden_project.folder.first()
        UploadsDataset.objects.create(name="Hidden draft")
        UploadsDataset.objects.create(
            name="Collaborating published dataset",
            folder=collaborating_folder,
            publication_date=timezone.now(),
        )
        UploadsDataset.objects.create(
            name="Hidden published dataset",
            folder=hidden_folder,
            publication_date=timezone.now(),
        )

        ProjectMembership.objects.create(
            project=collaborating_project,
            member=current_user,
            is_project_admin=True,
        )

        response = client.get(reverse("dashboard-list"), format="json")

        assert response.status_code == status.HTTP_200_OK
        resource_counts = response.data["resource_counts"]
        assert {
            "draft_datasets": resource_counts["draft_datasets"],
            "project_datasets": resource_counts["project_datasets"],
            "created_projects": resource_counts["created_projects"],
            "collaborating_projects": resource_counts["collaborating_projects"],
        } == {
            "draft_datasets": 2,
            "project_datasets": 2,
            "created_projects": 1,
            "collaborating_projects": 1,
        }

    def test_upcoming_auto_deletion_uses_vienna_calendar_boundaries(self, client, initial_users, monkeypatch):
        vienna = ZoneInfo("Europe/Vienna")
        now = datetime(2026, 7, 13, 10, tzinfo=vienna)
        monkeypatch.setattr("fdm.dashboard.selectors.timezone.now", lambda: now)

        set_request_for_user(initial_users["user_1"])
        expiry_dates = [
            datetime(2026, 7, 13, 23, tzinfo=vienna),
            datetime(2026, 7, 14, 23, tzinfo=vienna),
            datetime(2026, 7, 15, 12, tzinfo=vienna),
            datetime(2026, 7, 16, 12, tzinfo=vienna),
            datetime(2026, 7, 17, 12, tzinfo=vienna),
            datetime(2026, 7, 20, 12, tzinfo=vienna),
            datetime(2026, 7, 21, 12, tzinfo=vienna),
            datetime(2026, 7, 27, 23, tzinfo=vienna),
            now - timedelta(hours=1),
            datetime(2026, 7, 28, 12, tzinfo=vienna),
        ]
        for index, expiry_date in enumerate(expiry_dates):
            dataset = UploadsDataset.objects.create(name=f"Draft {index}")
            UploadsDataset.objects.filter(pk=dataset.pk).update(expiry_date=expiry_date)

        set_request_for_user(initial_users["user_2"])
        hidden_dataset = UploadsDataset.objects.create(name="Hidden expiring draft")
        UploadsDataset.objects.filter(pk=hidden_dataset.pk).update(
            expiry_date=datetime(2026, 7, 14, 12, tzinfo=vienna),
        )

        response = client.get(reverse("dashboard-list"), format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["upcoming_auto_deletion"] == {
            "today_or_tomorrow": 2,
            "in_2_to_3_days": 2,
            "in_4_to_7_days": 2,
            "in_8_to_14_days": 2,
        }

    def test_missing_file_count_uses_latest_terminal_version_relation(self, client, initial_users):
        current_user = initial_users["user_1"]
        other_user = initial_users["user_2"]

        set_request_for_user(current_user)
        project = Project.objects.create(name="Visible project")
        visible_folder = project.folder.first()

        UploadsDataset.objects.create(name="Versionless draft")
        placeholder = UploadsDataset.objects.create(
            name="Hidden internal placeholder",
        )
        UploadsDataset.objects.filter(pk=placeholder.pk).update(
            locked=True,
        )

        finished_draft = UploadsDataset.objects.create(name="Finished draft without file")
        UploadsVersion.objects.create(
            dataset=finished_draft,
            status=UploadsVersion.Status.FINISHED,
        )

        errored_published = UploadsDataset.objects.create(
            name="Errored published dataset",
            folder=visible_folder,
            publication_date=timezone.now(),
        )
        UploadsVersion.objects.create(
            dataset=errored_published,
            status=UploadsVersion.Status.ERROR,
        )

        scheduled_draft = UploadsDataset.objects.create(name="Scheduled draft")
        UploadsVersion.objects.create(
            dataset=scheduled_draft,
            status=UploadsVersion.Status.SCHEDULED,
        )

        in_progress_published = UploadsDataset.objects.create(
            name="In-progress published dataset",
            folder=visible_folder,
            publication_date=timezone.now(),
        )
        UploadsVersion.objects.create(
            dataset=in_progress_published,
            status=UploadsVersion.Status.IN_PROGRESS,
        )

        relational_file = UploadsVersionFile.objects.create(
            status=UploadsVersionFile.Status.FINISHED,
        )
        draft_with_file = UploadsDataset.objects.create(name="Draft with relational file")
        UploadsVersion.objects.create(
            dataset=draft_with_file,
            version_file=relational_file,
            status=UploadsVersion.Status.FINISHED,
        )

        history_dataset = UploadsDataset.objects.create(name="Latest version has file")
        UploadsVersion.objects.create(
            dataset=history_dataset,
            status=UploadsVersion.Status.FINISHED,
        )
        UploadsVersion.objects.create(
            dataset=history_dataset,
            version_file=relational_file,
            status=UploadsVersion.Status.FINISHED,
        )

        set_request_for_user(other_user)
        hidden_project = Project.objects.create(name="Hidden project")
        hidden_published = UploadsDataset.objects.create(
            name="Hidden published dataset",
            folder=hidden_project.folder.first(),
            publication_date=timezone.now(),
        )
        UploadsVersion.objects.create(
            dataset=hidden_published,
            status=UploadsVersion.Status.ERROR,
        )
        hidden_draft = UploadsDataset.objects.create(name="Hidden draft without file")
        UploadsVersion.objects.create(
            dataset=hidden_draft,
            status=UploadsVersion.Status.FINISHED,
        )

        response = client.get(reverse("dashboard-list"), format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["resource_counts"]["datasets_missing_files"] == 3

    def test_dashboard_snapshot_requires_authentication(self, client):
        client.credentials()

        response = client.get(reverse("dashboard-list"), format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_dashboard_snapshot_stays_within_query_budget(
        self,
        client,
        initial_users,
    ):
        set_request_for_user(initial_users["user_1"])
        project = Project.objects.create(name="Project with several datasets")
        folder = project.folder.first()

        for index in range(6):
            draft = UploadsDataset.objects.create(name=f"Draft {index}")
            UploadsVersion.objects.create(
                dataset=draft,
                status=UploadsVersion.Status.FINISHED,
            )
            published = UploadsDataset.objects.create(
                name=f"Published dataset {index}",
                folder=folder,
                publication_date=timezone.now(),
            )
            UploadsVersion.objects.create(
                dataset=published,
                status=UploadsVersion.Status.FINISHED,
            )

        with CaptureQueriesContext(connection) as captured_queries:
            response = client.get(reverse("dashboard-list"), format="json")

        assert response.status_code == status.HTTP_200_OK
        application_queries = [
            query
            for query in captured_queries.captured_queries
            if not query["sql"].startswith(("SAVEPOINT", "RELEASE SAVEPOINT"))
        ]
        assert len(application_queries) <= 5
