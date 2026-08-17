from django.core.exceptions import PermissionDenied
from django.db.models.query import QuerySet
from django.http import Http404
from django.shortcuts import get_list_or_404, get_object_or_404
from django_userforeignkey.request import get_current_user

from fdm.core.utils.permissions import get_permission_name

__all__ = [
    "BaseQuerySet",
]


class BaseQuerySet(QuerySet):
    """
    Custom query set

    Extends django query sets by common filters. This is a base class only for each of the custom query sets responsible
    for each of the models.
    """

    def get_object_or_404(self, *args, **kwargs):
        """
        Calls get() on a the queryset, but it raises Http404 instead of the model’s DoesNotExist exception.

        :param args: Positional lookup parameters
        :param kwargs: Keyword lookup parameters
        :return: Model instance
        """
        return get_object_or_404(self, *args, **kwargs)

    def get_list_or_404(self, *args, **kwargs):
        """
        Calls find() on a the queryset, but it raises Http404 if the resulting list is empty.

        :param args: Positional lookup parameters
        :param kwargs: Keyword lookup parameters
        :return: QuerySet
        """
        return get_list_or_404(self, *args, **kwargs)

    def get_object_or_403(self, *args, **kwargs):
        """
        Calls get() on a the queryset, but it raises PermissionDenied instead of the model’s DoesNotExist exception.

        :param args: Positional lookup parameters
        :param kwargs: Keyword lookup parameters
        :return: Model instance
        """
        try:
            self.get_object_or_404(*args, **kwargs)
        except Http404:
            raise PermissionDenied

    def get_list_or_403(self, *args, **kwargs):
        """
        Calls find() on a the queryset, but it raises PermissionDenied if the resulting list is empty.

        :param args: Positional lookup parameters
        :param kwargs: Keyword lookup parameters
        :return: QuerySet
        """
        try:
            self.get_list_or_404(*args, **kwargs)
        except Http404:
            raise PermissionDenied

    def viewable(self, *args, **kwargs):
        """
        Returns an `all` QuerySet if current user has the "APP.view_MODEL" permission (whereas "APP" corresponds to the
        managed models app label and "MODEL" corresponds to the managed models name). Returns a `none` QuerySet else.

        :param args:
        :param kwargs:
        :return:
        """
        return self.all()
        # user = get_current_user()
        # if user.has_perm(get_permission_name(self.model, "view")):
        #     return self.all()
        # return self.none()

    def editable(self, *args, **kwargs):
        """
        Returns an `all` QuerySet if current user has the "APP.change_MODEL" permission (whereas "APP" corresponds to
        the managed models app label and "MODEL" corresponds to the managed models name). Returns a `none` QuerySet
        else.
        :param args:
        :param kwargs:
        :return:
        """
        return self.all()
        # user = get_current_user()
        # if user.has_perm(get_permission_name(self.model, "change")):
        #     return self.all()
        # return self.none()

    def deletable(self, *args, **kwargs):
        """
        Returns an `all` QuerySet if current user has the "APP.delete_MODEL" permission (whereas "APP" corresponds to
        the managed models app label and "MODEL" corresponds to the managed models name). Returns a `none` QuerySet
        else.
        :param args:
        :param kwargs:
        :return:
        """
        return self.all()
        # user = get_current_user()
        # if user.has_perm(get_permission_name(self.model, "delete")):
        #     return self.all()
        # return self.none()
