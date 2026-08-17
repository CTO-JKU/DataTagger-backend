from django.contrib.auth import get_user_model
from django_rest_passwordreset.views import ResetPasswordConfirm, ResetPasswordRequestToken, ResetPasswordValidateToken
from django_userforeignkey.request import get_current_user
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import mixins, serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle

from fdm.core.rest.base import BaseModelViewSet
from fdm.users.models import *
from fdm.users.rest.filter import *
from fdm.users.rest.serializers import *

User = get_user_model()

__all__ = [
    "UserViewSet",
    "ExtendedResetPasswordRequestToken",
    "ExtendedResetPasswordConfirm",
    "ExtendedResetPasswordValidate",
]


class UserViewSet(BaseModelViewSet, mixins.ListModelMixin, mixins.RetrieveModelMixin):
    queryset = User.objects.none()

    serializer_class = UserSerializer

    throttle_classes = []

    filterset_class = UserFilter

    search_fields = [
        "username",
        "email",
        "first_name",
        "last_name",
    ]

    def get_queryset(self):
        """
        Gets the queryset for the view set
        :return:
        """
        return User.objects.none()

    @extend_schema(
        responses={
            200: UserSerializer,
        },
        methods=["GET"],
    )
    @extend_schema(
        request=UserSettingsUpdateSerializer,
        responses={
            200: UserSerializer,
        },
        methods=["PATCH"],
    )
    @action(
        detail=False,
        methods=["GET", "PATCH"],
        url_path="me",
        url_name="me",
        filterset_class=None,
        pagination_class=None,
    )
    def me(self, request, *args, **kwargs):
        user = get_current_user()

        if request.method == "PATCH":
            serializer = UserSettingsUpdateSerializer(user, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()

        return Response(
            data=UserSerializer(user).data,
            status=status.HTTP_200_OK,
        )


class ExtendedResetPasswordRequestToken(ResetPasswordRequestToken):
    throttle_classes = [
        AnonRateThrottle,
    ]

    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class ExtendedResetPasswordConfirm(ResetPasswordConfirm):
    throttle_classes = [
        AnonRateThrottle,
    ]

    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class ExtendedResetPasswordValidate(ResetPasswordValidateToken):
    throttle_classes = [
        AnonRateThrottle,
    ]

    @extend_schema(
        responses={
            201: inline_serializer(
                name="ExtendedResetPasswordValidateSerializer",
                fields={
                    "status": serializers.CharField(),
                    "username": serializers.CharField(),
                    "email": serializers.EmailField(),
                },
            ),
        },
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)
