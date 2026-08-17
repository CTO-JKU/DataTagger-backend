import pytest
from django.urls import reverse
from rest_framework import status


@pytest.mark.django_db
class TestUserAPI:
    def test_read_current_user_details(self, client, initial_users):
        """
        Ensure we can read the user details of the currently logged-in user.
        """
        url = reverse("user-me")

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["username"] == initial_users["user_1"].username
        assert response.data["email"] == initial_users["user_1"].email

    def test_patch_user_settings(self, client, initial_users):
        url = reverse("user-me")
        settings_data = {"settings": {"theme": "dark", "locale": "en"}}

        response = client.patch(url, data=settings_data, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["settings"] == {"theme": "dark", "locale": "en"}

        # Verify persistence
        response = client.get(url, format="json")
        assert response.data["settings"] == {"theme": "dark", "locale": "en"}

    def test_patch_user_settings_full_replace(self, client, initial_users):
        url = reverse("user-me")

        # Set initial settings
        client.patch(url, data={"settings": {"a": 1, "b": 2}}, format="json")

        # Replace with new settings — old keys should be gone
        response = client.patch(url, data={"settings": {"c": 3}}, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["settings"] == {"c": 3}

    def test_patch_user_settings_empty_dict(self, client, initial_users):
        url = reverse("user-me")

        # Set some settings first
        client.patch(url, data={"settings": {"key": "value"}}, format="json")

        # Clear settings
        response = client.patch(url, data={"settings": {}}, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["settings"] == {}

    def test_patch_user_settings_empty_body(self, client, initial_users):
        url = reverse("user-me")

        # Set some settings first
        client.patch(url, data={"settings": {"key": "value"}}, format="json")

        # PATCH with empty body — settings should remain unchanged
        response = client.patch(url, data={}, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["settings"] == {"key": "value"}

    def test_patch_user_settings_invalid_value(self, client, initial_users):
        url = reverse("user-me")
        response = client.patch(url, data={"settings": "not a dict"}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_user_list_is_empty(self, client, initial_users):
        """
        Ensure the user list is empty.
        """
        url = reverse("user-list")

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 0
