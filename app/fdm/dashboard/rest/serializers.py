from drf_spectacular.utils import extend_schema_serializer
from rest_framework import serializers

__all__ = [
    "DashboardSnapshotSerializer",
]


class DashboardResourceCountsSerializer(serializers.Serializer):
    draft_datasets = serializers.IntegerField()
    project_datasets = serializers.IntegerField()
    created_projects = serializers.IntegerField()
    collaborating_projects = serializers.IntegerField()
    datasets_missing_files = serializers.IntegerField()


class UpcomingAutoDeletionSerializer(serializers.Serializer):
    today_or_tomorrow = serializers.IntegerField()
    in_2_to_3_days = serializers.IntegerField()
    in_4_to_7_days = serializers.IntegerField()
    in_8_to_14_days = serializers.IntegerField()


@extend_schema_serializer(many=False)
class DashboardSnapshotSerializer(serializers.Serializer):
    generated_at = serializers.DateTimeField()
    resource_counts = DashboardResourceCountsSerializer()
    upcoming_auto_deletion = UpcomingAutoDeletionSerializer()
