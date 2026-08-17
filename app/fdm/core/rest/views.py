import logging
import os
import shutil

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.utils.translation import gettext_lazy as _
from django_userforeignkey.request import get_current_user
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import authentication, permissions, status
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView, TokenVerifyView

from fdm.core.rest.authentication import JWTCookieAuthentication
from fdm.core.rest.base import BaseGenericViewSet
from fdm.core.rest.serializers import *
from fdm.users.rest.serializers import UserSerializer

User = get_user_model()

logger = logging.getLogger(__name__)

__all__ = [
    "ObtainJSONWebTokenViewSet",
    "RefreshJSONWebTokenViewSet",
    "VerifyJSONWebTokenViewSet",
    "JWTCookieViewSet",
    "ResetBackendView",
]


@extend_schema_view(create=extend_schema(responses=TokenResponseSerializer))
class ObtainJSONWebTokenViewSet(BaseGenericViewSet):
    """
    Obtain a JWT access token by providing email and password.
    Returns {"token": "<access_token>", "user": {...}}.
    """

    throttle_classes = [
        AnonRateThrottle,
    ]

    authentication_classes = []

    permission_classes = [
        permissions.AllowAny,
    ]

    serializer_class = CookieAuthTokenSerializer

    def create(self, request, *args, **kwargs):
        email = request.data.get(User.USERNAME_FIELD)
        password = request.data.get("password")

        if not email or not password:
            return Response(
                {"non_field_errors": [_("Unable to log in with provided credentials.")]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Authenticate using Django's auth backend (case-insensitive email)
        from django.contrib.auth import authenticate

        user = authenticate(request=request, **{User.USERNAME_FIELD: email, "password": password})

        if user is None:
            return Response(
                {"non_field_errors": [_("Unable to log in with provided credentials.")]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Check if there's already a different authentication provider linked to this user account
        if user.authentication_provider != User.AuthenticationProvider.APPLICATION:
            return Response(
                data={
                    "email": _(
                        "Your account is already connected to the {provider} authentication provider, so you are only allowed to log in using this authentication method.",
                    ).format(
                        provider=user.get_authentication_provider_display(),
                    ),
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # Check if the user is an internal user and force him to login via Shibboleth
        if user.is_internal_user:
            return Response(
                data={
                    "email": _(
                        "Your account is connected to an internal email address. You must log in through {provider}.",
                    ).format(
                        provider=User.AuthenticationProvider.SHIBBOLETH.label,
                    ),
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # Generate simplejwt tokens
        refresh = RefreshToken.for_user(user)
        token = str(refresh.access_token)

        return Response(
            {
                "token": token,
                "user": UserSerializer(user, context={"request": request}).data,
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema_view(create=extend_schema(responses=TokenResponseSerializer))
class RefreshJSONWebTokenViewSet(BaseGenericViewSet):
    """
    Refresh a JWT token. Accepts {"token": "<access_token>"} for backward
    compatibility, internally maps to simplejwt's refresh flow.
    """

    throttle_classes = [
        AnonRateThrottle,
    ]

    authentication_classes = []

    permission_classes = [
        permissions.AllowAny,
    ]

    serializer_class = CookieAuthTokenSerializer

    def create(self, request, *args, **kwargs):
        token = request.data.get("token")
        if not token:
            return Response(
                {"token": [_("This field is required.")]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # For backward compatibility, we treat the incoming token as an access token
        # and generate a new one. simplejwt normally uses refresh tokens for this,
        # but to maintain API compatibility we validate the access token and issue a new pair.
        from rest_framework_simplejwt.tokens import AccessToken

        try:
            validated_token = AccessToken(token)
            user = User.objects.get(id=validated_token["user_id"])
        except Exception:
            return Response(
                {"non_field_errors": [_("Token is invalid or expired.")]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not user.is_active:
            return Response(
                {"non_field_errors": [_("User account is disabled.")]},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        refresh = RefreshToken.for_user(user)
        new_token = str(refresh.access_token)

        return Response(
            {
                "token": new_token,
                "user": UserSerializer(user, context={"request": request}).data,
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema_view(create=extend_schema(responses=TokenResponseSerializer))
class VerifyJSONWebTokenViewSet(BaseGenericViewSet):
    """
    Verify a JWT token. Accepts {"token": "<access_token>"}.
    Returns the user data if the token is valid.
    """

    throttle_classes = [
        AnonRateThrottle,
    ]

    authentication_classes = []

    permission_classes = [
        permissions.AllowAny,
    ]

    serializer_class = CookieAuthTokenSerializer

    def create(self, request, *args, **kwargs):
        token = request.data.get("token")
        if not token:
            return Response(
                {"token": [_("This field is required.")]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from rest_framework_simplejwt.tokens import AccessToken

        try:
            validated_token = AccessToken(token)
            user = User.objects.get(id=validated_token["user_id"])
        except Exception:
            return Response(
                {"non_field_errors": [_("Token is invalid or expired.")]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "token": token,
                "user": UserSerializer(user, context={"request": request}).data,
            },
            status=status.HTTP_200_OK,
        )


@extend_schema_view(create=extend_schema(responses={200: TokenResponseSerializer}))
class JWTCookieViewSet(BaseGenericViewSet):
    throttle_classes = []

    authentication_classes = [
        JWTCookieAuthentication,
        authentication.SessionAuthentication,
    ]

    permission_classes = [
        permissions.IsAuthenticated,
    ]

    serializer_class = CookieAuthTokenSerializer

    @extend_schema(responses={200: "TokenResponseSerializer"})
    def create(self, request, *args, **kwargs):
        # Get the full cookie string
        cookie_string = request.META.get("HTTP_COOKIE", "")

        # Extract the token from the cookie string
        token = None
        for cookie in cookie_string.split(";"):
            parts = cookie.strip().split("=", 1)
            if len(parts) == 2 and parts[0] == "token":
                token = parts[1]
                break

        if not token:
            return Response(
                {"error": "No token found in cookie"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({"token": token}, status=status.HTTP_200_OK)


class ResetBackendView(BaseGenericViewSet):
    throttle_classes = []

    authentication_classes = [
        JWTCookieAuthentication,
        authentication.SessionAuthentication,
    ]

    permission_classes = [
        permissions.IsAuthenticated,
    ]

    serializer_class = []

    TABLES_TO_TRUNCATE = [
        "django_admin_log",
        "file_parser_fileparser",
        "metadata_metadata",
        "metadata_metadatafield",
        "metadata_metadatatemplate",
        "metadata_metadatatemplatecontainer",
        "metadata_metadatatemplatefield",
        "rest_framework_tus_upload",
        "uploads_uploadsdataset",
        "uploads_uploadsversion",
        "uploads_uploadsversionfile",
    ]

    @staticmethod
    def clean_media_folders():
        deleted_folder_count = 0
        deleted_file_count = 0

        try:
            for folder_name in os.listdir(settings.MEDIA_ROOT):
                folder_path = os.path.join(settings.MEDIA_ROOT, folder_name)

                if os.path.exists(folder_path) and os.path.isdir(folder_path):
                    for root, dirs, files in os.walk(folder_path):
                        deleted_file_count += len(files)

                    if not os.path.exists(settings.MEDIA_ROOT):
                        logger.error(
                            _(
                                "MEDIA_ROOT does not exist: {media_root}",
                            ).format(
                                media_root=settings.MEDIA_ROOT,
                            ),
                        )
                    else:
                        shutil.rmtree(folder_path)
                        deleted_folder_count += 1
                        logger.debug(
                            _(
                                "Deleted folder: {folder_path}",
                            ).format(
                                folder_path=folder_path,
                            ),
                        )
                else:
                    logger.error(
                        _(
                            "Folder not found or not a directory: {folder_path}",
                        ).format(
                            folder_path=folder_path,
                        ),
                    )

            return deleted_folder_count, deleted_file_count

        except Exception as e:
            logger.error(
                _(
                    "Error while deleting media folders: {e}",
                ).format(
                    e=e,
                ),
            )

    def create(self, request):
        user = get_current_user()
        # Check if user is a superuser unless the request is None, then the view is run via the management command
        if request is not None and not (user and user.is_superuser):
            return Response(
                {
                    "error": _("Only superusers can reset the backend"),
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # Check if DEBUG is True
        from django.conf import settings

        if not settings.DEBUG:
            return Response(
                {
                    "error": _("This endpoint is only available in DEBUG mode"),
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        results = {}

        try:
            # Step 1: Truncate tables and calculate row counts
            with connection.cursor() as cursor:
                # Disable foreign key constraints temporarily
                cursor.execute("SET session_replication_role = 'replica';")

                for table in self.TABLES_TO_TRUNCATE:
                    try:
                        # Count rows before truncation
                        cursor.execute(f"SELECT COUNT(*) FROM {table};")
                        initial_count = cursor.fetchone()[0]

                        # Perform truncation
                        cursor.execute(f"TRUNCATE TABLE {table} CASCADE;")

                        # Log the results for this table
                        results[table] = {
                            "deleted_count": initial_count,
                        }
                    except Exception as e:
                        results[table] = {"error": str(e)}
                        logger.error(
                            _(
                                "Error while truncating table {table}: {e}",
                            ).format(
                                table=table,
                                e=e,
                            ),
                        )
                        continue

                # Re-enable foreign key constraints
                cursor.execute("SET session_replication_role = 'origin';")

            # Step 2: Clean media folders
            deleted_folder_count, deleted_file_count = self.clean_media_folders()
            results["media"] = {
                "deleted_folder_count": deleted_folder_count,
                "deleted_file_count": deleted_file_count,
            }

            # Success response
            logger.debug(
                _(
                    "Reset backend successfully. Results: {results}",
                ).format(
                    results=results,
                ),
            )
            return Response(
                {
                    "success": True,
                    "results": results,
                },
            )

        except Exception as e:
            logger.error(
                _(
                    "Error while resetting backend: {e}",
                ).format(
                    e=e,
                ),
            )
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
