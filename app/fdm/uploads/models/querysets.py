from django.db.models import Q
from django_userforeignkey.request import get_current_user

from fdm.core.models.querysets import BaseQuerySet
from fdm.core.rest.permissions import can_view_in_folder

__all__ = [
    "UploadsDatasetQuerySet",
    "UploadsVersionQuerySet",
]


class UploadsDatasetQuerySet(BaseQuerySet):
    def folder_viewable(self, folder_pk, *args, **kwargs):
        user = get_current_user()

        if user.is_authenticated and can_view_in_folder(user, folder_pk):
            return self.filter(
                publication_date__isnull=False,
                folder=folder_pk,
            ).order_by(
                "name",
                "-creation_date",
            )

        return self.none()

    def my_viewable(self, view_type="list", *args, **kwargs):
        """
        My uploaded datasets
        """
        user = get_current_user()

        if user.is_authenticated:
            query = Q(
                publication_date__isnull=True,
                created_by=user,
            )

            if view_type == "detail":
                query |= Q(
                    folder__folderpermission__project_membership__member=user,
                )

            datasets = self.filter(query)

            if view_type == "list":
                datasets = datasets.exclude(
                    uploads_versions_count=0,
                    locked=True,
                )

            return datasets.distinct().order_by(
                "name",
                "-creation_date",
            )

        return self.none()

    def all_viewable(self, *args, **kwargs):
        """
        All uploaded datasets which a user has access to.
        """
        user = get_current_user()

        if user.is_authenticated:
            return self.filter(
                Q(
                    publication_date__isnull=True,
                    created_by=user,
                )
                | Q(
                    folder__folderpermission__project_membership__member=user,
                ),
            ).distinct()

        return self.none()


class UploadsVersionQuerySet(BaseQuerySet):
    def folder_viewable(self, folder_pk, *args, **kwargs):
        user = get_current_user()

        if user.is_authenticated and can_view_in_folder(user, folder_pk):
            return self.filter(
                publication_date__isnull=False,
                dataset__folder=folder_pk,
            ).order_by("-creation_date")

        return self.none()

    def my_viewable(self, view_type="list", *args, **kwargs):
        """
        My uploaded versions (draft files or via a folder permission)
        """
        user = get_current_user()

        if user.is_authenticated:
            query = Q(
                dataset__folder__isnull=True,
                publication_date__isnull=True,
                created_by=user,
            )

            if view_type == "detail":
                query |= Q(
                    dataset__folder__folderpermission__project_membership__member=user,
                )

            return self.filter(query).distinct().order_by("-creation_date")

        return self.none()
