from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Count, OuterRef, Q, Subquery
from django.utils import timezone

from fdm.projects.models import Project
from fdm.uploads.models import UploadsDataset, UploadsVersion

__all__ = [
    "build_dashboard_snapshot",
]


def build_dashboard_snapshot(user):
    generated_at = timezone.now()
    local_timezone = ZoneInfo(settings.TIME_ZONE)
    local_date = generated_at.astimezone(local_timezone).date()

    def start_of_day(days_from_today):
        return datetime.combine(
            local_date + timedelta(days=days_from_today),
            time.min,
            tzinfo=local_timezone,
        )

    drafts = UploadsDataset.objects.my_viewable()
    draft_counts = drafts.aggregate(
        total=Count("pk", distinct=True),
        today_or_tomorrow=Count(
            "pk",
            filter=Q(
                expiry_date__gt=generated_at,
                expiry_date__lt=start_of_day(2),
            ),
            distinct=True,
        ),
        in_2_to_3_days=Count(
            "pk",
            filter=Q(
                expiry_date__gte=start_of_day(2),
                expiry_date__lt=start_of_day(4),
            ),
            distinct=True,
        ),
        in_4_to_7_days=Count(
            "pk",
            filter=Q(
                expiry_date__gte=start_of_day(4),
                expiry_date__lt=start_of_day(8),
            ),
            distinct=True,
        ),
        in_8_to_14_days=Count(
            "pk",
            filter=Q(
                expiry_date__gte=start_of_day(8),
                expiry_date__lt=start_of_day(15),
            ),
            distinct=True,
        ),
    )
    project_datasets = UploadsDataset.objects.all_viewable().filter(
        publication_date__isnull=False,
    )
    project_counts = Project.objects.filter(
        project_members__member=user,
    ).aggregate(
        total=Count("pk", distinct=True),
        created=Count(
            "pk",
            filter=Q(created_by=user),
            distinct=True,
        ),
    )
    latest_version = UploadsVersion.objects.filter(
        dataset_id=OuterRef("pk"),
    ).order_by("-creation_date")
    missing_file_datasets = (
        UploadsDataset.objects.all_viewable()
        .filter(
            Q(publication_date__isnull=False)
            | Q(
                publication_date__isnull=True,
                created_by=user,
            ),
        )
        .exclude(
            publication_date__isnull=True,
            uploads_versions_count=0,
            locked=True,
        )
        .annotate(
            dashboard_latest_version_status=Subquery(
                latest_version.values("status")[:1],
            ),
            dashboard_latest_version_file_id=Subquery(
                latest_version.values("version_file_id")[:1],
            ),
        )
        .filter(
            Q(dashboard_latest_version_status__isnull=True)
            | Q(
                dashboard_latest_version_status__in=[
                    UploadsVersion.Status.ERROR,
                    UploadsVersion.Status.FINISHED,
                ],
                dashboard_latest_version_file_id__isnull=True,
            ),
        )
    )
    return {
        "generated_at": generated_at,
        "resource_counts": {
            "draft_datasets": draft_counts["total"],
            "project_datasets": project_datasets.count(),
            "created_projects": project_counts["created"],
            "collaborating_projects": project_counts["total"] - project_counts["created"],
            "datasets_missing_files": missing_file_datasets.count(),
        },
        "upcoming_auto_deletion": {
            "today_or_tomorrow": draft_counts["today_or_tomorrow"],
            "in_2_to_3_days": draft_counts["in_2_to_3_days"],
            "in_4_to_7_days": draft_counts["in_4_to_7_days"],
            "in_8_to_14_days": draft_counts["in_8_to_14_days"],
        },
    }
