import pytest
from django.contrib.admin.sites import AdminSite
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory
from django.urls import reverse

from fdm.core.helpers import set_request_for_user
from fdm.uploads.admin import UploadsDatasetAdmin, UploadsVersionAdmin, UploadsVersionFileAdmin
from fdm.uploads.models import UploadsDataset, UploadsVersion, UploadsVersionFile
from fdm.users.models import User

sample_file = SimpleUploadedFile(
    "test-file.txt",
    b"payload",
    content_type="text/plain",
)


@pytest.mark.django_db
class TestUploadsAdminBulkDelete:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        self.admin_user = User.objects.create_superuser("admin", "admin@example.com", "adminpass")
        self.admin_user.can_hard_delete_datasets = True
        self.admin_user.save()

        self.regular_user = initial_users["regular_user"]
        self.regular_user.is_staff = True
        self.regular_user.save()

        set_request_for_user(self.admin_user)

        self.site = AdminSite()
        self.dataset_admin = UploadsDatasetAdmin(UploadsDataset, self.site)
        self.version_admin = UploadsVersionAdmin(UploadsVersion, self.site)
        self.version_file_admin = UploadsVersionFileAdmin(UploadsVersionFile, self.site)

    def _create_dataset_with_version(self):
        version_file = UploadsVersionFile.objects.create(
            uploaded_file=sample_file,
        )
        dataset = UploadsDataset.objects.create(name="Test dataset")
        version = UploadsVersion.objects.create(
            dataset=dataset,
            version_file=version_file,
            name="v1",
        )
        return dataset, version, version_file

    def test_default_delete_selected_not_available_on_dataset_admin(self):
        request = RequestFactory().get("/")
        request.user = self.admin_user
        actions = self.dataset_admin.get_actions(request)
        assert "delete_selected" not in actions

    def test_bulk_delete_action_available_on_dataset_admin(self):
        request = RequestFactory().get("/")
        request.user = self.admin_user
        actions = self.dataset_admin.get_actions(request)
        assert "bulk_delete_selected" in actions

    def test_bulk_delete_action_not_available_for_regular_user(self):
        request = RequestFactory().get("/")
        request.user = self.regular_user
        actions = self.dataset_admin.get_actions(request)
        assert "bulk_delete_selected" not in actions

    def test_bulk_delete_datasets(self):
        dataset_1, version_1, file_1 = self._create_dataset_with_version()
        dataset_2, version_2, file_2 = self._create_dataset_with_version()

        dataset_1_pk = dataset_1.pk
        dataset_2_pk = dataset_2.pk
        version_1_pk = version_1.pk
        version_2_pk = version_2.pk
        file_1_pk = file_1.pk
        file_2_pk = file_2.pk

        queryset = UploadsDataset.objects.filter(pk__in=[dataset_1_pk, dataset_2_pk])

        request = RequestFactory().post("/", {"apply": "1"})
        request.user = self.admin_user
        from django.contrib.messages.storage.fallback import FallbackStorage

        setattr(request, "session", "session")
        setattr(request, "_messages", FallbackStorage(request))

        self.dataset_admin.bulk_delete_selected(request, queryset)

        assert not UploadsDataset.objects.filter(pk__in=[dataset_1_pk, dataset_2_pk]).exists()
        assert not UploadsVersion.objects.filter(pk__in=[version_1_pk, version_2_pk]).exists()
        assert not UploadsVersionFile.objects.filter(pk__in=[file_1_pk, file_2_pk]).exists()

    def test_bulk_delete_denied_for_non_admin_user(self):
        dataset, _, _ = self._create_dataset_with_version()
        dataset_pk = dataset.pk

        set_request_for_user(self.regular_user)

        queryset = UploadsDataset.objects.filter(pk=dataset_pk)

        request = RequestFactory().post("/")
        request.user = self.regular_user
        from django.contrib.messages.storage.fallback import FallbackStorage

        setattr(request, "session", "session")
        setattr(request, "_messages", FallbackStorage(request))

        self.dataset_admin.bulk_delete_selected(request, queryset)

        assert UploadsDataset.objects.filter(pk=dataset_pk).exists()

    def test_bulk_delete_via_changelist(self, client):
        set_request_for_user(self.admin_user)
        dataset_1, _, _ = self._create_dataset_with_version()
        dataset_2, _, _ = self._create_dataset_with_version()

        client.login(email="admin@example.com", password="adminpass")

        changelist_url = reverse("admin:uploads_uploadsdataset_changelist")
        response = client.post(
            changelist_url,
            {
                "action": "bulk_delete_selected",
                "_selected_action": [str(dataset_1.pk), str(dataset_2.pk)],
            },
        )

        assert response.status_code == 200
        assert UploadsDataset.objects.filter(pk__in=[dataset_1.pk, dataset_2.pk]).exists()

        response = client.post(
            changelist_url,
            {
                "action": "bulk_delete_selected",
                "_selected_action": [str(dataset_1.pk), str(dataset_2.pk)],
                "apply": "1",
            },
        )

        assert response.status_code in [200, 302]
        assert not UploadsDataset.objects.filter(pk__in=[dataset_1.pk, dataset_2.pk]).exists()

    def test_delete_confirmation_shows_apply_button_for_admin(self, client):
        set_request_for_user(self.admin_user)
        dataset, _, _ = self._create_dataset_with_version()

        client.login(email="admin@example.com", password="adminpass")

        delete_url = reverse("admin:uploads_uploadsdataset_delete", args=[dataset.pk])
        response = client.get(delete_url)

        assert response.status_code == 200
        assert b"Yes, I'm sure" in response.content

    def test_delete_redirects_to_changelist_after_single_delete(self, client):
        set_request_for_user(self.admin_user)
        dataset, _, _ = self._create_dataset_with_version()

        client.login(email="admin@example.com", password="adminpass")

        delete_url = reverse("admin:uploads_uploadsdataset_delete", args=[dataset.pk])
        changelist_url = reverse("admin:uploads_uploadsdataset_changelist")

        response = client.post(delete_url, {"post": "yes"})

        assert response.status_code == 302
        assert response["Location"].endswith(changelist_url)

    def test_version_admin_has_no_delete_permission(self):
        request = RequestFactory().get("/")
        request.user = self.admin_user
        assert self.version_admin.has_delete_permission(request) is False

    def test_version_admin_has_no_actions(self):
        request = RequestFactory().get("/")
        request.user = self.admin_user
        actions = self.version_admin.get_actions(request)
        assert len(actions) == 0

    def test_version_file_admin_has_no_delete_permission(self):
        request = RequestFactory().get("/")
        request.user = self.admin_user
        assert self.version_file_admin.has_delete_permission(request) is False

    def test_version_file_admin_has_no_actions(self):
        request = RequestFactory().get("/")
        request.user = self.admin_user
        actions = self.version_file_admin.get_actions(request)
        assert len(actions) == 0
