from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.http import HttpResponseRedirect
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from waffle.mixins import WaffleSwitchMixin

from fdm.core.helpers import get_or_create_user
from fdm.core.rest.base import BaseGenericViewSet
from fdm.shibboleth.models.models import ShibbolethAuthCode
from fdm.shibboleth.rest.serializers import ShibbolethStartSerializer

User = get_user_model()


def decode_shibboleth_attribute(attribute):
    if not attribute:
        return attribute

    # First, try to fix misinterpreted UTF-8 as Latin-1
    try:
        return attribute.encode("latin1").decode("utf-8")
    except UnicodeError:
        pass

    # Try to fix double UTF-8 encoding
    try:
        return attribute.encode("utf-8").decode("utf-8")
    except UnicodeError:
        pass

    # If all attempts fail, return the original attribute
    return attribute


class ShibbolethViewSet(WaffleSwitchMixin, BaseGenericViewSet):
    """
    Shibboleth Authentication Workflow:

    1. Initiate Authentication (Frontend):
       - User clicks "Login" button
       - Frontend makes a GET request to /api/v1/shibboleth/start/

    2. Generate Auth Code (Backend):
       - Backend generates a unique auth_code
       - Backend constructs a shibboleth_login_url with the auth_code
       - Backend returns JSON with shibboleth_login_url and auth_code

    3. Redirect to Shibboleth (Frontend):
       - Frontend redirects the user to the shibboleth_login_url

    4. Shibboleth Authentication (External):
       - User authenticates with the Shibboleth Identity Provider (IdP)
       - Shibboleth Service Provider (SP) processes the authentication

    5. Handle Shibboleth Callback (Backend):
       - Shibboleth SP redirects to /api/v1/shibboleth/target/{auth_code}/
       - Backend receives the request with Shibboleth attributes in HTTP headers

    6. Process Authentication (Backend):
       - Backend validates the auth_code
       - Backend checks for required Shibboleth headers
       - Backend creates or updates the user account based on Shibboleth attributes
       - Backend generates a JWT token for the authenticated user

    7. Set JWT Token and Redirect (Backend):
       - Backend sets the JWT token as an HTTP-only cookie
       - Backend performs an HTTP redirect to:
         <frontend-url>/auth-complete/{user_id}

    8. Complete Authentication (Frontend):
       - Frontend router catches the /auth-complete/{user_id} route
       - Frontend considers the user as authenticated
       - Frontend can query the API using the user_id for user information
       - Frontend can use the JWT token (stored in the cookie) for subsequent API requests

    9. Subsequent API Requests (Frontend):
       - Frontend doesn't need to explicitly send the JWT token
       - The JWT token is automatically included in requests via the HTTP-only cookie
    """

    waffle_switch = "shibboleth_login_enabled_switch"

    authentication_classes = []

    permission_classes = [
        permissions.AllowAny,
    ]

    throttle_classes = []

    serializer_class = ShibbolethStartSerializer

    @extend_schema(
        responses={
            200: ShibbolethStartSerializer,
        },
        description="Initiates Shibboleth authentication process",
    )
    @action(
        detail=False,
        methods=["GET"],
        url_path="start",
        url_name="start",
    )
    def start(self, request):
        auth_code = ShibbolethAuthCode.objects.create().pk

        target_url = f"{settings.FRONTEND_WEB_URL}/api/v1/shibboleth/target/{auth_code}/"
        shibboleth_login_url = f"{settings.FRONTEND_WEB_URL}/Shibboleth.sso/Login?target={target_url}"

        serializer = ShibbolethStartSerializer(
            {
                "shibboleth_login_url": shibboleth_login_url,
                "auth_code": auth_code,
            },
        )
        return Response(serializer.data)

    @extend_schema(
        parameters=[
            OpenApiParameter(name="auth_code", type=str, location=OpenApiParameter.PATH),
        ],
        responses={
            302: None,
            400: None,
        },
        description="Handles Shibboleth authentication callback",
    )
    @action(
        detail=False,
        methods=["GET"],
        url_path="target/(?P<auth_code>[^/.]+)",
    )
    def target(self, request, auth_code=None):
        try:
            auth_code_obj = ShibbolethAuthCode.objects.get(auth_code=auth_code)
        except ShibbolethAuthCode.DoesNotExist:
            return Response(
                data={
                    "error": _("Invalid or expired auth code"),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ValidationError:
            return Response(
                data={
                    "error": _("Invalid auth code"),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if auth_code_obj.auth_code_is_expired():
            auth_code_obj.delete()

            return Response(
                data={
                    "error": _("Auth code has expired"),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        auth_code_obj.delete()

        required_headers = [
            "HTTP_SHIB_MAIL",
            "HTTP_SHIB_EDU_PERSON_AFFILIATION",
            "HTTP_SHIB_AUTH_TYPE",
            "HTTP_SHIB_APPLICATION_ID",
            "HTTP_SHIB_AUTHENTICATION_INSTANT",
            "HTTP_SHIB_AUTHENTICATION_METHOD",
            "HTTP_SHIB_AUTHNCONTEXT_CLASS",
            "HTTP_SHIB_IDENTITY_PROVIDER",
            "HTTP_SHIB_SESSION_ID",
            "HTTP_SHIB_SESSION_INDEX",
            "HTTP_SHIB_REMOTE_USER",
            "HTTP_SHIB_PERSISTENT_ID",
        ]

        # Headers that must have a non-empty value
        non_empty_headers = [
            "HTTP_SHIB_MAIL",
            "HTTP_SHIB_AUTH_TYPE",
            "HTTP_SHIB_IDENTITY_PROVIDER",
            "HTTP_SHIB_SESSION_ID",
            "HTTP_SHIB_REMOTE_USER",
        ]

        # Process only Shibboleth headers here
        shib_headers = {key: value for key, value in request.META.items() if key.startswith("HTTP_SHIB_")}
        missing_headers = [header for header in required_headers if header not in shib_headers.keys()]
        empty_headers = [header for header in non_empty_headers if header in shib_headers and not shib_headers[header]]

        if missing_headers or empty_headers:
            error_messages = []
            if missing_headers:
                error_messages.append(
                    _("Missing required Shibboleth headers: {}.").format(", ".join(missing_headers)),
                )
            if empty_headers:
                error_messages.append(
                    _("Required Shibboleth headers with empty values: {}.").format(", ".join(empty_headers)),
                )

            return Response(
                data={
                    "error": " ".join(error_messages),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # cn is not yet available in shibboleth attributes (LRZ),
        # so for now it's calculated below by using the first part of the email address
        # cn = shib_headers.get("HTTP_SHIB_CN")

        email = shib_headers.get("HTTP_SHIB_MAIL").split(";")[0]  # only use the first entry if there are multiple

        user = get_or_create_user(
            email=email,
            notification=False,
        )

        self.sync_shibboleth_headers(user, shib_headers)
        self.set_can_create_projects(user, shib_headers)

        # Generate JWT token
        refresh = RefreshToken.for_user(user)
        token = str(refresh.access_token)

        response = HttpResponseRedirect(f"{settings.FRONTEND_WEB_URL}/auth-complete/{user.pk}")

        # Set the JWT token as a cookie
        simple_jwt_settings = settings.SIMPLE_JWT
        response.set_cookie(
            simple_jwt_settings.get("AUTH_COOKIE", "token"),
            token,
            max_age=simple_jwt_settings["ACCESS_TOKEN_LIFETIME"].total_seconds(),
            httponly=simple_jwt_settings.get("AUTH_COOKIE_HTTP_ONLY", True),
            secure=simple_jwt_settings.get("AUTH_COOKIE_SECURE", False),
            samesite=simple_jwt_settings.get("AUTH_COOKIE_SAMESITE", "Lax"),
        )

        ShibbolethAuthCode.cleanup()

        # Redirect to frontend
        return response

    @staticmethod
    def sync_shibboleth_headers(user, shib_headers):
        given_name = shib_headers.get("HTTP_SHIB_GIVEN_NAME") or None
        user.given_name = decode_shibboleth_attribute(given_name)

        sn = shib_headers.get("HTTP_SHIB_SN") or None
        user.sn = decode_shibboleth_attribute(sn)

        user.edu_person_affiliation = shib_headers.get("HTTP_SHIB_EDU_PERSON_AFFILIATION") or None
        user.im_org_zug_mitarbeiter = shib_headers.get("HTTP_SHIB_IM_ORG_ZUG_MITARBEITER") or None
        user.im_org_zug_gast = shib_headers.get("HTTP_SHIB_IM_ORG_ZUG_GAST") or None
        user.im_org_zug_student = shib_headers.get("HTTP_SHIB_IM_ORG_ZUG_STUDENT") or None
        user.im_akademischer_grad = shib_headers.get("HTTP_SHIB_IM_AKADEMISCHER_GRAD") or None
        user.im_titel_anrede = shib_headers.get("HTTP_SHIB_IM_TITEL_ANREDE") or None
        user.im_titel_pre = shib_headers.get("HTTP_SHIB_IM_TITEL_PRE") or None
        user.im_titel_post = shib_headers.get("HTTP_SHIB_IM_TITEL_POST") or None
        user.last_login = timezone.now()

        # set first_name and last_name according to the corresponding shibboleth attributes
        # but only if the fields were empty at first
        # and only the first 150 characters because of a max_length restriction
        if user.given_name and not user.first_name:
            user.first_name = user.given_name[:150]

        if user.sn and not user.last_name:
            user.last_name = user.sn[:150]

        user.authentication_provider = User.AuthenticationProvider.SHIBBOLETH
        user.save()

    @staticmethod
    def set_can_create_projects(user, shib_headers):
        edu_person_affiliation = shib_headers.get("HTTP_SHIB_EDU_PERSON_AFFILIATION") or None

        affiliations = set(edu_person_affiliation.split(";") if edu_person_affiliation else [])

        if affiliations and affiliations not in [{"alum", "library-walk-in"}, {"alum"}, {"library-walk-in"}]:
            user.can_create_projects = True
        else:
            user.can_create_projects = False

        user.save()
