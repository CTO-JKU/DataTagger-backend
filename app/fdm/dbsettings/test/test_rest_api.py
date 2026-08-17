import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status

from fdm.core.helpers import set_request_for_user
from fdm.dbsettings.models import *

User = get_user_model()


@pytest.mark.django_db
class TestSettingAPI:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.setting_1 = Setting.objects.create(
            key="SETTING_1",
            value="1",
            description="Test setting",
            public=True,
        )

        self.setting_2 = Setting.objects.create(
            key="SETTING_2",
            value="2",
            description="Test setting",
            public=False,
        )

    def test_read_settings_list(self, client):
        """
        Ensure we can read the setting list.
        """
        url = reverse("setting-list")

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        settings_count_logged_in = len(response.data)

        client.logout()

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        settings_count_logged_out = len(response.data)

        assert settings_count_logged_in != settings_count_logged_out

    def test_read_settings_details(self, client):
        """
        Ensure we can read the setting details.
        """
        url = reverse(
            "setting-detail",
            kwargs={
                "pk": self.setting_2.pk,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["key"] == self.setting_2.key

        client.logout()

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND
