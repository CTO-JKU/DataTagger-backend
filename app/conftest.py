import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

User = get_user_model()


@pytest.fixture
def client():
    yield APIClient()


@pytest.fixture(autouse=True)
def initial_users():
    user_1 = User.objects.create_user("user_1", "user_1@test.local", "password")
    user_1.is_active = True
    user_1.is_global_metadata_template_admin = True
    user_1.can_create_projects = True
    user_1.save()

    user_2 = User.objects.create_user("user_2", "user_2@test.local", "password")
    user_2.is_active = True
    user_2.can_create_projects = True
    user_2.save()

    tum_member_user = User.objects.create_user("tum_member_user", "tum_member_user@test.local", "password")
    tum_member_user.is_active = True
    tum_member_user.can_create_projects = True
    tum_member_user.save()

    project_admin_user = User.objects.create_user("project_admin_user", "project_admin_user@test.local", "password")
    project_admin_user.is_active = True
    project_admin_user.save()

    regular_user = User.objects.create_user("regular_user", "regular_user@test.local", "password")
    regular_user.is_active = True
    regular_user.save()

    regular_user_2 = User.objects.create_user("regular_user_2", "regular_user_2@test.local", "password")
    regular_user_2.is_active = True
    regular_user_2.save()

    yield {
        "user_1": user_1,
        "user_2": user_2,
        "tum_member_user": tum_member_user,
        "project_admin_user": project_admin_user,
        "regular_user": regular_user,
        "regular_user_2": regular_user_2,
    }


def auth_user(client: APIClient, user: User):
    url = reverse("auth-list")

    response = client.post(
        url,
        {
            User.USERNAME_FIELD: user.email,
            "password": "password",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED

    client.credentials(HTTP_AUTHORIZATION="Bearer " + response.data["token"])


@pytest.fixture(autouse=True)
def auth_user_1(client, initial_users):
    auth_user(client, initial_users["user_1"])
