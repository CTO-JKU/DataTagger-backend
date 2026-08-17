import pytest
from django.contrib.admin import AdminSite
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from fdm.cms.admin import ContentAdmin
from fdm.cms.models.models import Content
from fdm.users.models import User


class MockRequest:
    pass


@pytest.mark.django_db
class TestCMSModels:
    @pytest.fixture(autouse=True)
    def _setup(self, client):
        self.admin_user = User.objects.create_superuser("admin", "admin@example.com", "adminpass")
        client.login(email="admin@example.com", password="adminpass")

        self.content_1 = Content.objects.get(
            slug="terms-of-use",
        )

        self.content_2 = Content.objects.get(
            slug="privacy-policy",
        )

        self.content_3 = Content.objects.get(
            slug="accessibility",
        )

        self.model_admin = ContentAdmin(Content, AdminSite())

    def test_delete_permission(self):
        request = MockRequest()
        assert self.model_admin.has_delete_permission(request) is False

    def test_add_permission(self):
        request = MockRequest()
        assert self.model_admin.has_add_permission(request) is False

    def test_delete_action_not_available(self):
        actions = self.model_admin.actions
        assert "delete_selected" not in actions

    def test_cannot_delete_from_change_list(self, client):
        change_list_url = reverse("admin:cms_content_changelist")
        response = client.get(change_list_url)
        assert response.status_code == 200
        assert "Delete" not in str(response.content)

    def test_cannot_delete_from_change_form(self, client):
        change_form_url = reverse("admin:cms_content_change", args=[self.content_1.id])
        response = client.get(change_form_url)
        assert response.status_code == 200
        assert "Delete" not in str(response.content)

    def test_delete_view_not_accessible(self, client):
        delete_url = reverse("admin:cms_content_delete", args=[self.content_2.id])
        response = client.post(delete_url, {"post": "yes"})
        assert response.status_code == 403

    def test_all_content_pages_created(self):
        assert Content.objects.count() == 3
        assert self.content_1.slug == "terms-of-use"
        assert self.content_2.slug == "privacy-policy"
        assert self.content_3.slug == "accessibility"

    def test_content_protection(self):
        """
        Ensure we can't delete a content page.
        """
        content = Content.objects.get(
            slug="accessibility",
        )

        with pytest.raises(PermissionDenied):
            content.delete()
