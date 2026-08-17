from unittest.mock import patch

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from fdm.shibboleth.models.models import ShibbolethAuthCode

User = get_user_model()


@pytest.fixture
def api_client():
    return APIClient()


@pytest.mark.django_db
class TestShibbolethViewSet:
    def test_start_action(self, api_client):
        url = reverse("shibboleth-start")
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert "shibboleth_login_url" in response.data
        assert "auth_code" in response.data

        # Verify that a ShibbolethAuthCode object was created
        auth_code = response.data["auth_code"]
        assert ShibbolethAuthCode.objects.filter(auth_code=auth_code).exists()

    def test_target_action_success(self, api_client):
        auth_code = ShibbolethAuthCode.objects.create()

        url = reverse("shibboleth-target", kwargs={"auth_code": auth_code.pk})

        headers = {
            "HTTP_SHIB_CN": "test_user",
            "HTTP_SHIB_EDU_PERSON_AFFILIATION": "student;staff",
            "HTTP_SHIB_MAIL": "test@example.com",
            "HTTP_SHIB_REMOTE_USER": "test_user",
            "HTTP_SHIB_APPLICATION_ID": "test_app",
            "HTTP_SHIB_AUTHENTICATION_INSTANT": "2023-07-25T12:00:00Z",
            "HTTP_SHIB_AUTHENTICATION_METHOD": "urn:oasis:names:tc:SAML:2.0:ac:classes:Password",
            "HTTP_SHIB_IDENTITY_PROVIDER": "https://idp.example.com/idp/shibboleth",
            "HTTP_SHIB_AUTH_TYPE": "shibboleth",
            "HTTP_SHIB_AUTHNCONTEXT_CLASS": "context_class",
            "HTTP_SHIB_SESSION_ID": "test_session",
            "HTTP_SHIB_SESSION_INDEX": "test_session_id",
            "HTTP_SHIB_PERSISTENT_ID": "test_persistent_id",
        }

        response = api_client.get(url, **headers)

        assert response.status_code == status.HTTP_302_FOUND
        user = User.objects.get(email="test@example.com")
        assert response.url == f"{settings.FRONTEND_WEB_URL}/auth-complete/{user.pk}"

        # Check if JWT token is set in the cookie
        assert settings.SIMPLE_JWT["AUTH_COOKIE"] in response.cookies
        assert response.cookies[settings.SIMPLE_JWT["AUTH_COOKIE"]]["httponly"] is True
        assert (
            response.cookies[settings.SIMPLE_JWT["AUTH_COOKIE"]]["secure"] == settings.SIMPLE_JWT["AUTH_COOKIE_SECURE"]
        )

    def test_target_action_invalid_auth_code(self, api_client):
        url = reverse("shibboleth-target", kwargs={"auth_code": "invalid_code"})
        response = api_client.get(url)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "error" in response.data
        assert "Invalid auth code" in response.data["error"]

    def test_target_action_expired_auth_code(self, api_client):
        auth_code_object = ShibbolethAuthCode.objects.create()
        auth_code_object.creation_date = timezone.now() - timezone.timedelta(
            seconds=settings.SHIBBOLETH_AUTH_CODE_MAX_LIFESPAN + 1,
        )
        auth_code_object.save()

        url = reverse("shibboleth-target", kwargs={"auth_code": auth_code_object.pk})

        headers = {
            "HTTP_SHIB_CN": "test_user",
            "HTTP_SHIB_EDU_PERSON_AFFILIATION": "student;staff",
            "HTTP_SHIB_MAIL": "test@example.com",
            "HTTP_SHIB_REMOTE_USER": "test_user",
            "HTTP_SHIB_APPLICATION_ID": "test_app",
            "HTTP_SHIB_AUTHENTICATION_INSTANT": "2023-07-25T12:00:00Z",
            "HTTP_SHIB_AUTHENTICATION_METHOD": "urn:oasis:names:tc:SAML:2.0:ac:classes:Password",
            "HTTP_SHIB_IDENTITY_PROVIDER": "https://idp.example.com/idp/shibboleth",
            "HTTP_SHIB_AUTH_TYPE": "shibboleth",
            "HTTP_SHIB_AUTHNCONTEXT_CLASS": "context_class",
            "HTTP_SHIB_SESSION_ID": "test_session",
            "HTTP_SHIB_SESSION_INDEX": "test_session_id",
            "HTTP_SHIB_PERSISTENT_ID": "test_persistent_id",
        }

        response = api_client.get(url, **headers)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "error" in response.data
        assert "Auth code has expired" in response.data["error"]

    def test_target_action_missing_headers(self, api_client):
        auth_code = ShibbolethAuthCode.objects.create()
        url = reverse("shibboleth-target", kwargs={"auth_code": auth_code.pk})

        # Omit some required headers
        headers = {
            "HTTP_SHIB_CN": "test_user",
            "HTTP_SHIB_MAIL": "test@example.com",
        }

        response = api_client.get(url, **headers)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "error" in response.data
        assert "Missing required Shibboleth headers" in response.data["error"]

    def test_target_action_empty_headers(self, api_client):
        auth_code = ShibbolethAuthCode.objects.create()
        url = reverse("shibboleth-target", kwargs={"auth_code": auth_code.pk})

        headers = {
            "HTTP_SHIB_CN": "test_user",
            "HTTP_SHIB_EDU_PERSON_AFFILIATION": "student;staff",
            "HTTP_SHIB_MAIL": "",
            "HTTP_SHIB_REMOTE_USER": "",
            "HTTP_SHIB_APPLICATION_ID": "test_app",
            "HTTP_SHIB_AUTHENTICATION_INSTANT": "2023-07-25T12:00:00Z",
            "HTTP_SHIB_AUTHENTICATION_METHOD": "urn:oasis:names:tc:SAML:2.0:ac:classes:Password",
            "HTTP_SHIB_IDENTITY_PROVIDER": "",
            "HTTP_SHIB_AUTH_TYPE": "",
            "HTTP_SHIB_AUTHNCONTEXT_CLASS": "context_class",
            "HTTP_SHIB_SESSION_ID": "",
            "HTTP_SHIB_SESSION_INDEX": "test_session_id",
            "HTTP_SHIB_PERSISTENT_ID": "test_persistent_id",
        }

        response = api_client.get(url, **headers)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "error" in response.data
        assert "Required Shibboleth headers with empty values" in response.data["error"]

    def test_sync_shibboleth_headers(self, api_client):
        auth_code = ShibbolethAuthCode.objects.create()

        url = reverse("shibboleth-target", kwargs={"auth_code": auth_code.pk})

        headers = {
            "HTTP_SHIB_CN": "test_user",
            "HTTP_SHIB_MAIL": "test@example.com",
            "HTTP_SHIB_REMOTE_USER": "test_user",
            "HTTP_SHIB_APPLICATION_ID": "test_app",
            "HTTP_SHIB_AUTHENTICATION_INSTANT": "2023-07-25T12:00:00Z",
            "HTTP_SHIB_AUTHENTICATION_METHOD": "urn:oasis:names:tc:SAML:2.0:ac:classes:Password",
            "HTTP_SHIB_IDENTITY_PROVIDER": "https://idp.example.com/idp/shibboleth",
            "HTTP_SHIB_AUTH_TYPE": "shibboleth",
            "HTTP_SHIB_AUTHNCONTEXT_CLASS": "context_class",
            "HTTP_SHIB_SESSION_ID": "test_session",
            "HTTP_SHIB_SESSION_INDEX": "test_session_id",
            "HTTP_SHIB_PERSISTENT_ID": "test_persistent_id",
            "HTTP_SHIB_GIVEN_NAME": "Test",
            "HTTP_SHIB_SN": "User",
            "HTTP_SHIB_EDU_PERSON_AFFILIATION": "student;staff",
            "HTTP_SHIB_IM_ORG_ZUG_MITARBEITER": "Employee",
            "HTTP_SHIB_IM_ORG_ZUG_GAST": "Guest",
            "HTTP_SHIB_IM_ORG_ZUG_STUDENT": "Student",
            "HTTP_SHIB_IM_AKADEMISCHER_GRAD": "MSc",
            "HTTP_SHIB_IM_TITEL_ANREDE": "Mr",
            "HTTP_SHIB_IM_TITEL_PRE": "Dr",
            "HTTP_SHIB_IM_TITEL_POST": "PhD",
        }

        response = api_client.get(url, **headers)

        assert response.status_code == status.HTTP_302_FOUND

        user = User.objects.get(email="test@example.com")
        assert user.authentication_provider == User.AuthenticationProvider.SHIBBOLETH
        assert user.given_name == "Test"
        assert user.sn == "User"
        assert user.edu_person_affiliation == "student;staff"
        assert user.im_org_zug_mitarbeiter == "Employee"
        assert user.im_org_zug_gast == "Guest"
        assert user.im_org_zug_student == "Student"
        assert user.im_akademischer_grad == "MSc"
        assert user.im_titel_anrede == "Mr"
        assert user.im_titel_pre == "Dr"
        assert user.im_titel_post == "PhD"

        # Check can_create_projects
        assert user.can_create_projects

        # Check JWT token cookie
        assert settings.SIMPLE_JWT["AUTH_COOKIE"] in response.cookies
        assert response.cookies[settings.SIMPLE_JWT["AUTH_COOKIE"]]["httponly"] is True
        assert (
            response.cookies[settings.SIMPLE_JWT["AUTH_COOKIE"]]["secure"] == settings.SIMPLE_JWT["AUTH_COOKIE_SECURE"]
        )
        assert response.cookies[settings.SIMPLE_JWT["AUTH_COOKIE"]]["samesite"] == "Lax"

        # Check redirect URL
        assert response.url == f"{settings.FRONTEND_WEB_URL}/auth-complete/{user.pk}"

    def test_can_create_projects_logic(self, api_client):
        auth_code = ShibbolethAuthCode.objects.create()

        url = reverse("shibboleth-target", kwargs={"auth_code": auth_code.pk})

        # Test case 1: User should have can_create_projects set to True
        headers = {
            "HTTP_SHIB_CN": "test_user1",
            "HTTP_SHIB_MAIL": "test1@example.com",
            "HTTP_SHIB_REMOTE_USER": "test_user1",
            "HTTP_SHIB_APPLICATION_ID": "test_app",
            "HTTP_SHIB_AUTHENTICATION_INSTANT": "2023-07-25T12:00:00Z",
            "HTTP_SHIB_AUTHENTICATION_METHOD": "urn:oasis:names:tc:SAML:2.0:ac:classes:Password",
            "HTTP_SHIB_IDENTITY_PROVIDER": "https://idp.example.com/idp/shibboleth",
            "HTTP_SHIB_AUTH_TYPE": "shibboleth",
            "HTTP_SHIB_AUTHNCONTEXT_CLASS": "context_class",
            "HTTP_SHIB_SESSION_ID": "test_session",
            "HTTP_SHIB_SESSION_INDEX": "test_session_id",
            "HTTP_SHIB_PERSISTENT_ID": "test_persistent_id",
            "HTTP_SHIB_EDU_PERSON_AFFILIATION": "student;staff",
        }
        response = api_client.get(url, **headers)
        assert response.status_code == status.HTTP_302_FOUND
        user1 = User.objects.get(email="test1@example.com")
        assert user1.can_create_projects
        assert settings.SIMPLE_JWT["AUTH_COOKIE"] in response.cookies
        assert response.url == f"{settings.FRONTEND_WEB_URL}/auth-complete/{user1.pk}"

        # Test case 2: User should have can_create_projects set to False
        test_auth_code2 = ShibbolethAuthCode.objects.create()
        url = reverse("shibboleth-target", kwargs={"auth_code": test_auth_code2.pk})
        headers = {
            "HTTP_SHIB_CN": "test_user2",
            "HTTP_SHIB_MAIL": "test2@example.com",
            "HTTP_SHIB_REMOTE_USER": "test_user2",
            "HTTP_SHIB_APPLICATION_ID": "test_app",
            "HTTP_SHIB_AUTHENTICATION_INSTANT": "2023-07-25T12:00:00Z",
            "HTTP_SHIB_AUTHENTICATION_METHOD": "urn:oasis:names:tc:SAML:2.0:ac:classes:Password",
            "HTTP_SHIB_IDENTITY_PROVIDER": "https://idp.example.com/idp/shibboleth",
            "HTTP_SHIB_AUTH_TYPE": "shibboleth",
            "HTTP_SHIB_AUTHNCONTEXT_CLASS": "context_class",
            "HTTP_SHIB_SESSION_ID": "test_session",
            "HTTP_SHIB_SESSION_INDEX": "test_session_id",
            "HTTP_SHIB_PERSISTENT_ID": "test_persistent_id",
            "HTTP_SHIB_EDU_PERSON_AFFILIATION": "alum;library-walk-in",
        }
        response = api_client.get(url, **headers)
        assert response.status_code == status.HTTP_302_FOUND
        user2 = User.objects.get(email="test2@example.com")
        assert user2.can_create_projects is False
        assert settings.SIMPLE_JWT["AUTH_COOKIE"] in response.cookies
        assert response.url == f"{settings.FRONTEND_WEB_URL}/auth-complete/{user2.pk}"

    def test_start_action_waffle_switch_disabled(self, api_client):
        with patch("waffle.mixins.switch_is_active", return_value=False):
            url = reverse("shibboleth-start")
            response = api_client.get(url)
            assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_target_action_existing_user(self, api_client):
        # Create a test_user
        existing_user = User.objects.create_user(username="existinguser", email="existing@example.com")

        auth_code = ShibbolethAuthCode.objects.create()

        url = reverse("shibboleth-target", kwargs={"auth_code": auth_code.pk})

        headers = {
            "HTTP_SHIB_CN": "existinguser",
            "HTTP_SHIB_MAIL": "existing@example.com",
            "HTTP_SHIB_EDU_PERSON_AFFILIATION": "student;staff",
            "HTTP_SHIB_REMOTE_USER": "existinguser",
            "HTTP_SHIB_APPLICATION_ID": "test_app",
            "HTTP_SHIB_AUTHENTICATION_INSTANT": "2023-07-25T12:00:00Z",
            "HTTP_SHIB_AUTHENTICATION_METHOD": "urn:oasis:names:tc:SAML:2.0:ac:classes:Password",
            "HTTP_SHIB_IDENTITY_PROVIDER": "https://idp.example.com/idp/shibboleth",
            "HTTP_SHIB_AUTH_TYPE": "shibboleth",
            "HTTP_SHIB_AUTHNCONTEXT_CLASS": "context_class",
            "HTTP_SHIB_SESSION_ID": "test_session",
            "HTTP_SHIB_SESSION_INDEX": "test_session_id",
            "HTTP_SHIB_PERSISTENT_ID": "test_persistent_id",
        }

        response = api_client.get(url, **headers)

        assert response.status_code == status.HTTP_302_FOUND
        assert User.objects.filter(email="existing@example.com").count() == 1
        updated_user = User.objects.get(email="existing@example.com")
        assert updated_user.id == existing_user.id  # Ensure it's the same user, not a new one

        # Check JWT token cookie
        assert settings.SIMPLE_JWT["AUTH_COOKIE"] in response.cookies
        assert response.cookies[settings.SIMPLE_JWT["AUTH_COOKIE"]]["httponly"] is True
        assert (
            response.cookies[settings.SIMPLE_JWT["AUTH_COOKIE"]]["secure"] == settings.SIMPLE_JWT["AUTH_COOKIE_SECURE"]
        )
        assert response.cookies[settings.SIMPLE_JWT["AUTH_COOKIE"]]["samesite"] == "Lax"

        # Check redirect URL
        assert response.url == f"{settings.FRONTEND_WEB_URL}/auth-complete/{updated_user.pk}"

    def test_target_action_new_user_creation(self, api_client):
        auth_code = ShibbolethAuthCode.objects.create()

        url = reverse("shibboleth-target", kwargs={"auth_code": auth_code.pk})

        headers = {
            "HTTP_SHIB_CN": "newuser",
            "HTTP_SHIB_EDU_PERSON_AFFILIATION": "student;staff",
            "HTTP_SHIB_MAIL": "new@example.com",
            "HTTP_SHIB_REMOTE_USER": "newuser",
            "HTTP_SHIB_APPLICATION_ID": "test_app",
            "HTTP_SHIB_AUTHENTICATION_INSTANT": "2023-07-25T12:00:00Z",
            "HTTP_SHIB_AUTHENTICATION_METHOD": "urn:oasis:names:tc:SAML:2.0:ac:classes:Password",
            "HTTP_SHIB_IDENTITY_PROVIDER": "https://idp.example.com/idp/shibboleth",
            "HTTP_SHIB_AUTH_TYPE": "shibboleth",
            "HTTP_SHIB_AUTHNCONTEXT_CLASS": "context_class",
            "HTTP_SHIB_SESSION_ID": "test_session",
            "HTTP_SHIB_SESSION_INDEX": "test_session_id",
            "HTTP_SHIB_PERSISTENT_ID": "test_persistent_id",
        }

        assert not User.objects.filter(email="new@example.com").exists()

        response = api_client.get(url, **headers)

        assert response.status_code == status.HTTP_302_FOUND
        assert User.objects.filter(email="new@example.com").exists()
        new_user = User.objects.get(email="new@example.com")
        assert new_user.username == "new"

        # Check JWT token cookie
        assert settings.SIMPLE_JWT["AUTH_COOKIE"] in response.cookies
        assert response.cookies[settings.SIMPLE_JWT["AUTH_COOKIE"]]["httponly"] is True
        assert (
            response.cookies[settings.SIMPLE_JWT["AUTH_COOKIE"]]["secure"] == settings.SIMPLE_JWT["AUTH_COOKIE_SECURE"]
        )
        assert response.cookies[settings.SIMPLE_JWT["AUTH_COOKIE"]]["samesite"] == "Lax"

        # Check redirect URL
        assert response.url == f"{settings.FRONTEND_WEB_URL}/auth-complete/{new_user.pk}"

    def test_target_action_update_existing_user(self, api_client):
        existing_user = User.objects.create_user(username="existinguser", email="existing@example.com")

        auth_code = ShibbolethAuthCode.objects.create()

        url = reverse("shibboleth-target", kwargs={"auth_code": auth_code.pk})

        headers = {
            "HTTP_SHIB_CN": "existinguser",
            "HTTP_SHIB_MAIL": "existing@example.com",
            "HTTP_SHIB_REMOTE_USER": "existinguser",
            "HTTP_SHIB_APPLICATION_ID": "test_app",
            "HTTP_SHIB_AUTHENTICATION_INSTANT": "2023-07-25T12:00:00Z",
            "HTTP_SHIB_AUTHENTICATION_METHOD": "urn:oasis:names:tc:SAML:2.0:ac:classes:Password",
            "HTTP_SHIB_IDENTITY_PROVIDER": "https://idp.example.com/idp/shibboleth",
            "HTTP_SHIB_AUTH_TYPE": "shibboleth",
            "HTTP_SHIB_AUTHNCONTEXT_CLASS": "context_class",
            "HTTP_SHIB_SESSION_ID": "test_session",
            "HTTP_SHIB_SESSION_INDEX": "test_session_id",
            "HTTP_SHIB_PERSISTENT_ID": "test_persistent_id",
            "HTTP_SHIB_GIVEN_NAME": "Existing",
            "HTTP_SHIB_SN": "User",
            "HTTP_SHIB_EDU_PERSON_AFFILIATION": "staff",
        }

        response = api_client.get(url, **headers)

        assert response.status_code == status.HTTP_302_FOUND
        updated_user = User.objects.get(email="existing@example.com")
        assert updated_user.id == existing_user.id
        assert updated_user.given_name == "Existing"
        assert updated_user.sn == "User"
        assert updated_user.edu_person_affiliation == "staff"

        # Check JWT token cookie
        assert settings.SIMPLE_JWT["AUTH_COOKIE"] in response.cookies
        assert response.cookies[settings.SIMPLE_JWT["AUTH_COOKIE"]]["httponly"] is True
        assert (
            response.cookies[settings.SIMPLE_JWT["AUTH_COOKIE"]]["secure"] == settings.SIMPLE_JWT["AUTH_COOKIE_SECURE"]
        )
        assert response.cookies[settings.SIMPLE_JWT["AUTH_COOKIE"]]["samesite"] == "Lax"

        # Check redirect URL
        assert response.url == f"{settings.FRONTEND_WEB_URL}/auth-complete/{updated_user.pk}"

        # Test updating the user again
        auth_code = ShibbolethAuthCode.objects.create()

        url = reverse("shibboleth-target", kwargs={"auth_code": auth_code.pk})

        headers.update(
            {
                "HTTP_SHIB_GIVEN_NAME": "Updated",
                "HTTP_SHIB_SN": "UpUser",
                "HTTP_SHIB_EDU_PERSON_AFFILIATION": "staff;student",
            },
        )

        response = api_client.get(url, **headers)

        assert response.status_code == status.HTTP_302_FOUND
        updated_user = User.objects.get(email="existing@example.com")
        assert updated_user.id == existing_user.id
        assert updated_user.given_name == "Updated"
        assert updated_user.sn == "UpUser"
        assert updated_user.edu_person_affiliation == "staff;student"

        # Check JWT token cookie
        assert settings.SIMPLE_JWT["AUTH_COOKIE"] in response.cookies
        assert response.cookies[settings.SIMPLE_JWT["AUTH_COOKIE"]]["httponly"] is True
        assert (
            response.cookies[settings.SIMPLE_JWT["AUTH_COOKIE"]]["secure"] == settings.SIMPLE_JWT["AUTH_COOKIE_SECURE"]
        )
        assert response.cookies[settings.SIMPLE_JWT["AUTH_COOKIE"]]["samesite"] == "Lax"

        # Check redirect URL
        assert response.url == f"{settings.FRONTEND_WEB_URL}/auth-complete/{updated_user.pk}"

    def test_target_action_jwt_token_generation(self, api_client):
        auth_code = ShibbolethAuthCode.objects.create()

        url = reverse("shibboleth-target", kwargs={"auth_code": auth_code.pk})

        headers = {
            "HTTP_SHIB_CN": "test_user",
            "HTTP_SHIB_EDU_PERSON_AFFILIATION": "student;staff",
            "HTTP_SHIB_MAIL": "test@example.com",
            "HTTP_SHIB_REMOTE_USER": "testuser",
            "HTTP_SHIB_APPLICATION_ID": "test_app",
            "HTTP_SHIB_AUTHENTICATION_INSTANT": "2023-07-25T12:00:00Z",
            "HTTP_SHIB_AUTHENTICATION_METHOD": "urn:oasis:names:tc:SAML:2.0:ac:classes:Password",
            "HTTP_SHIB_IDENTITY_PROVIDER": "https://idp.example.com/idp/shibboleth",
            "HTTP_SHIB_AUTH_TYPE": "shibboleth",
            "HTTP_SHIB_AUTHNCONTEXT_CLASS": "context_class",
            "HTTP_SHIB_SESSION_ID": "test_session",
            "HTTP_SHIB_SESSION_INDEX": "test_session_id",
            "HTTP_SHIB_PERSISTENT_ID": "test_persistent_id",
        }

        mock_access_token = "test_jwt_token"
        with patch("fdm.shibboleth.rest.views.RefreshToken") as mock_refresh_cls:
            mock_refresh = mock_refresh_cls.for_user.return_value
            mock_refresh.access_token = mock_access_token

            response = api_client.get(url, **headers)

        assert response.status_code == status.HTTP_302_FOUND

        # Check JWT token cookie
        assert settings.SIMPLE_JWT["AUTH_COOKIE"] in response.cookies
        assert response.cookies[settings.SIMPLE_JWT["AUTH_COOKIE"]].value == mock_access_token
        assert response.cookies[settings.SIMPLE_JWT["AUTH_COOKIE"]]["httponly"] is True
        assert (
            response.cookies[settings.SIMPLE_JWT["AUTH_COOKIE"]]["secure"] == settings.SIMPLE_JWT["AUTH_COOKIE_SECURE"]
        )
        assert response.cookies[settings.SIMPLE_JWT["AUTH_COOKIE"]]["samesite"] == "Lax"
        assert (
            response.cookies[settings.SIMPLE_JWT["AUTH_COOKIE"]]["max-age"]
            == settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"].total_seconds()
        )

        # Check redirect URL
        user = User.objects.get(email="test@example.com")
        assert response.url == f"{settings.FRONTEND_WEB_URL}/auth-complete/{user.pk}"

        # Verify that RefreshToken.for_user was called
        mock_refresh_cls.for_user.assert_called_once()

    def test_target_action_redirect_url(self, api_client):
        auth_code = ShibbolethAuthCode.objects.create()

        url = reverse("shibboleth-target", kwargs={"auth_code": auth_code.pk})

        headers = {
            "HTTP_SHIB_CN": "test_user",
            "HTTP_SHIB_EDU_PERSON_AFFILIATION": "student;staff",
            "HTTP_SHIB_MAIL": "test@example.com",
            "HTTP_SHIB_REMOTE_USER": "testuser",
            "HTTP_SHIB_APPLICATION_ID": "test_app",
            "HTTP_SHIB_AUTHENTICATION_INSTANT": "2023-07-25T12:00:00Z",
            "HTTP_SHIB_AUTHENTICATION_METHOD": "urn:oasis:names:tc:SAML:2.0:ac:classes:Password",
            "HTTP_SHIB_IDENTITY_PROVIDER": "https://idp.example.com/idp/shibboleth",
            "HTTP_SHIB_AUTH_TYPE": "shibboleth",
            "HTTP_SHIB_AUTHNCONTEXT_CLASS": "context_class",
            "HTTP_SHIB_SESSION_ID": "test_session",
            "HTTP_SHIB_SESSION_INDEX": "test_session_id",
            "HTTP_SHIB_PERSISTENT_ID": "test_persistent_id",
        }

        response = api_client.get(url, **headers)

        assert response.status_code == status.HTTP_302_FOUND
        user = User.objects.get(email="test@example.com")
        assert response.url == f"{settings.FRONTEND_WEB_URL}/auth-complete/{user.pk}"

    def test_sync_shibboleth_headers_partial_data(self, api_client):
        auth_code = ShibbolethAuthCode.objects.create()

        url = reverse("shibboleth-target", kwargs={"auth_code": auth_code.pk})

        headers = {
            "HTTP_SHIB_CN": "test_user",
            "HTTP_SHIB_EDU_PERSON_AFFILIATION": "",
            "HTTP_SHIB_MAIL": "test@example.com",
            "HTTP_SHIB_REMOTE_USER": "testuser",
            "HTTP_SHIB_APPLICATION_ID": "test_app",
            "HTTP_SHIB_AUTHENTICATION_INSTANT": "2023-07-25T12:00:00Z",
            "HTTP_SHIB_AUTHENTICATION_METHOD": "urn:oasis:names:tc:SAML:2.0:ac:classes:Password",
            "HTTP_SHIB_IDENTITY_PROVIDER": "https://idp.example.com/idp/shibboleth",
            "HTTP_SHIB_AUTH_TYPE": "shibboleth",
            "HTTP_SHIB_AUTHNCONTEXT_CLASS": "context_class",
            "HTTP_SHIB_SESSION_ID": "test_session",
            "HTTP_SHIB_SESSION_INDEX": "test_session_id",
            "HTTP_SHIB_PERSISTENT_ID": "test_persistent_id",
            "HTTP_SHIB_GIVEN_NAME": "Test",
            # Omitting some headers intentionally
        }

        response = api_client.get(url, **headers)

        assert response.status_code == status.HTTP_302_FOUND
        user = User.objects.get(email="test@example.com")
        assert user.given_name == "Test"
        assert user.sn is None
        assert user.edu_person_affiliation is None

    def test_can_create_projects_edge_cases(self, api_client):
        auth_code = ShibbolethAuthCode.objects.create()

        url = reverse("shibboleth-target", kwargs={"auth_code": auth_code.pk})

        # Test case 1: Empty affiliation
        headers = {
            "HTTP_SHIB_CN": "testuser1",
            "HTTP_SHIB_MAIL": "test1@example.com",
            "HTTP_SHIB_REMOTE_USER": "testuser1",
            "HTTP_SHIB_APPLICATION_ID": "test_app",
            "HTTP_SHIB_AUTHENTICATION_INSTANT": "2023-07-25T12:00:00Z",
            "HTTP_SHIB_AUTHENTICATION_METHOD": "urn:oasis:names:tc:SAML:2.0:ac:classes:Password",
            "HTTP_SHIB_IDENTITY_PROVIDER": "https://idp.example.com/idp/shibboleth",
            "HTTP_SHIB_AUTH_TYPE": "shibboleth",
            "HTTP_SHIB_AUTHNCONTEXT_CLASS": "context_class",
            "HTTP_SHIB_SESSION_ID": "test_session",
            "HTTP_SHIB_SESSION_INDEX": "test_session_id",
            "HTTP_SHIB_PERSISTENT_ID": "test_persistent_id",
            "HTTP_SHIB_EDU_PERSON_AFFILIATION": "",
        }

        response = api_client.get(url, **headers)

        assert response.status_code == status.HTTP_302_FOUND
        user1 = User.objects.get(email="test1@example.com")
        assert user1.can_create_projects is False
        assert settings.SIMPLE_JWT["AUTH_COOKIE"] in response.cookies
        assert response.url == f"{settings.FRONTEND_WEB_URL}/auth-complete/{user1.pk}"

        # Test case 2: Single affiliation "student"
        auth_code_2 = ShibbolethAuthCode.objects.create()
        url = reverse("shibboleth-target", kwargs={"auth_code": auth_code_2.pk})
        headers["HTTP_SHIB_EDU_PERSON_AFFILIATION"] = "student"
        response = api_client.get(url, **headers)
        assert response.status_code == status.HTTP_302_FOUND
        user1.refresh_from_db()
        assert user1.can_create_projects
        assert settings.SIMPLE_JWT["AUTH_COOKIE"] in response.cookies
        assert response.url == f"{settings.FRONTEND_WEB_URL}/auth-complete/{user1.pk}"

        # Test case 3: Multiple affiliations including "alum"
        auth_code_3 = ShibbolethAuthCode.objects.create()
        url = reverse("shibboleth-target", kwargs={"auth_code": auth_code_3.pk})
        headers["HTTP_SHIB_EDU_PERSON_AFFILIATION"] = "alum;staff"
        response = api_client.get(url, **headers)
        assert response.status_code == status.HTTP_302_FOUND
        user1.refresh_from_db()
        assert user1.can_create_projects
        assert settings.SIMPLE_JWT["AUTH_COOKIE"] in response.cookies
        assert response.url == f"{settings.FRONTEND_WEB_URL}/auth-complete/{user1.pk}"

    def test_target_action_invalid_auth_code_format(self, api_client):
        url = reverse("shibboleth-target", kwargs={"auth_code": "invalid!@#$"})
        response = api_client.get(url)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "error" in response.data
        assert "Invalid auth code" in response.data["error"]

    def test_target_action_sql_injection_attempt(self, api_client):
        url = reverse("shibboleth-target", kwargs={"auth_code": "' OR '1'='1"})
        response = api_client.get(url)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "error" in response.data
        assert "Invalid auth code" in response.data["error"]

    def test_sync_shibboleth_headers_handles_long_values(self, api_client):
        auth_code = ShibbolethAuthCode.objects.create()

        url = reverse("shibboleth-target", kwargs={"auth_code": auth_code.pk})

        long_value = "a" * 255  # Assuming the field has a max_length of 255

        headers = {
            "HTTP_SHIB_CN": "test_user",
            "HTTP_SHIB_EDU_PERSON_AFFILIATION": "student;staff",
            "HTTP_SHIB_MAIL": "test@example.com",
            "HTTP_SHIB_REMOTE_USER": "testuser",
            "HTTP_SHIB_APPLICATION_ID": "test_app",
            "HTTP_SHIB_AUTHENTICATION_INSTANT": "2023-07-25T12:00:00Z",
            "HTTP_SHIB_AUTHENTICATION_METHOD": "urn:oasis:names:tc:SAML:2.0:ac:classes:Password",
            "HTTP_SHIB_IDENTITY_PROVIDER": "https://idp.example.com/idp/shibboleth",
            "HTTP_SHIB_AUTH_TYPE": "shibboleth",
            "HTTP_SHIB_AUTHNCONTEXT_CLASS": "context_class",
            "HTTP_SHIB_SESSION_ID": "test_session",
            "HTTP_SHIB_SESSION_INDEX": "test_session_id",
            "HTTP_SHIB_PERSISTENT_ID": "test_persistent_id",
            "HTTP_SHIB_GIVEN_NAME": long_value,
            "HTTP_SHIB_SN": long_value,
        }

        response = api_client.get(url, **headers)

        assert response.status_code == status.HTTP_302_FOUND
        user = User.objects.get(email="test@example.com")
        assert len(user.given_name) <= 255
        assert len(user.sn) <= 255

    def test_target_action_handles_unicode_characters(self, api_client):
        auth_code = ShibbolethAuthCode.objects.create()

        url = reverse("shibboleth-target", kwargs={"auth_code": auth_code.pk})

        headers = {
            "HTTP_SHIB_CN": "testüser",
            "HTTP_SHIB_EDU_PERSON_AFFILIATION": "student;staff",
            "HTTP_SHIB_MAIL": "test@example.com",
            "HTTP_SHIB_REMOTE_USER": "testüser",
            "HTTP_SHIB_APPLICATION_ID": "test_app",
            "HTTP_SHIB_AUTHENTICATION_INSTANT": "2023-07-25T12:00:00Z",
            "HTTP_SHIB_AUTHENTICATION_METHOD": "urn:oasis:names:tc:SAML:2.0:ac:classes:Password",
            "HTTP_SHIB_IDENTITY_PROVIDER": "https://idp.example.com/idp/shibboleth",
            "HTTP_SHIB_AUTH_TYPE": "shibboleth",
            "HTTP_SHIB_AUTHNCONTEXT_CLASS": "context_class",
            "HTTP_SHIB_SESSION_ID": "test_session",
            "HTTP_SHIB_SESSION_INDEX": "test_session_id",
            "HTTP_SHIB_PERSISTENT_ID": "test_persistent_id",
            "HTTP_SHIB_GIVEN_NAME": "Tést",
            "HTTP_SHIB_SN": "Üser",
        }

        response = api_client.get(url, **headers)

        assert response.status_code == status.HTTP_302_FOUND
        user = User.objects.get(email="test@example.com")
        assert user.username == "test"
        assert user.given_name == "Tést"
        assert user.sn == "Üser"

    def test_target_action_jwt_cookie_settings(self, api_client):
        auth_code = ShibbolethAuthCode.objects.create()

        url = reverse("shibboleth-target", kwargs={"auth_code": auth_code.pk})

        headers = {
            "HTTP_SHIB_CN": "test_user",
            "HTTP_SHIB_EDU_PERSON_AFFILIATION": "student;staff",
            "HTTP_SHIB_MAIL": "test@example.com",
            "HTTP_SHIB_REMOTE_USER": "test_user",
            "HTTP_SHIB_APPLICATION_ID": "test_app",
            "HTTP_SHIB_AUTHENTICATION_INSTANT": "2023-07-25T12:00:00Z",
            "HTTP_SHIB_AUTHENTICATION_METHOD": "urn:oasis:names:tc:SAML:2.0:ac:classes:Password",
            "HTTP_SHIB_IDENTITY_PROVIDER": "https://idp.example.com/idp/shibboleth",
            "HTTP_SHIB_AUTH_TYPE": "shibboleth",
            "HTTP_SHIB_AUTHNCONTEXT_CLASS": "context_class",
            "HTTP_SHIB_SESSION_ID": "test_session",
            "HTTP_SHIB_SESSION_INDEX": "test_session_id",
            "HTTP_SHIB_PERSISTENT_ID": "test_persistent_id",
        }

        response = api_client.get(url, **headers)

        assert response.status_code == status.HTTP_302_FOUND
        assert settings.SIMPLE_JWT["AUTH_COOKIE"] in response.cookies
        jwt_cookie = response.cookies[settings.SIMPLE_JWT["AUTH_COOKIE"]]
        assert jwt_cookie["httponly"] is True
        assert jwt_cookie["secure"] == settings.SIMPLE_JWT["AUTH_COOKIE_SECURE"]
        assert jwt_cookie["samesite"] == "Lax"
        assert jwt_cookie["max-age"] == settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"].total_seconds()

    def test_target_action_cleanup_old_auth_codes(self, api_client):
        # Create an old auth code that should be deleted
        old_code = ShibbolethAuthCode.objects.create()
        old_code.creation_date = timezone.now() - timezone.timedelta(
            seconds=settings.SHIBBOLETH_AUTH_CODE_MAX_LIFESPAN + 1,
        )
        old_code.save()

        # Create a recent auth code that should be kept
        recent_code = ShibbolethAuthCode.objects.create()
        recent_code.creation_date = timezone.now() - timezone.timedelta(seconds=200)
        recent_code.save()

        # Create a new auth code for the test
        auth_code = ShibbolethAuthCode.objects.create()

        url = reverse("shibboleth-target", kwargs={"auth_code": auth_code.pk})

        headers = {
            "HTTP_SHIB_CN": "test_user",
            "HTTP_SHIB_EDU_PERSON_AFFILIATION": "student;staff",
            "HTTP_SHIB_MAIL": "test@example.com",
            "HTTP_SHIB_REMOTE_USER": "test_user",
            "HTTP_SHIB_APPLICATION_ID": "test_app",
            "HTTP_SHIB_AUTHENTICATION_INSTANT": "2023-07-25T12:00:00Z",
            "HTTP_SHIB_AUTHENTICATION_METHOD": "urn:oasis:names:tc:SAML:2.0:ac:classes:Password",
            "HTTP_SHIB_IDENTITY_PROVIDER": "https://idp.example.com/idp/shibboleth",
            "HTTP_SHIB_AUTH_TYPE": "shibboleth",
            "HTTP_SHIB_AUTHNCONTEXT_CLASS": "context_class",
            "HTTP_SHIB_SESSION_ID": "test_session",
            "HTTP_SHIB_SESSION_INDEX": "test_session_id",
            "HTTP_SHIB_PERSISTENT_ID": "test_persistent_id",
        }

        response = api_client.get(url, **headers)

        assert response.status_code == status.HTTP_302_FOUND

        # Verify that old auth codes were deleted, but recent ones remain
        remaining_auth_codes = ShibbolethAuthCode.objects.all()
        assert remaining_auth_codes.count() == 1
        assert remaining_auth_codes.first().pk == recent_code.pk

        # Verify that old auth codes were deleted
        assert not ShibbolethAuthCode.objects.filter(pk=old_code.pk).exists()
        assert not ShibbolethAuthCode.objects.filter(pk=auth_code.pk).exists()
