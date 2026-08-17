import pytest
from conftest import auth_user
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_rest_passwordreset.models import ResetPasswordToken
from rest_framework import status
from waffle.models import Switch

from fdm.core.helpers import get_content_type_for_object, set_request_for_user
from fdm.folders.models import Folder, FolderPermission, get_default_folder_storage
from fdm.folders.rest.filter import FolderPermissionFilter
from fdm.metadata.helpers import MetadataFieldType, set_metadata
from fdm.metadata.models import MetadataTemplate, MetadataTemplateContainer, MetadataTemplateField
from fdm.projects.models import Project, ProjectMembership
from fdm.storages.models import DynamicStorage
from fdm.uploads.models import UploadsDataset

User = get_user_model()


@pytest.mark.django_db
class TestFolderAPI:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.project_1 = Project.objects.create(
            name="Project 1",
        )

        self.folder_1 = self.project_1.folder.first()

        self.project_2 = Project.objects.create(
            name="Project 2",
        )

        self.folder_2 = self.project_2.folder.first()

        self.metadata_template_container_1 = MetadataTemplateContainer.objects.create(
            name="Project metadata template container 1",
            assigned_to_content_type=self.project_1.get_content_type(),
            assigned_to_object_id=self.project_1.pk,
        )

        self.metadata_template_1 = MetadataTemplate.objects.create(
            metadata_template_container=self.metadata_template_container_1,
        )

        set_metadata(
            assigned_to_content_type=self.project_1.folders.first().get_content_type(),
            assigned_to_object_id=self.project_1.folders.first().pk,
            custom_key="custom_key_1",
            value="custom_value_1",
        )

    def test_read_folder_list(self, client, initial_users):
        """
        Ensure we can read the folder list.
        """
        url = reverse("folder-list")

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 2

        url_filter = f"{url}?folder_permission__can_edit=true"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 2

        url_filter = f"{url}?folder_permission__can_edit=false"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0

        project_membership_user_2 = ProjectMembership.objects.create(
            project=self.project_1,
            member=initial_users["user_2"],
            is_project_admin=False,
            can_create_folders=False,
            is_metadata_template_admin=False,
        )

        FolderPermission.objects.create(
            folder=self.folder_1,
            project_membership=project_membership_user_2,
            is_folder_admin=False,
            is_metadata_template_admin=False,
            can_edit=False,
        )

        auth_user(client, initial_users["user_2"])

        url_filter = f"{url}?folder_permission__can_edit=true"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0

        url_filter = f"{url}?folder_permission__can_edit=false"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

    def test_read_folder_details(self, client):
        """
        Ensure we can read the folder details.
        """
        url = reverse(
            "folder-detail",
            kwargs={
                "pk": self.project_1.folders.first().pk,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == _("General")
        assert response.data["members_count"] == 1
        assert response.data["datasets_count"] == 0

    def test_default_folder_storage(self, client):
        """
        Ensure every folder has a default storage assigned to it.
        """
        default_storage = get_default_folder_storage()

        url = reverse(
            "folder-detail",
            kwargs={
                "pk": self.project_1.folders.first().pk,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["storage"]["pk"] == str(default_storage.pk)

    def test_change_folder_storage(self, client, initial_users):
        """
        Ensure we can change the folder storage using a storage PK payload
        and get the full storage object in the response.
        """
        project_membership, _ = ProjectMembership.objects.update_or_create(
            project=self.project_1,
            member=initial_users["user_1"],
            defaults={
                "is_project_admin": True,
                "can_create_folders": True,
                "is_metadata_template_admin": True,
            },
        )

        FolderPermission.objects.update_or_create(
            folder=self.folder_1,
            project_membership=project_membership,
            defaults={
                "is_folder_admin": True,
                "is_metadata_template_admin": True,
                "can_edit": True,
            },
        )

        auth_user(client, initial_users["user_1"])

        assert FolderPermission.objects.filter(
            folder=self.folder_1,
            project_membership__member=initial_users["user_1"],
            is_folder_admin=True,
        ).exists()

        Switch.objects.update_or_create(
            name="storage_private_dss_enabled_switch",
            defaults={
                "active": True,
            },
        )

        new_storage = DynamicStorage.objects.create(
            name="New storage",
            storage_type="private_dss",
            approved=True,
            mounted=True,
        )

        url = reverse(
            "folder-detail",
            kwargs={
                "pk": self.folder_1.pk,
            },
        )

        response = client.patch(
            url,
            {
                "storage": new_storage.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert isinstance(response.data["storage"], dict)
        assert response.data["storage"]["pk"] == str(new_storage.pk)
        assert response.data["storage"]["name"] == new_storage.name

    def test_change_folder_details(self, client):
        """
        Ensure we can change the folder details.
        """
        url = reverse(
            "folder-detail",
            kwargs={
                "pk": self.project_1.folders.first().pk,
            },
        )

        folder_name = "Altered folder name"

        response = client.patch(
            url,
            {
                "name": folder_name,
                "metadata_template": self.metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == folder_name
        assert response.data["metadata_template"]["pk"] == str(self.metadata_template_1.pk)

        response = client.patch(
            url,
            {
                "description": {
                    "custom_key": "custom_value",
                    "some_boolean": True,
                },
                "metadata_template": None,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert isinstance(response.data["description"], dict)
        assert response.data["description"]["custom_key"] == "custom_value"
        assert response.data["description"]["some_boolean"] is True
        assert response.data["metadata_template"] is None

    def test_change_folder_details_and_create_a_new_metadata_template(self, client, initial_users):
        """
        Ensure we can change the folder details and create a new metadata template.
        """
        url = reverse(
            "folder-detail",
            kwargs={
                "pk": self.project_1.folders.first().pk,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"] is None

        response = client.patch(
            url,
            {
                "metadata_template": self.metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"]["pk"] == str(self.metadata_template_1.pk)

        response = client.patch(
            url,
            {
                "metadata_template": {
                    "name": "Metadata template 2",
                    "metadata_template_fields": [
                        {
                            "custom_key": "custom_key_1",
                            "value": "custom value",
                            "mandatory": True,
                        },
                        {
                            "custom_key": "custom_key_2",
                            "mandatory": False,
                        },
                    ],
                },
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["metadata_template"] is not None
        assert response.data["metadata_template"]["pk"] != str(self.metadata_template_1.pk)

        url = reverse(
            "metadata-template-detail",
            kwargs={
                "pk": response.data["metadata_template"]["pk"],
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] is None
        assert response.data["metadata_template_container"]["name"] == "Metadata template 2"

    def test_delete(self, client):
        """
        Ensure we can delete an empty folder.
        """
        folder_pk = self.project_1.folders.first().pk

        url = reverse(
            "folder-detail",
            kwargs={
                "pk": folder_pk,
            },
        )

        response = client.delete(url)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert Folder.objects.filter(pk=folder_pk).count() == 0
        assert FolderPermission.objects.filter(folder=folder_pk).count() == 0

    def test_delete_protection(self, client):
        """
        Ensure we can't delete a folder if it isn't empty.
        """
        folder_pk = self.project_1.folders.first().pk

        UploadsDataset.objects.create(
            name="Dataset 1",
            folder=self.project_1.folders.first(),
        )

        url = reverse(
            "folder-detail",
            kwargs={
                "pk": folder_pk,
            },
        )

        response = client.delete(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert Folder.objects.filter(pk=folder_pk).count() == 1
        assert FolderPermission.objects.filter(folder=folder_pk).count() == 1

    def test_folder_metadata_template_container_action(self, client):
        """
        Ensure we can read the available metadata template container list for a folder.
        """
        url = reverse(
            "folder-detail",
            kwargs={
                "pk": self.project_1.folders.first().pk,
            },
        )

        # We can't use reverse for the action as this would collide with other routers
        action_url = f"{url}metadata-template-container/"

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        url = reverse("folder-list")

        response = client.post(
            url,
            {
                "name": "Folder 2",
                "project": str(self.project_1.pk),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Folder.objects.filter(project=self.project_1).count() == 2

        folder_2 = response.data

        MetadataTemplateContainer.objects.create(
            name="Folder metadata template container 2",
            assigned_to_content_type=ContentType.objects.get_for_model(Folder),
            assigned_to_object_id=folder_2["pk"],
        )

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        MetadataTemplateContainer.objects.create(
            name="Project metadata template container 1",
            assigned_to_content_type=self.project_1.get_content_type(),
            assigned_to_object_id=self.project_1.pk,
        )

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

        MetadataTemplateContainer.objects.create(
            name="Project metadata template container 2",
            assigned_to_content_type=self.project_2.get_content_type(),
            assigned_to_object_id=self.project_2.pk,
        )

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

        MetadataTemplateContainer.objects.create(
            name="Global metadata template container 1",
        )

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 3

    def test_folder_metadata_templates_action(self, client):
        """
        Ensure we can read the available metadata templates list for a folder.
        """
        url = reverse(
            "folder-detail",
            kwargs={
                "pk": self.project_1.folders.first().pk,
            },
        )

        # We can't use reverse for the action as this would collide with other routers
        action_url = f"{url}metadata-templates/"

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        url = reverse("folder-list")

        response = client.post(
            url,
            {
                "name": "Folder 2",
                "project": str(self.project_1.pk),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Folder.objects.filter(project=self.project_1).count() == 2

        folder_2 = response.data

        metadata_template_container_2 = MetadataTemplateContainer.objects.create(
            name="Folder metadata template container 2",
            assigned_to_content_type=ContentType.objects.get_for_model(Folder),
            assigned_to_object_id=folder_2["pk"],
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_2,
        )

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        metadata_template_container_3 = MetadataTemplateContainer.objects.create(
            name="Project metadata template container 1",
            assigned_to_content_type=self.project_1.get_content_type(),
            assigned_to_object_id=self.project_1.pk,
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_3,
        )

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

        metadata_template_container_4 = MetadataTemplateContainer.objects.create(
            name="Project metadata template container 2",
            assigned_to_content_type=self.project_2.get_content_type(),
            assigned_to_object_id=self.project_2.pk,
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_4,
        )

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

        metadata_template_container_5 = MetadataTemplateContainer.objects.create(
            name="Global metadata template container 1",
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_5,
        )

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 3

    def test_datasets_count(self, client, initial_users):
        """
        Ensure we get an accurate count of published datasets in a folder.
        """
        url = reverse(
            "folder-detail",
            kwargs={
                "pk": self.project_1.folders.first().pk,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["datasets_count"] == 0

        folder_1 = response.data

        set_request_for_user(initial_users["user_1"])

        UploadsDataset.objects.create(
            name="Dataset 1",
            folder_id=folder_1["pk"],
            publication_date=timezone.now(),
        )

        url = reverse(
            "folder-detail",
            kwargs={
                "pk": folder_1["pk"],
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["datasets_count"] == 1

        set_request_for_user(initial_users["user_1"])

        UploadsDataset.objects.create(
            name="Dataset 2",
            folder_id=folder_1["pk"],
            publication_date=timezone.now(),
        )

        url = reverse(
            "folder-detail",
            kwargs={
                "pk": folder_1["pk"],
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["datasets_count"] == 2

        url = reverse("folder-list")

        response = client.post(
            url,
            {
                "name": "Folder 2",
                "project": str(self.project_1.pk),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Folder.objects.filter(project=self.project_1).count() == 2

        folder_2 = response.data

        set_request_for_user(initial_users["user_1"])

        UploadsDataset.objects.create(
            name="Dataset 3",
            folder_id=folder_2["pk"],
            publication_date=timezone.now(),
        )

        url = reverse(
            "folder-detail",
            kwargs={
                "pk": folder_1["pk"],
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["datasets_count"] == 2

        url = reverse(
            "folder-detail",
            kwargs={
                "pk": folder_2["pk"],
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["datasets_count"] == 1

        uploads_dataset_4 = UploadsDataset.objects.create(
            name="Dataset 4",
            folder_id=folder_1["pk"],
        )

        url = reverse(
            "folder-detail",
            kwargs={
                "pk": folder_1["pk"],
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["datasets_count"] == 2

        uploads_dataset_4.publication_date = timezone.now()
        uploads_dataset_4.save()

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["datasets_count"] == 3

        url = reverse(
            "folder-detail",
            kwargs={
                "pk": folder_2["pk"],
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["datasets_count"] == 1

    def test_create_folder(self, client, initial_users):
        """
        Ensure we can create a new folder.
        """
        url = reverse("folder-list")

        response = client.post(
            url,
            {
                "name": "Folder 2",
                "project": str(self.project_1.pk),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["metadata_template"] is None
        assert Folder.objects.filter(project=self.project_1).count() == 2

    def test_create_folder_with_metadata_template(self, client, initial_users):
        """
        Ensure we can create a new folder with a metadata template.
        """
        url = reverse("metadata-template-list")

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        url = reverse("folder-list")

        response = client.post(
            url,
            {
                "name": "Folder 2",
                "project": str(self.project_1.pk),
                "metadata_template": {
                    "name": "Metadata template 2",
                    "metadata_template_fields": [
                        {
                            "custom_key": "custom_key_1",
                            "value": "custom value",
                            "mandatory": True,
                        },
                        {
                            "custom_key": "custom_key_2",
                            "mandatory": False,
                        },
                    ],
                },
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        folder = response.data

        url = reverse("metadata-template-list")

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 2

        url = reverse(
            "metadata-template-detail",
            kwargs={
                "pk": folder["metadata_template"]["pk"],
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] is None
        assert response.data["metadata_template_container"]["name"] == "Metadata template 2"

        content_type = ContentType.objects.get_for_model(Folder)
        assert response.data["metadata_template_container"]["assigned_to_content_type"] == get_content_type_for_object(
            content_type,
        )
        assert response.data["metadata_template_container"]["assigned_to_object_id"] == folder["pk"]

        assert response.data["metadata_template_container"]["project"] is not None
        assert response.data["metadata_template_container"]["project"]["pk"] == str(self.project_1.pk)
        assert response.data["metadata_template_container"]["project"]["name"] == self.project_1.name

        url = reverse("metadata-template-field-list")
        url_filter = f"{url}?metadata_template={folder['metadata_template']['pk']}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 2

        assert MetadataTemplateField.objects.filter(metadata_template=folder["metadata_template"]["pk"]).count() == 2

        metadata_template_field_1 = MetadataTemplateField.objects.get(custom_key="custom_key_1")
        assert metadata_template_field_1.custom_key == "custom_key_1"
        assert metadata_template_field_1.field_type == MetadataFieldType.TEXT
        assert metadata_template_field_1.get_value() == "custom value"
        assert metadata_template_field_1.mandatory is True

        metadata_template_field_2 = MetadataTemplateField.objects.get(custom_key="custom_key_2")
        assert metadata_template_field_2.custom_key == "custom_key_2"
        assert metadata_template_field_2.field_type == MetadataFieldType.TEXT
        assert metadata_template_field_2.get_value() is None
        assert metadata_template_field_2.mandatory is False

    def test_create_folder_with_user_permissions(self, client, initial_users):
        """
        Ensure we can create a new folder with user permissions.
        """
        url = reverse("folder-list")

        response = client.post(
            url,
            {
                "name": "Folder 2",
                "project": self.project_1.pk,
                "folder_users": [
                    {
                        "email": initial_users["user_2"].email,
                        "is_folder_admin": True,
                        "is_metadata_template_admin": True,
                        "can_edit": True,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        folder = response.data

        url = reverse("project-membership-list")

        url_filter = f"{url}?project={self.project_1.pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

        url_filter = f"{url}?project={self.project_1.pk}&member={initial_users['user_2'].pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert str(response.data[0]["member"]["pk"]) == str(initial_users["user_2"].pk)
        assert response.data[0]["is_project_admin"] is False
        assert response.data[0]["is_metadata_template_admin"] is False
        assert response.data[0]["can_create_folders"] is False

        url = reverse("folder-permission-list")

        url_filter = f"{url}?folder={folder['pk']}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

        url_filter = f"{url}?folder={folder['pk']}&project_membership__member={initial_users['user_1'].pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert str(response.data[0]["project_membership"]["member"]["pk"]) == str(initial_users["user_1"].pk)
        assert response.data[0]["is_folder_admin"] is True
        assert response.data[0]["is_metadata_template_admin"] is True
        assert response.data[0]["can_edit"] is True

        url_filter = f"{url}?folder={folder['pk']}&project_membership__member={initial_users['user_2'].pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert str(response.data[0]["project_membership"]["member"]["pk"]) == str(initial_users["user_2"].pk)
        assert response.data[0]["is_folder_admin"] is True
        assert response.data[0]["is_metadata_template_admin"] is True
        assert response.data[0]["can_edit"] is True

    def test_create_folder_with_user_permissions_for_an_unknown_user(self, client):
        """
        Ensure we can create a new folder with user permissions for an unknown user.
        """
        assert User.objects.count() == 6
        assert ResetPasswordToken.objects.count() == 0

        url = reverse("folder-list")

        response = client.post(
            url,
            {
                "name": "Folder 2",
                "project": self.project_1.pk,
                "folder_users": [
                    {
                        "email": "unknown@test.local",
                        "is_folder_admin": True,
                        "is_metadata_template_admin": True,
                        "can_edit": True,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        folder = Folder.objects.get(pk=response.data["pk"])
        assert folder.project.project_members.count() == 2
        assert folder.members_count == 2

        assert User.objects.count() == 7
        assert ResetPasswordToken.objects.count() == 1

    def test_check_folder_permissions_after_creating_a_new_folder(self, client, initial_users):
        """
        Ensure we have correct folder permissions after creating a new folder.
        """
        set_request_for_user(initial_users["user_1"])

        ProjectMembership.objects.create(
            project=self.project_1,
            member=initial_users["user_2"],
            is_project_admin=True,
            can_create_folders=True,
        )

        ProjectMembership.objects.create(
            project=self.project_1,
            member=initial_users["project_admin_user"],
            is_project_admin=True,
            can_create_folders=True,
        )

        ProjectMembership.objects.create(
            project=self.project_1,
            member=initial_users["tum_member_user"],
            is_project_admin=False,
            can_create_folders=True,
        )

        ProjectMembership.objects.create(
            project=self.project_1,
            member=initial_users["regular_user"],
            is_project_admin=False,
            can_create_folders=False,
        )

        assert User.objects.count() == 6
        assert ProjectMembership.objects.filter(project=self.project_1).count() == 5

        url = reverse("folder-list")

        response = client.post(
            url,
            {
                "name": "Folder 2",
                "project": self.project_1.pk,
                "folder_users": [],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        folder = Folder.objects.get(pk=response.data["pk"])
        assert User.objects.count() == 6
        assert folder.project.project_members.count() == 5
        assert folder.members_count == 3

        response = client.post(
            url,
            {
                "name": "Folder 3",
                "project": self.project_1.pk,
                "folder_users": [
                    {
                        "email": "unknown@test.local",
                        "is_folder_admin": True,
                        "is_metadata_template_admin": True,
                        "can_edit": True,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        folder = Folder.objects.get(pk=response.data["pk"])
        assert User.objects.count() == 7
        assert folder.project.project_members.count() == 6
        assert folder.members_count == 4

    def test_folder_permissions_action(self, client, initial_users):
        """
        Ensure we can create, update and delete folder permissions with a single request.
        """
        ProjectMembership.objects.create(
            project=self.project_1,
            member=initial_users["user_2"],
            is_project_admin=True,
            can_create_folders=True,
        )

        folder_users = [
            {
                "email": initial_users["user_1"].email,
                "is_folder_admin": True,
                "is_metadata_template_admin": True,
                "can_edit": True,
            },
            {
                "email": initial_users["user_2"].email,
                "is_folder_admin": True,
                "is_metadata_template_admin": False,
                "can_edit": False,
            },
            {
                "email": "unknown@test.local",
                "is_folder_admin": False,
                "is_metadata_template_admin": False,
                "can_edit": False,
            },
        ]

        folder_1 = self.project_1.folder.first()

        url = reverse(
            "folder-detail",
            kwargs={
                "pk": folder_1.pk,
            },
        )
        action_url = f"{url}permissions/"

        response = client.post(
            action_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

        response = client.patch(
            action_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

        assert ProjectMembership.objects.filter(project=self.project_1).count() == 2
        assert FolderPermission.objects.filter(folder=folder_1).count() == 2
        assert not User.objects.filter(email="unknown@test.local").exists()

        response = client.put(
            action_url,
            {
                "folder_users": folder_users,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert User.objects.filter(email="unknown@test.local").exists()
        assert len(response.data) == 3
        assert ProjectMembership.objects.filter(project=self.project_1).count() == 3
        assert (
            ProjectMembership.objects.filter(
                project=self.project_1,
                member__email__in=[user["email"] for user in folder_users],
            ).count()
            == 3
        )
        assert FolderPermission.objects.filter(folder=folder_1).count() == 3
        assert (
            FolderPermission.objects.filter(
                folder=folder_1,
                project_membership__member__email__in=[user["email"] for user in folder_users],
            ).count()
            == 3
        )

        project_membership_1 = ProjectMembership.objects.get(
            project=self.project_1,
            member__email=folder_users[0]["email"],
        )
        assert project_membership_1.is_project_admin is True
        assert project_membership_1.is_metadata_template_admin is True
        assert project_membership_1.can_create_folders is True

        folder_permission_1 = FolderPermission.objects.get(
            project_membership__member__email=folder_users[0]["email"],
            folder=folder_1,
        )
        assert folder_permission_1.is_folder_admin is True
        assert folder_permission_1.is_metadata_template_admin is True
        assert folder_permission_1.can_edit is True

        project_membership_2 = ProjectMembership.objects.get(
            project=self.project_1,
            member__email=folder_users[1]["email"],
        )
        assert project_membership_2.is_project_admin is True
        assert project_membership_2.is_metadata_template_admin is True
        assert project_membership_2.can_create_folders is True

        folder_permission_2 = FolderPermission.objects.get(
            project_membership__member__email=folder_users[1]["email"],
            folder=folder_1,
        )
        assert folder_permission_2.is_folder_admin is True
        assert folder_permission_2.is_metadata_template_admin is True
        assert folder_permission_2.can_edit is True

        project_membership_3 = ProjectMembership.objects.get(
            project=self.project_1,
            member__email=folder_users[2]["email"],
        )
        assert project_membership_3.is_project_admin is False
        assert project_membership_3.is_metadata_template_admin is False
        assert project_membership_3.can_create_folders is False

        folder_permission_3 = FolderPermission.objects.get(
            project_membership__member__email=folder_users[2]["email"],
            folder=folder_1,
        )
        assert folder_permission_3.is_folder_admin is False
        assert folder_permission_3.is_metadata_template_admin is False
        assert folder_permission_3.can_edit is False

        auth_user(client, initial_users["user_2"])

        url = reverse("folder-list")

        response = client.post(
            url,
            {
                "name": "Folder 2",
                "project": str(self.project_1.pk),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        folder_2 = Folder.objects.get(pk=response.data["pk"])

        url = reverse(
            "folder-detail",
            kwargs={
                "pk": folder_2.pk,
            },
        )
        action_url = f"{url}permissions/"

        assert Folder.objects.filter(project=self.project_1).count() == 2
        assert FolderPermission.objects.filter(folder=folder_2).count() == 2
        assert (
            FolderPermission.objects.filter(
                folder=folder_2,
                project_membership__member__email__in=[
                    folder_users[0]["email"],
                    folder_users[1]["email"],
                ],
            ).count()
            == 2
        )

        UploadsDataset.objects.create(
            name="Dataset 1",
            folder=folder_2,
        )

        auth_user(client, initial_users["user_1"])

        response = client.put(
            action_url,
            {
                "folder_users": [
                    folder_users[0],
                    folder_users[2],
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 3
        assert ProjectMembership.objects.filter(project=self.project_1).count() == 3
        assert (
            ProjectMembership.objects.filter(
                project=self.project_1,
                member__email__in=[
                    folder_users[0]["email"],
                    folder_users[1]["email"],
                    folder_users[2]["email"],
                ],
            ).count()
            == 3
        )
        assert FolderPermission.objects.filter(folder=folder_2).count() == 3
        assert (
            FolderPermission.objects.filter(
                folder=folder_2,
                project_membership__member__email__in=[
                    folder_users[0]["email"],
                    folder_users[1]["email"],
                    folder_users[2]["email"],
                ],
            ).count()
            == 3
        )

        project_membership_1.refresh_from_db()
        assert project_membership_1.is_project_admin is True
        assert project_membership_1.is_metadata_template_admin is True
        assert project_membership_1.can_create_folders is True

        folder_permission_1 = FolderPermission.objects.get(
            project_membership__member__email=folder_users[0]["email"],
            folder=folder_2,
        )
        assert folder_permission_1.is_folder_admin is True
        assert folder_permission_1.is_metadata_template_admin is True
        assert folder_permission_1.can_edit is True

        project_membership_2.refresh_from_db()
        assert project_membership_2.is_project_admin is True
        assert project_membership_2.is_metadata_template_admin is True
        assert project_membership_2.can_create_folders is True

        folder_permission_2 = FolderPermission.objects.get(
            project_membership__member__email=folder_users[1]["email"],
            folder=folder_2,
        )
        assert folder_permission_2.is_folder_admin is True
        assert folder_permission_2.is_metadata_template_admin is True
        assert folder_permission_2.can_edit is True

        project_membership_3.refresh_from_db()
        assert project_membership_3.is_project_admin is False
        assert project_membership_3.is_metadata_template_admin is False
        assert project_membership_3.can_create_folders is False

        folder_permission_3 = FolderPermission.objects.get(
            project_membership__member__email=folder_users[2]["email"],
            folder=folder_2,
        )
        assert folder_permission_3.is_folder_admin is False
        assert folder_permission_3.is_metadata_template_admin is False
        assert folder_permission_3.can_edit is False

        response = client.put(
            action_url,
            {
                "folder_users": [],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2
        assert ProjectMembership.objects.filter(project=self.project_1).count() == 3
        assert (
            ProjectMembership.objects.filter(
                project=self.project_1,
                member__email__in=[
                    folder_users[0]["email"],
                    folder_users[1]["email"],
                    folder_users[2]["email"],
                ],
            ).count()
            == 3
        )
        assert FolderPermission.objects.filter(folder=folder_2).count() == 2
        assert (
            FolderPermission.objects.filter(
                folder=folder_2,
                project_membership__member__email__in=[
                    folder_users[0]["email"],
                    folder_users[1]["email"],
                ],
            ).count()
            == 2
        )

        project_membership_1.refresh_from_db()
        assert project_membership_1.is_project_admin is True
        assert project_membership_1.is_metadata_template_admin is True
        assert project_membership_1.can_create_folders is True

        folder_permission_1 = FolderPermission.objects.get(
            project_membership__member__email=folder_users[0]["email"],
            folder=folder_2,
        )
        assert folder_permission_1.is_folder_admin is True
        assert folder_permission_1.is_metadata_template_admin is True
        assert folder_permission_1.can_edit is True

        project_membership_2.refresh_from_db()
        assert project_membership_2.is_project_admin is True
        assert project_membership_2.is_metadata_template_admin is True
        assert project_membership_2.can_create_folders is True

        folder_permission_2 = FolderPermission.objects.get(
            project_membership__member__email=folder_users[1]["email"],
            folder=folder_2,
        )
        assert folder_permission_2.is_folder_admin is True
        assert folder_permission_2.is_metadata_template_admin is True
        assert folder_permission_2.can_edit is True

        project_membership_3.refresh_from_db()
        assert project_membership_3.is_project_admin is False
        assert project_membership_3.is_metadata_template_admin is False
        assert project_membership_3.can_create_folders is False

        assert not FolderPermission.objects.filter(
            project_membership__member__email=folder_users[2]["email"],
            folder=folder_2,
        ).exists()

        response = client.put(
            action_url,
            {
                "folder_users": [
                    folder_users[2],
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 3
        assert ProjectMembership.objects.filter(project=self.project_1).count() == 3
        assert (
            ProjectMembership.objects.filter(
                project=self.project_1,
                member__email__in=[
                    folder_users[0]["email"],
                    folder_users[1]["email"],
                    folder_users[2]["email"],
                ],
            ).count()
            == 3
        )
        assert FolderPermission.objects.filter(folder=folder_2).count() == 3
        assert (
            FolderPermission.objects.filter(
                folder=folder_2,
                project_membership__member__email__in=[
                    folder_users[0]["email"],
                    folder_users[1]["email"],
                    folder_users[2]["email"],
                ],
            ).count()
            == 3
        )

        project_membership_1.refresh_from_db()
        assert project_membership_1.is_project_admin is True
        assert project_membership_1.is_metadata_template_admin is True
        assert project_membership_1.can_create_folders is True

        folder_permission_1 = FolderPermission.objects.get(
            project_membership__member__email=folder_users[0]["email"],
            folder=folder_2,
        )
        assert folder_permission_1.is_folder_admin is True
        assert folder_permission_1.is_metadata_template_admin is True
        assert folder_permission_1.can_edit is True

        project_membership_2.refresh_from_db()
        assert project_membership_2.is_project_admin is True
        assert project_membership_2.is_metadata_template_admin is True
        assert project_membership_2.can_create_folders is True

        folder_permission_2 = FolderPermission.objects.get(
            project_membership__member__email=folder_users[1]["email"],
            folder=folder_2,
        )
        assert folder_permission_2.is_folder_admin is True
        assert folder_permission_2.is_metadata_template_admin is True
        assert folder_permission_2.can_edit is True

        project_membership_3.refresh_from_db()
        assert project_membership_3.is_project_admin is False
        assert project_membership_3.is_metadata_template_admin is False
        assert project_membership_3.can_create_folders is False

        folder_permission_3 = FolderPermission.objects.get(
            project_membership__member__email=folder_users[2]["email"],
            folder=folder_2,
        )
        assert folder_permission_3.is_folder_admin is False
        assert folder_permission_3.is_metadata_template_admin is False
        assert folder_permission_3.can_edit is False

        response = client.put(
            action_url,
            {
                "folder_users": [
                    {
                        "email": folder_users[0]["email"],
                        "is_folder_admin": False,
                        "is_metadata_template_admin": False,
                        "can_edit": False,
                    },
                    folder_users[2],
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 3
        assert ProjectMembership.objects.filter(project=self.project_1).count() == 3
        assert (
            ProjectMembership.objects.filter(
                project=self.project_1,
                member__email__in=[
                    folder_users[0]["email"],
                    folder_users[1]["email"],
                    folder_users[2]["email"],
                ],
            ).count()
            == 3
        )
        assert FolderPermission.objects.filter(folder=folder_2).count() == 3
        assert (
            FolderPermission.objects.filter(
                folder=folder_2,
                project_membership__member__email__in=[
                    folder_users[0]["email"],
                    folder_users[1]["email"],
                    folder_users[2]["email"],
                ],
            ).count()
            == 3
        )

        project_membership_1.refresh_from_db()
        assert project_membership_1.is_project_admin is True
        assert project_membership_1.is_metadata_template_admin is True
        assert project_membership_1.can_create_folders is True

        folder_permission_1 = FolderPermission.objects.get(
            project_membership__member__email=folder_users[0]["email"],
            folder=folder_2,
        )
        assert folder_permission_1.is_folder_admin is True
        assert folder_permission_1.is_metadata_template_admin is True
        assert folder_permission_1.can_edit is True

        project_membership_2.refresh_from_db()
        assert project_membership_2.is_project_admin is True
        assert project_membership_2.is_metadata_template_admin is True
        assert project_membership_2.can_create_folders is True

        folder_permission_2 = FolderPermission.objects.get(
            project_membership__member__email=folder_users[1]["email"],
            folder=folder_2,
        )
        assert folder_permission_2.is_folder_admin is True
        assert folder_permission_2.is_metadata_template_admin is True
        assert folder_permission_2.can_edit is True

        project_membership_3.refresh_from_db()
        assert project_membership_3.is_project_admin is False
        assert project_membership_3.is_metadata_template_admin is False
        assert project_membership_3.can_create_folders is False

        folder_permission_3 = FolderPermission.objects.get(
            project_membership__member__email=folder_users[2]["email"],
            folder=folder_2,
        )
        assert folder_permission_3.is_folder_admin is False
        assert folder_permission_3.is_metadata_template_admin is False
        assert folder_permission_3.can_edit is False

        response = client.put(
            action_url,
            {
                "folder_users": [
                    folder_users[0],
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2
        assert ProjectMembership.objects.filter(project=self.project_1).count() == 3
        assert (
            ProjectMembership.objects.filter(
                project=self.project_1,
                member__email__in=[
                    folder_users[0]["email"],
                    folder_users[1]["email"],
                    folder_users[2]["email"],
                ],
            ).count()
            == 3
        )
        assert FolderPermission.objects.filter(folder=folder_2).count() == 2
        assert (
            FolderPermission.objects.filter(
                folder=folder_2,
                project_membership__member__email__in=[
                    folder_users[0]["email"],
                    folder_users[1]["email"],
                ],
            ).count()
            == 2
        )

        project_membership_1.refresh_from_db()
        assert project_membership_1.is_project_admin is True
        assert project_membership_1.is_metadata_template_admin is True
        assert project_membership_1.can_create_folders is True

        folder_permission_1 = FolderPermission.objects.get(
            project_membership__member__email=folder_users[0]["email"],
            folder=folder_2,
        )
        assert folder_permission_1.is_folder_admin is True
        assert folder_permission_1.is_metadata_template_admin is True
        assert folder_permission_1.can_edit is True

        project_membership_2.refresh_from_db()
        assert project_membership_2.is_project_admin is True
        assert project_membership_2.is_metadata_template_admin is True
        assert project_membership_2.can_create_folders is True

        folder_permission_2 = FolderPermission.objects.get(
            project_membership__member__email=folder_users[1]["email"],
            folder=folder_2,
        )
        assert folder_permission_2.is_folder_admin is True
        assert folder_permission_2.is_metadata_template_admin is True
        assert folder_permission_2.can_edit is True

        project_membership_3.refresh_from_db()
        assert project_membership_3.is_project_admin is False
        assert project_membership_3.is_metadata_template_admin is False
        assert project_membership_3.can_create_folders is False

        assert not FolderPermission.objects.filter(
            project_membership__member__email=folder_users[2]["email"],
            folder=folder_2,
        ).exists()

        response = client.put(
            action_url,
            {
                "folder_users": [
                    {
                        "email": initial_users["project_admin_user"].email,
                        "is_folder_admin": True,
                        "is_metadata_template_admin": True,
                        "can_edit": True,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert ProjectMembership.objects.filter(project=self.project_1).count() == 4
        assert (
            ProjectMembership.objects.filter(
                project=self.project_1,
                member__email__in=[
                    folder_users[0]["email"],
                    folder_users[1]["email"],
                    folder_users[2]["email"],
                    initial_users["project_admin_user"].email,
                ],
            ).count()
            == 4
        )
        assert FolderPermission.objects.filter(folder=folder_2).count() == 3
        assert (
            FolderPermission.objects.filter(
                folder=folder_2,
                project_membership__member__email__in=[
                    folder_users[0]["email"],
                    folder_users[1]["email"],
                    initial_users["project_admin_user"].email,
                ],
            ).count()
            == 3
        )

        project_membership_1.refresh_from_db()
        assert project_membership_1.is_project_admin is True
        assert project_membership_1.is_metadata_template_admin is True
        assert project_membership_1.can_create_folders is True

        folder_permission_1 = FolderPermission.objects.get(
            project_membership__member__email=folder_users[0]["email"],
            folder=folder_2,
        )
        assert folder_permission_1.is_folder_admin is True
        assert folder_permission_1.is_metadata_template_admin is True
        assert folder_permission_1.can_edit is True

        project_membership_2.refresh_from_db()
        assert project_membership_2.is_project_admin is True
        assert project_membership_2.is_metadata_template_admin is True
        assert project_membership_2.can_create_folders is True

        folder_permission_2 = FolderPermission.objects.get(
            project_membership__member__email=folder_users[1]["email"],
            folder=folder_2,
        )
        assert folder_permission_2.is_folder_admin is True
        assert folder_permission_2.is_metadata_template_admin is True
        assert folder_permission_2.can_edit is True

        project_membership_3.refresh_from_db()
        assert project_membership_3.is_project_admin is False
        assert project_membership_3.is_metadata_template_admin is False
        assert project_membership_3.can_create_folders is False

        assert not FolderPermission.objects.filter(
            project_membership__member__email=folder_users[2]["email"],
            folder=folder_2,
        ).exists()

        project_membership_4 = ProjectMembership.objects.get(
            project=self.project_1,
            member__email=initial_users["project_admin_user"].email,
        )
        assert project_membership_4.is_project_admin is False
        assert project_membership_4.is_metadata_template_admin is False
        assert project_membership_4.can_create_folders is False

        folder_permission_4 = FolderPermission.objects.get(
            project_membership__member__email=initial_users["project_admin_user"].email,
            folder=folder_2,
        )
        assert folder_permission_4.is_folder_admin is True
        assert folder_permission_4.is_metadata_template_admin is True
        assert folder_permission_4.can_edit is True

        user_2 = User.objects.get(email=folder_users[2]["email"])
        user_2.set_password("password")
        user_2.is_active = True
        user_2.save()

        auth_user(client, user_2)

        response = client.put(
            action_url,
            {
                "folder_users": folder_users,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

        auth_user(client, initial_users["project_admin_user"])

        response = client.put(
            action_url,
            {
                "folder_users": folder_users,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert ProjectMembership.objects.filter(project=self.project_1).count() == 4
        assert (
            ProjectMembership.objects.filter(
                project=self.project_1,
                member__email__in=[
                    folder_users[0]["email"],
                    folder_users[1]["email"],
                    folder_users[2]["email"],
                    initial_users["project_admin_user"].email,
                ],
            ).count()
            == 4
        )
        assert FolderPermission.objects.filter(folder=folder_2).count() == 3
        assert (
            FolderPermission.objects.filter(
                folder=folder_2,
                project_membership__member__email__in=[
                    folder_users[0]["email"],
                    folder_users[1]["email"],
                    folder_users[2]["email"],
                ],
            ).count()
            == 3
        )

        project_membership_1.refresh_from_db()
        assert project_membership_1.is_project_admin is True
        assert project_membership_1.is_metadata_template_admin is True
        assert project_membership_1.can_create_folders is True

        folder_permission_1 = FolderPermission.objects.get(
            project_membership__member__email=folder_users[0]["email"],
            folder=folder_2,
        )
        assert folder_permission_1.is_folder_admin is True
        assert folder_permission_1.is_metadata_template_admin is True
        assert folder_permission_1.can_edit is True

        project_membership_2.refresh_from_db()
        assert project_membership_2.is_project_admin is True
        assert project_membership_2.is_metadata_template_admin is True
        assert project_membership_2.can_create_folders is True

        folder_permission_2 = FolderPermission.objects.get(
            project_membership__member__email=folder_users[1]["email"],
            folder=folder_2,
        )
        assert folder_permission_2.is_folder_admin is True
        assert folder_permission_2.is_metadata_template_admin is True
        assert folder_permission_2.can_edit is True

        project_membership_3.refresh_from_db()
        assert project_membership_3.is_project_admin is False
        assert project_membership_3.is_metadata_template_admin is False
        assert project_membership_3.can_create_folders is False

        folder_permission_3 = FolderPermission.objects.get(
            project_membership__member__email=folder_users[2]["email"],
            folder=folder_2,
        )
        assert folder_permission_3.is_folder_admin is False
        assert folder_permission_3.is_metadata_template_admin is False
        assert folder_permission_3.can_edit is False

        project_membership_4.refresh_from_db()
        assert project_membership_4.is_project_admin is False
        assert project_membership_4.is_metadata_template_admin is False
        assert project_membership_4.can_create_folders is False

        assert not FolderPermission.objects.filter(
            project_membership__member__email=initial_users["project_admin_user"].email,
            folder=folder_2,
        ).exists()

        auth_user(client, initial_users["regular_user"])

        response = client.put(
            action_url,
            {
                "folder_users": folder_users,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

        auth_user(client, initial_users["user_1"])

        response = client.put(
            action_url,
            {
                "folder_users": [
                    folder_users[0],
                    folder_users[1],
                    {
                        "email": folder_users[2]["email"],
                        "is_folder_admin": True,
                        "is_metadata_template_admin": False,
                        "can_edit": True,
                    },
                    {
                        "email": initial_users["project_admin_user"].email,
                        "is_folder_admin": True,
                        "is_metadata_template_admin": False,
                        "can_edit": True,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert ProjectMembership.objects.filter(project=self.project_1).count() == 4
        assert (
            ProjectMembership.objects.filter(
                project=self.project_1,
                member__email__in=[
                    folder_users[0]["email"],
                    folder_users[1]["email"],
                    folder_users[2]["email"],
                    initial_users["project_admin_user"].email,
                ],
            ).count()
            == 4
        )
        assert FolderPermission.objects.filter(folder=folder_2).count() == 4
        assert (
            FolderPermission.objects.filter(
                folder=folder_2,
                project_membership__member__email__in=[
                    folder_users[0]["email"],
                    folder_users[1]["email"],
                    folder_users[2]["email"],
                    initial_users["project_admin_user"].email,
                ],
            ).count()
            == 4
        )

        project_membership_1.refresh_from_db()
        assert project_membership_1.is_project_admin is True
        assert project_membership_1.is_metadata_template_admin is True
        assert project_membership_1.can_create_folders is True

        folder_permission_1 = FolderPermission.objects.get(
            project_membership__member__email=folder_users[0]["email"],
            folder=folder_2,
        )
        assert folder_permission_1.is_folder_admin is True
        assert folder_permission_1.is_metadata_template_admin is True
        assert folder_permission_1.can_edit is True

        project_membership_2.refresh_from_db()
        assert project_membership_2.is_project_admin is True
        assert project_membership_2.is_metadata_template_admin is True
        assert project_membership_2.can_create_folders is True

        folder_permission_2 = FolderPermission.objects.get(
            project_membership__member__email=folder_users[1]["email"],
            folder=folder_2,
        )
        assert folder_permission_2.is_folder_admin is True
        assert folder_permission_2.is_metadata_template_admin is True
        assert folder_permission_2.can_edit is True

        project_membership_3.refresh_from_db()
        assert project_membership_3.is_project_admin is False
        assert project_membership_3.is_metadata_template_admin is False
        assert project_membership_3.can_create_folders is False

        folder_permission_3 = FolderPermission.objects.get(
            project_membership__member__email=folder_users[2]["email"],
            folder=folder_2,
        )
        assert folder_permission_3.is_folder_admin is True
        assert folder_permission_3.is_metadata_template_admin is True
        assert folder_permission_3.can_edit is True

        project_membership_4.refresh_from_db()
        assert project_membership_4.is_project_admin is False
        assert project_membership_4.is_metadata_template_admin is False
        assert project_membership_4.can_create_folders is False

        folder_permission_4 = FolderPermission.objects.get(
            project_membership__member__email=initial_users["project_admin_user"].email,
            folder=folder_2,
        )
        assert folder_permission_4.is_folder_admin is True
        assert folder_permission_4.is_metadata_template_admin is True
        assert folder_permission_4.can_edit is True

        response = client.put(
            action_url,
            {
                "folder_users": [
                    {
                        "email": folder_users[0]["email"],
                        "is_folder_admin": True,
                        "is_metadata_template_admin": False,
                        "can_edit": False,
                    },
                    {
                        "email": folder_users[2]["email"],
                        "is_folder_admin": False,
                        "is_metadata_template_admin": True,
                        "can_edit": True,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert ProjectMembership.objects.filter(project=self.project_1).count() == 4
        assert (
            ProjectMembership.objects.filter(
                project=self.project_1,
                member__email__in=[
                    folder_users[0]["email"],
                    folder_users[1]["email"],
                    folder_users[2]["email"],
                    initial_users["project_admin_user"].email,
                ],
            ).count()
            == 4
        )
        assert FolderPermission.objects.filter(folder=folder_2).count() == 3
        assert (
            FolderPermission.objects.filter(
                folder=folder_2,
                project_membership__member__email__in=[
                    folder_users[0]["email"],
                    folder_users[1]["email"],
                    folder_users[2]["email"],
                ],
            ).count()
            == 3
        )

        project_membership_1.refresh_from_db()
        assert project_membership_1.is_project_admin is True
        assert project_membership_1.is_metadata_template_admin is True
        assert project_membership_1.can_create_folders is True

        folder_permission_1 = FolderPermission.objects.get(
            project_membership__member__email=folder_users[0]["email"],
            folder=folder_2,
        )
        assert folder_permission_1.is_folder_admin is True
        assert folder_permission_1.is_metadata_template_admin is True
        assert folder_permission_1.can_edit is True

        project_membership_2.refresh_from_db()
        assert project_membership_2.is_project_admin is True
        assert project_membership_2.is_metadata_template_admin is True
        assert project_membership_2.can_create_folders is True

        folder_permission_1 = FolderPermission.objects.get(
            project_membership__member__email=folder_users[1]["email"],
            folder=folder_2,
        )
        assert folder_permission_1.is_folder_admin is True
        assert folder_permission_1.is_metadata_template_admin is True
        assert folder_permission_1.can_edit is True

        project_membership_3.refresh_from_db()
        assert project_membership_3.is_project_admin is False
        assert project_membership_3.is_metadata_template_admin is False
        assert project_membership_3.can_create_folders is False

        folder_permission_3 = FolderPermission.objects.get(
            project_membership__member__email=folder_users[2]["email"],
            folder=folder_2,
        )
        assert folder_permission_3.is_folder_admin is False
        assert folder_permission_3.is_metadata_template_admin is True
        assert folder_permission_3.can_edit is True

        project_membership_4 = ProjectMembership.objects.get(
            project=self.project_1,
            member__email=initial_users["project_admin_user"].email,
        )
        assert project_membership_4.is_project_admin is False
        assert project_membership_4.is_metadata_template_admin is False
        assert project_membership_4.can_create_folders is False

        assert not FolderPermission.objects.filter(
            project_membership__member__email=initial_users["project_admin_user"].email,
            folder=folder_2,
        )

    def test_metadata_templates_count(self, initial_users):
        """
        Ensure we get an accurate count of all metadata templates assigned to a folder.
        """
        folder_1 = self.project_1.folders.first()

        assert MetadataTemplateContainer.objects.count() == 1
        assert MetadataTemplate.objects.count() == 1
        assert folder_1.metadata_templates_count == 1

        metadata_template_container_1 = MetadataTemplateContainer.objects.create(
            name="Global metadata template container 1",
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_1,
        )

        folder_1.refresh_from_db()
        assert MetadataTemplateContainer.objects.count() == 2
        assert MetadataTemplate.objects.count() == 2
        assert folder_1.metadata_templates_count == 1

        metadata_template_container_2 = MetadataTemplateContainer.objects.create(
            name="Project metadata template container 1",
            assigned_to_content_type=self.project_1.get_content_type(),
            assigned_to_object_id=self.project_1.pk,
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_2,
        )

        folder_1.refresh_from_db()
        assert MetadataTemplateContainer.objects.count() == 3
        assert MetadataTemplate.objects.count() == 3
        assert folder_1.metadata_templates_count == 2

        metadata_template_container_3 = MetadataTemplateContainer.objects.create(
            name="Folder metadata template container 1",
            assigned_to_content_type=folder_1.get_content_type(),
            assigned_to_object_id=folder_1.pk,
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_3,
        )

        folder_1.refresh_from_db()
        assert MetadataTemplateContainer.objects.count() == 4
        assert MetadataTemplate.objects.count() == 4
        assert folder_1.metadata_templates_count == 3

    def test_metadata_template_container_pool(self, client, initial_users):
        """
        Ensure we can set and get the folder's metadata template container pool.
        """
        url = reverse(
            "folder-detail",
            kwargs={
                "pk": self.project_1.folder.first().pk,
            },
        )
        action_url = f"{url}metadata-template-container-pool/"

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0

        response = client.put(
            action_url,
            {
                "metadata_template_container": [
                    "8530f9ea-d04a-40b1-b502-82b9cdc78727",
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

        response = client.put(
            action_url,
            {
                "metadata_template_container": [
                    self.metadata_template_container_1.pk,
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        metadata_template_container_2 = MetadataTemplateContainer.objects.create(
            name="Metadata Template Container 2",
        )

        metadata_template_2 = MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_2,
        )

        response = client.put(
            action_url,
            {
                "metadata_template_container": [
                    metadata_template_container_2.pk,
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        response = client.put(
            action_url,
            {
                "metadata_template_container": [
                    self.metadata_template_container_1.pk,
                    metadata_template_container_2.pk,
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

        response = client.put(
            action_url,
            {
                "metadata_template_container": [],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0

        self.project_1.metadata_template = self.metadata_template_1
        self.project_1.save()

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        self.folder_1.metadata_template = metadata_template_2
        self.folder_1.save()

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

        self.folder_1.metadata_template = None
        self.folder_1.save()

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        self.project_1.metadata_template = None
        self.project_1.save()

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0


@pytest.mark.django_db
class TestFolderPermissionAPI:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.project_1 = Project.objects.create(
            name="Project 1",
        )

    def test_read_folder_permissions_list(self, client, initial_users):
        """
        Ensure we can read the folder permission list.
        """
        url = reverse("folder-permission-list")

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert ProjectMembership.objects.count() == 1
        assert FolderPermission.objects.count() == 1

        project_membership_2 = ProjectMembership.objects.create(
            project=self.project_1,
            member=initial_users["user_2"],
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert ProjectMembership.objects.count() == 2
        assert FolderPermission.objects.count() == 1

        url_filter = f"{url}?project_membership__member={initial_users['user_1'].pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert ProjectMembership.objects.count() == 2
        assert response.data[0]["project_membership"]["member"]["pk"] == initial_users["user_1"].pk
        assert FolderPermission.objects.count() == 1

        url_filter = f"{url}?project_membership__member__email={initial_users['user_1'].email}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert ProjectMembership.objects.count() == 2
        assert response.data[0]["project_membership"]["member"]["pk"] == initial_users["user_1"].pk
        assert FolderPermission.objects.count() == 1

        url_filter = f"{url}?project_membership__member={FolderPermissionFilter.Member.ME}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert ProjectMembership.objects.count() == 2
        assert response.data[0]["project_membership"]["member"]["pk"] == initial_users["user_1"].pk
        assert FolderPermission.objects.count() == 1

        FolderPermission.objects.create(
            folder=self.project_1.folders.first(),
            project_membership=project_membership_2,
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2
        assert ProjectMembership.objects.count() == 2
        assert FolderPermission.objects.count() == 2

        url_filter = f"{url}?project_membership__member={initial_users['user_2'].pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert ProjectMembership.objects.count() == 2
        assert response.data[0]["project_membership"]["member"]["pk"] == initial_users["user_2"].pk
        assert FolderPermission.objects.count() == 2

        url_filter = f"{url}?project_membership__member="
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2
        assert ProjectMembership.objects.count() == 2
        assert FolderPermission.objects.count() == 2

        set_request_for_user(initial_users["user_1"])

        project_2 = Project.objects.create(
            name="Project 2",
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 3
        assert ProjectMembership.objects.count() == 3
        assert FolderPermission.objects.count() == 3

        url_filter = f"{url}?folder={self.project_1.folders.first().pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2
        assert ProjectMembership.objects.count() == 3
        assert FolderPermission.objects.count() == 3

        url_filter = f"{url}?folder={project_2.folders.first().pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert ProjectMembership.objects.count() == 3
        assert FolderPermission.objects.count() == 3

        url_filter = f"{url}?folder={self.project_1.folders.first().pk}&project_membership__member={FolderPermissionFilter.Member.ME}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert ProjectMembership.objects.count() == 3
        assert FolderPermission.objects.count() == 3

        url_filter = f"{url}?project_membership__member=invalid_value"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0
        assert ProjectMembership.objects.count() == 3
        assert FolderPermission.objects.count() == 3

    def test_folder_creation_permission(self, client, initial_users):
        # Create a project
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("project-list")
        response = client.post(
            url,
            {
                "name": "Project Name",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Project Name"
        assert response.data["members_count"] == 1

        project_id = response.data["pk"]

        # add a regular member who can create folders
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("project-membership-list")
        add_regular_response = client.post(
            url,
            {
                "member": initial_users["regular_user"].pk,
                "project": project_id,
                "is_project_admin": False,
                "can_create_folders": True,
            },
        )
        assert add_regular_response.status_code == status.HTTP_201_CREATED

        # add another regular member who can not create folders
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("project-membership-list")
        add_regular_response = client.post(
            url,
            {
                "member": initial_users["regular_user_2"].pk,
                "project": project_id,
                "is_project_admin": False,
                "can_create_folders": False,
            },
        )
        assert add_regular_response.status_code == status.HTTP_201_CREATED

        # Create a folder as admin
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("folder-list")
        response = client.post(
            url,
            {
                "name": "Folder Name",
                "project": project_id,
            },
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Folder Name"

        # Create a folder with permission
        auth_user(client, initial_users["regular_user"])
        url = reverse("folder-list")
        response = client.post(
            url,
            {
                "name": "Another Folder Name",
                "project": project_id,
            },
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Another Folder Name"

        # Try to create a folder without permission
        auth_user(client, initial_users["regular_user_2"])
        url = reverse("folder-list")
        response = client.post(
            url,
            {
                "name": "Unauthorized Folder Name",
                "project": project_id,
            },
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_add_folder_permission_permission(self, client, initial_users):
        # Create a project
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("project-list")
        response = client.post(
            url,
            {
                "name": "Project Name",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Project Name"
        assert response.data["members_count"] == 1

        project_id = response.data["pk"]

        # add a regular member
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("project-membership-list")
        regular_user_membership_response = client.post(
            url,
            {
                "member": initial_users["regular_user"].pk,
                "project": project_id,
                "is_project_admin": False,
                "can_create_folders": True,
            },
        )
        assert regular_user_membership_response.status_code == status.HTTP_201_CREATED

        # add another regular member
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("project-membership-list")
        regular_user_membership_response = client.post(
            url,
            {
                "member": initial_users["regular_user_2"].pk,
                "project": project_id,
                "is_project_admin": False,
                "can_create_folders": True,
            },
        )
        assert regular_user_membership_response.status_code == status.HTTP_201_CREATED

        # get the auto created "General" folder
        general_folder = Folder.objects.get(project=project_id)
        assert general_folder.name == _("General")

        # get folder permissions
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("folder-permission-list")
        folder_permissions = client.get(url)
        assert len(folder_permissions.data) == 1

        # add regular member to folder permission as folder admin
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("folder-permission-list")
        add_regular_response = client.post(
            url,
            {
                "folder": general_folder.pk,
                "member": initial_users["regular_user"].pk,
                "is_folder_admin": False,
                "can_edit": False,
            },
        )
        assert add_regular_response.status_code == status.HTTP_201_CREATED

        # get folder permissions
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("folder-permission-list")
        folder_permissions = client.get(url)
        assert len(folder_permissions.data) == 2

        # try to add another regular member to folder permission as a non admin
        auth_user(client, initial_users["regular_user"])
        url = reverse("folder-permission-list")
        add_regular_response = client.post(
            url,
            {
                "folder": general_folder.pk,
                "member": initial_users["regular_user_2"].pk,
                "is_folder_admin": False,
                "can_edit": False,
            },
        )
        assert add_regular_response.status_code == status.HTTP_403_FORBIDDEN

        # get folder permissions
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("folder-permission-list")
        folder_permissions = client.get(url)
        assert len(folder_permissions.data) == 2

    def test_folder_update_permission(self, client, initial_users):
        # Create a project
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("project-list")
        response = client.post(
            url,
            {
                "name": "Project Name",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Project Name"
        assert response.data["members_count"] == 1

        project_id = response.data["pk"]

        # add a regular member
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("project-membership-list")
        regular_user_membership_response = client.post(
            url,
            {
                "member": initial_users["regular_user"].pk,
                "project": project_id,
                "is_project_admin": False,
                "can_create_folders": True,
            },
        )
        assert regular_user_membership_response.status_code == status.HTTP_201_CREATED

        # add another regular member
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("project-membership-list")
        regular_user_membership_response = client.post(
            url,
            {
                "member": initial_users["regular_user_2"].pk,
                "project": project_id,
                "is_project_admin": False,
                "can_create_folders": True,
            },
        )
        assert regular_user_membership_response.status_code == status.HTTP_201_CREATED

        # get the auto created "General" folder
        general_folder = Folder.objects.filter(project=project_id).first()
        assert general_folder.name == _("General")

        # add regular member as folder admin
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("folder-permission-list")
        add_regular_response = client.post(
            url,
            {
                "folder": general_folder.pk,
                "member": initial_users["regular_user"].pk,
                "is_folder_admin": True,
                "can_edit": True,
            },
        )
        assert add_regular_response.status_code == status.HTTP_201_CREATED

        general_folder.unlock()

        # add another user without folder update rights
        auth_user(client, initial_users["regular_user"])
        url = reverse("folder-permission-list")
        add_regular_response = client.post(
            url,
            {
                "folder": general_folder.pk,
                "member": initial_users["regular_user_2"].pk,
                "is_folder_admin": False,
                "can_edit": False,
            },
        )
        assert add_regular_response.status_code == status.HTTP_201_CREATED

        general_folder.unlock()

        # update folder name as admin
        auth_user(client, initial_users["tum_member_user"])
        url = reverse(
            "folder-detail",
            kwargs={
                "pk": general_folder.pk,
            },
        )
        update_response = client.put(
            url,
            {
                "name": "Updated Folder Name",
                "project": project_id,
            },
        )
        assert update_response.status_code == status.HTTP_200_OK, "Admin user should be able to update the project"
        assert update_response.data["name"] == "Updated Folder Name"

        general_folder.unlock()

        # update folder name as another folder admin
        auth_user(client, initial_users["regular_user"])
        url = reverse(
            "folder-detail",
            kwargs={
                "pk": general_folder.pk,
            },
        )
        update_response = client.put(
            url,
            {
                "name": "Updated Folder Name Again",
                "project": project_id,
            },
        )
        assert update_response.status_code == status.HTTP_200_OK, "Admin user should be able to update the project"
        assert update_response.data["name"] == "Updated Folder Name Again"

        general_folder.unlock()

        # update folder name as another folder admin
        auth_user(client, initial_users["regular_user_2"])
        url = reverse(
            "folder-detail",
            kwargs={
                "pk": general_folder.pk,
            },
        )
        update_response = client.put(
            url,
            {
                "name": "Unauthorized Folder Name",
                "project": project_id,
            },
        )
        assert update_response.status_code == status.HTTP_403_FORBIDDEN

    def test_add_folder_permission_using_numeric_id_with_existing_membership(self, client, initial_users):
        # Create a project
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("project-list")
        response = client.post(
            url,
            {
                "name": "Project Name",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Project Name"
        assert response.data["members_count"] == 1

        project_id = response.data["pk"]

        # add a regular member
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("project-membership-list")
        regular_user_membership_response = client.post(
            url,
            {
                "member": initial_users["regular_user"].pk,
                "project": project_id,
                "is_project_admin": False,
                "can_create_folders": True,
            },
        )
        assert regular_user_membership_response.status_code == status.HTTP_201_CREATED

        # get the auto created "General" folder
        general_folder = Folder.objects.get(project=project_id)
        assert general_folder.name == _("General")
        assert general_folder.members_count == 1

        # add regular member to folder permission as folder admin
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("folder-permission-list")
        add_regular_response = client.post(
            url,
            {
                "folder": general_folder.pk,
                "member": initial_users["regular_user"].pk,
                "is_folder_admin": False,
                "can_edit": False,
            },
        )
        assert add_regular_response.status_code == status.HTTP_201_CREATED
        general_folder = Folder.objects.get(project=project_id)
        assert general_folder.members_count == 2

    def test_add_folder_permission_using_email_with_existing_membership(self, client, initial_users):
        # Create a project
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("project-list")
        response = client.post(
            url,
            {
                "name": "Project Name",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Project Name"
        assert response.data["members_count"] == 1

        project_id = response.data["pk"]

        # add a regular member
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("project-membership-list")
        regular_user_membership_response = client.post(
            url,
            {
                "member": initial_users["regular_user"].email,
                "project": project_id,
                "is_project_admin": False,
                "can_create_folders": True,
            },
        )
        assert regular_user_membership_response.status_code == status.HTTP_201_CREATED

        # get the auto created "General" folder
        general_folder = Folder.objects.get(project=project_id)
        assert general_folder.name == _("General")
        assert general_folder.members_count == 1

        # add regular member to folder permission as folder admin
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("folder-permission-list")
        add_regular_response = client.post(
            url,
            {
                "folder": general_folder.pk,
                "member": initial_users["regular_user"].email,
                "is_folder_admin": False,
                "can_edit": False,
            },
        )
        assert add_regular_response.status_code == status.HTTP_201_CREATED
        general_folder = Folder.objects.get(project=project_id)
        assert general_folder.members_count == 2

    def test_add_folder_permission_using_numeric_id_without_existing_membership(self, client, initial_users):
        # Create a project
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("project-list")
        response = client.post(
            url,
            {
                "name": "Project Name",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Project Name"
        assert response.data["members_count"] == 1

        project_id = response.data["pk"]

        # get the auto created "General" folder
        general_folder = Folder.objects.get(project=project_id)
        assert general_folder.name == _("General")
        assert general_folder.members_count == 1

        # add regular member to folder permission as folder admin
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("folder-permission-list")
        add_regular_response = client.post(
            url,
            {
                "folder": general_folder.pk,
                "member": initial_users["regular_user"].pk,
                "is_project_admin": False,
                "can_create_folders": False,
                "is_folder_admin": False,
                "can_edit": False,
            },
        )
        assert add_regular_response.status_code == status.HTTP_201_CREATED
        general_folder = Folder.objects.get(project=project_id)
        assert general_folder.members_count == 2

    def test_add_folder_permission_using_email_without_existing_membership(self, client, initial_users):
        # Create a project
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("project-list")
        response = client.post(
            url,
            {
                "name": "Project Name",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Project Name"
        assert response.data["members_count"] == 1

        project_id = response.data["pk"]

        # get the auto created "General" folder
        general_folder = Folder.objects.get(project=project_id)
        assert general_folder.name == _("General")
        assert general_folder.members_count == 1

        # add regular member to folder permission as folder admin
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("folder-permission-list")
        add_regular_response = client.post(
            url,
            {
                "folder": general_folder.pk,
                "member": initial_users["regular_user"].email,
                "is_project_admin": False,
                "can_create_folders": False,
                "is_folder_admin": False,
                "can_edit": False,
            },
        )
        assert add_regular_response.status_code == status.HTTP_201_CREATED
        general_folder = Folder.objects.get(project=project_id)
        assert general_folder.members_count == 2

    def test_change_folder_permission_using_numeric_id(self, client, initial_users):
        # Create a project
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("project-list")
        response = client.post(
            url,
            {
                "name": "Project Name",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Project Name"
        assert response.data["members_count"] == 1

        project_id = response.data["pk"]

        # add a regular member
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("project-membership-list")
        regular_user_membership_response = client.post(
            url,
            {
                "member": initial_users["regular_user"].email,
                "project": project_id,
                "is_project_admin": False,
                "can_create_folders": True,
            },
        )
        assert regular_user_membership_response.status_code == status.HTTP_201_CREATED

        # get the auto created "General" folder
        general_folder = Folder.objects.get(project=project_id)
        assert general_folder.name == _("General")
        assert general_folder.members_count == 1

        # add regular member to folder permission as folder admin
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("folder-permission-list")
        add_regular_response = client.post(
            url,
            {
                "folder": general_folder.pk,
                "member": initial_users["regular_user"].email,
                "is_folder_admin": False,
                "can_edit": False,
            },
        )
        assert add_regular_response.status_code == status.HTTP_201_CREATED
        general_folder = Folder.objects.get(project=project_id)
        assert general_folder.members_count == 2

        assert not FolderPermission.objects.get(pk=add_regular_response.data["pk"]).is_folder_admin
        assert not FolderPermission.objects.get(pk=add_regular_response.data["pk"]).can_edit

        folder_permission = FolderPermission.objects.get(pk=add_regular_response.data["pk"])
        # update to add edit permission
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("folder-permission-detail", kwargs={"pk": folder_permission.pk})
        add_regular_response = client.put(
            url,
            {
                "is_folder_admin": False,
                "can_edit": True,
            },
        )
        assert add_regular_response.status_code == status.HTTP_200_OK
        assert not FolderPermission.objects.get(pk=add_regular_response.data["pk"]).is_folder_admin
        assert FolderPermission.objects.get(pk=add_regular_response.data["pk"]).can_edit

    def test_change_folder_permission_using_email(self, client, initial_users):
        # Create a project
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("project-list")
        response = client.post(
            url,
            {
                "name": "Project Name",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Project Name"
        assert response.data["members_count"] == 1

        project_id = response.data["pk"]

        # add a regular member
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("project-membership-list")
        regular_user_membership_response = client.post(
            url,
            {
                "member": initial_users["regular_user"].email,
                "project": project_id,
                "is_project_admin": False,
                "can_create_folders": True,
            },
        )
        assert regular_user_membership_response.status_code == status.HTTP_201_CREATED

        # get the auto created "General" folder
        general_folder = Folder.objects.get(project=project_id)
        assert general_folder.name == _("General")
        assert general_folder.members_count == 1

        # add regular member to folder permission as folder admin
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("folder-permission-list")
        add_regular_response = client.post(
            url,
            {
                "folder": general_folder.pk,
                "member": initial_users["regular_user"].email,
                "is_folder_admin": False,
                "can_edit": False,
            },
        )
        assert add_regular_response.status_code == status.HTTP_201_CREATED
        general_folder = Folder.objects.get(project=project_id)
        assert general_folder.members_count == 2

        assert not FolderPermission.objects.get(pk=add_regular_response.data["pk"]).is_folder_admin
        assert not FolderPermission.objects.get(pk=add_regular_response.data["pk"]).can_edit

        folder_permission = FolderPermission.objects.get(pk=add_regular_response.data["pk"])
        # update to add edit permission
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("folder-permission-detail", kwargs={"pk": folder_permission.pk})
        add_regular_response = client.put(
            url,
            {
                "is_folder_admin": False,
                "can_edit": True,
            },
        )
        assert add_regular_response.status_code == status.HTTP_200_OK
        assert not FolderPermission.objects.get(pk=add_regular_response.data["pk"]).is_folder_admin
        assert FolderPermission.objects.get(pk=add_regular_response.data["pk"]).can_edit

    def test_prevent_downgrade_of_project_admin(self, client, initial_users):
        auth_user(client, initial_users["tum_member_user"])

        url = reverse("project-list")

        response = client.post(
            url,
            {
                "name": "Project Name",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Project Name"
        assert response.data["members_count"] == 1

        project = response.data

        url = reverse("project-membership-list")

        response = client.post(
            url,
            {
                "member": initial_users["regular_user"].email,
                "project": project["pk"],
                "is_project_admin": True,
                "is_metadata_template_admin": True,
                "can_create_folders": True,
            },
        )
        assert response.status_code == status.HTTP_201_CREATED

        general_folder = Folder.objects.get(pk=project["folders"][0]["pk"])
        assert general_folder.name == _("General")
        assert general_folder.members_count == 2

        regular_folder_permission = FolderPermission.objects.get(
            project_membership__pk=response.data["pk"],
            folder=general_folder,
        )

        url = reverse(
            "folder-permission-detail",
            kwargs={
                "pk": regular_folder_permission.pk,
            },
        )

        response = client.put(
            url,
            {
                "is_folder_admin": False,
                "is_metadata_template_admin": False,
                "can_edit": False,
            },
        )
        assert response.status_code == status.HTTP_200_OK

        regular_folder_permission.refresh_from_db()

        assert regular_folder_permission.is_folder_admin is True
        assert regular_folder_permission.is_metadata_template_admin is True
        assert regular_folder_permission.can_edit is True

    def test_prevent_downgrade_of_project_metadata_template_admin(self, client, initial_users):
        url = reverse("project-list")

        response = client.post(
            url,
            {
                "name": "Project 1",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED

        project = Project.objects.get(pk=response.data["pk"])
        folder_1 = project.folders.first()

        assert ProjectMembership.objects.filter(project=project).count() == 1
        assert FolderPermission.objects.filter(project_membership__project=project).count() == 1

        url = reverse("project-membership-list")

        response = client.post(
            url,
            {
                "member": initial_users["regular_user"].email,
                "project": project.pk,
                "is_project_admin": False,
                "is_metadata_template_admin": True,
                "can_create_folders": False,
            },
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert ProjectMembership.objects.filter(project=project).count() == 2
        assert FolderPermission.objects.filter(project_membership__project=project).count() == 1

        project_membership = ProjectMembership.objects.get(pk=response.data["pk"])

        assert project_membership.is_project_admin is False
        assert project_membership.is_metadata_template_admin is True
        assert project_membership.can_create_folders is False

        url = reverse("folder-permission-list")

        response = client.post(
            url,
            {
                "folder": folder_1.pk,
                "member": initial_users["regular_user"].email,
                "is_folder_admin": False,
                "is_metadata_template_admin": True,
                "can_edit": False,
            },
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert FolderPermission.objects.filter(project_membership__project=project).count() == 2

        folder_permission = FolderPermission.objects.get(pk=response.data["pk"])

        assert folder_permission.is_folder_admin is False
        assert folder_permission.is_metadata_template_admin is True
        assert folder_permission.can_edit is False

        url = reverse(
            "folder-permission-detail",
            kwargs={
                "pk": folder_permission.pk,
            },
        )

        response = client.put(
            url,
            {
                "is_folder_admin": False,
                "is_metadata_template_admin": False,
                "can_edit": False,
            },
        )
        assert response.status_code == status.HTTP_200_OK

        folder_permission.refresh_from_db()

        assert folder_permission.is_folder_admin is False
        assert folder_permission.is_metadata_template_admin is True
        assert folder_permission.can_edit is False

        url = reverse(
            "project-membership-detail",
            kwargs={
                "pk": project_membership.pk,
            },
        )

        response = client.put(
            url,
            {
                "is_project_admin": False,
                "is_metadata_template_admin": False,
                "can_create_folders": False,
            },
        )
        assert response.status_code == status.HTTP_200_OK

        project_membership.refresh_from_db()

        assert project_membership.is_project_admin is False
        assert project_membership.is_metadata_template_admin is False
        assert project_membership.can_create_folders is False

        folder_permission.refresh_from_db()

        assert folder_permission.is_folder_admin is False
        assert folder_permission.is_metadata_template_admin is True
        assert folder_permission.can_edit is False

        url = reverse(
            "folder-permission-detail",
            kwargs={
                "pk": folder_permission.pk,
            },
        )

        response = client.put(
            url,
            {
                "is_folder_admin": False,
                "is_metadata_template_admin": False,
                "can_edit": False,
            },
        )
        assert response.status_code == status.HTTP_200_OK

        folder_permission.refresh_from_db()

        assert folder_permission.is_folder_admin is False
        assert folder_permission.is_metadata_template_admin is False
        assert folder_permission.can_edit is False

        url = reverse(
            "project-membership-detail",
            kwargs={
                "pk": project_membership.pk,
            },
        )

        response = client.put(
            url,
            {
                "is_project_admin": False,
                "is_metadata_template_admin": True,
                "can_create_folders": False,
            },
        )
        assert response.status_code == status.HTTP_200_OK

        project_membership.refresh_from_db()

        assert project_membership.is_project_admin is False
        assert project_membership.is_metadata_template_admin is True
        assert project_membership.can_create_folders is False

        folder_permission.refresh_from_db()

        assert folder_permission.is_folder_admin is False
        assert folder_permission.is_metadata_template_admin is True
        assert folder_permission.can_edit is False

        response = client.delete(url)
        assert response.status_code == status.HTTP_204_NO_CONTENT

        with pytest.raises(ProjectMembership.DoesNotExist):
            project_membership.refresh_from_db()

        with pytest.raises(FolderPermission.DoesNotExist):
            folder_permission.refresh_from_db()

    def test_metadata_template_admin_flag_on_folder_creation(self, client, initial_users):
        url = reverse("project-list")

        response = client.post(
            url,
            {
                "name": "Project 1",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED

        project = Project.objects.get(pk=response.data["pk"])
        folder_1 = project.folders.first()

        assert ProjectMembership.objects.filter(project=project).count() == 1
        assert FolderPermission.objects.filter(project_membership__project=project).count() == 1

        url = reverse("project-membership-list")

        response = client.post(
            url,
            {
                "member": initial_users["regular_user"].email,
                "project": project.pk,
                "is_project_admin": False,
                "is_metadata_template_admin": True,
                "can_create_folders": False,
            },
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert ProjectMembership.objects.filter(project=project).count() == 2
        assert FolderPermission.objects.filter(project_membership__project=project).count() == 1

        project_membership = ProjectMembership.objects.get(pk=response.data["pk"])

        assert project_membership.is_project_admin is False
        assert project_membership.is_metadata_template_admin is True
        assert project_membership.can_create_folders is False

        url = reverse("folder-permission-list")

        response = client.post(
            url,
            {
                "folder": folder_1.pk,
                "member": initial_users["regular_user"].email,
                "is_folder_admin": False,
                "is_metadata_template_admin": False,
                "can_edit": False,
            },
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert FolderPermission.objects.filter(project_membership__project=project).count() == 2

        folder_permission = FolderPermission.objects.get(pk=response.data["pk"])

        assert folder_permission.is_folder_admin is False
        assert folder_permission.is_metadata_template_admin is True
        assert folder_permission.can_edit is False

    def test_prevent_deletion_of_project_admin(self, client, initial_users):
        auth_user(client, initial_users["tum_member_user"])

        url = reverse("project-list")

        response = client.post(
            url,
            {
                "name": "Project Name",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Project Name"
        assert response.data["members_count"] == 1

        project = response.data

        set_request_for_user(initial_users["tum_member_user"])

        dataset = UploadsDataset.objects.create(
            folder_id=project["folders"][0]["pk"],
        )

        url = reverse("project-membership-list")

        response = client.post(
            url,
            {
                "member": initial_users["regular_user"].email,
                "project": project["pk"],
                "is_project_admin": True,
                "is_metadata_template_admin": True,
                "can_create_folders": True,
            },
        )
        assert response.status_code == status.HTTP_201_CREATED

        general_folder = Folder.objects.get(pk=project["folders"][0]["pk"])
        assert general_folder.name == _("General")
        assert general_folder.members_count == 2

        folder_permission = FolderPermission.objects.get(
            project_membership__pk=response.data["pk"],
            folder=general_folder,
        )

        url = reverse(
            "folder-permission-detail",
            kwargs={
                "pk": folder_permission.pk,
            },
        )

        response = client.delete(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN

        assert folder_permission.is_folder_admin is True
        assert folder_permission.is_metadata_template_admin is True
        assert folder_permission.can_edit is True

        set_request_for_user(initial_users["tum_member_user"])

        dataset.delete()

        response = client.delete(url)
        assert response.status_code == status.HTTP_204_NO_CONTENT

        with pytest.raises(FolderPermission.DoesNotExist):
            folder_permission.refresh_from_db()

    def test_prevent_downgrade_of_last_folder_admin(self, client, initial_users):
        auth_user(client, initial_users["tum_member_user"])

        url = reverse("project-list")

        response = client.post(
            url,
            {
                "name": "Project Name",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Project Name"
        assert response.data["members_count"] == 1

        project = response.data

        url = reverse("project-membership-list")

        response = client.post(
            url,
            {
                "member": initial_users["regular_user"].email,
                "project": project["pk"],
                "is_project_admin": False,
                "is_metadata_template_admin": False,
                "can_create_folders": False,
            },
        )
        assert response.status_code == status.HTTP_201_CREATED

        general_folder = Folder.objects.get(pk=project["folders"][0]["pk"])

        url = reverse("folder-permission-list")

        response = client.post(
            url,
            {
                "folder": general_folder.pk,
                "member": initial_users["regular_user"].email,
                "is_folder_admin": False,
                "is_metadata_template_admin": False,
                "can_edit": False,
            },
        )
        assert response.status_code == status.HTTP_201_CREATED

        general_folder.refresh_from_db()
        assert general_folder.members_count == 2

        admin_folder_permission = FolderPermission.objects.get(
            project_membership__member__pk=initial_users["tum_member_user"].pk,
            folder=general_folder,
        )

        url = reverse(
            "folder-permission-detail",
            kwargs={
                "pk": admin_folder_permission.pk,
            },
        )

        response = client.put(
            url,
            {
                "is_folder_admin": False,
                "is_metadata_template_admin": False,
                "can_edit": False,
            },
        )
        assert response.status_code == status.HTTP_200_OK

        admin_folder_permission.refresh_from_db()

        assert admin_folder_permission.is_folder_admin is True
        assert admin_folder_permission.is_metadata_template_admin is True
        assert admin_folder_permission.can_edit is True

    def test_prevent_deletion_of_last_folder_admin(self, client, initial_users):
        # Create a project
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("project-list")
        response = client.post(
            url,
            {
                "name": "Project Name",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Project Name"
        assert response.data["members_count"] == 1

        project = response.data

        # add a regular member
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("project-membership-list")
        regular_user_membership_response = client.post(
            url,
            {
                "member": initial_users["regular_user"].email,
                "project": project["pk"],
                "is_project_admin": False,
                "is_metadata_template_admin": False,
                "can_create_folders": False,
            },
        )
        assert regular_user_membership_response.status_code == status.HTTP_201_CREATED

        general_folder = Folder.objects.get(pk=project["folders"][0]["pk"])

        UploadsDataset.objects.create(folder=general_folder)

        # add regular member to folder permission
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("folder-permission-list")
        add_regular_response = client.post(
            url,
            {
                "folder": general_folder.pk,
                "member": initial_users["regular_user"].email,
                "is_folder_admin": False,
                "is_metadata_template_admin": False,
                "can_edit": False,
            },
        )
        assert add_regular_response.status_code == status.HTTP_201_CREATED

        general_folder.refresh_from_db()
        assert general_folder.members_count == 2

        admin_folder_permission = FolderPermission.objects.get(
            project_membership__member__pk=initial_users["tum_member_user"].pk,
            folder=general_folder,
        )

        assert admin_folder_permission.is_folder_admin
        assert admin_folder_permission.can_edit

        auth_user(client, initial_users["tum_member_user"])
        url = reverse("folder-permission-detail", kwargs={"pk": admin_folder_permission.pk})
        response = client.delete(url)

        assert response.status_code == status.HTTP_403_FORBIDDEN

        admin_folder_permission.refresh_from_db()

        assert admin_folder_permission.is_folder_admin is True
        assert admin_folder_permission.is_metadata_template_admin is True
        assert admin_folder_permission.can_edit is True

    def test_allow_deletion_of_last_folder_admin_with_empty_folder(self, client, initial_users):
        # Create a project
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("project-list")
        response = client.post(
            url,
            {
                "name": "Project Name",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "Project Name"
        assert response.data["members_count"] == 1

        project = response.data

        # add a regular member
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("project-membership-list")
        regular_user_membership_response = client.post(
            url,
            {
                "member": initial_users["regular_user"].email,
                "project": project["pk"],
                "is_project_admin": False,
                "is_metadata_template_admin": False,
                "can_create_folders": False,
            },
        )
        assert regular_user_membership_response.status_code == status.HTTP_201_CREATED

        general_folder = Folder.objects.get(pk=project["folders"][0]["pk"])

        # add regular member to folder permission
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("folder-permission-list")
        add_regular_response = client.post(
            url,
            {
                "folder": general_folder.pk,
                "member": initial_users["regular_user"].email,
                "is_folder_admin": False,
                "is_metadata_template_admin": False,
                "can_edit": False,
            },
        )
        assert add_regular_response.status_code == status.HTTP_201_CREATED

        general_folder.refresh_from_db()
        assert general_folder.members_count == 2

        admin_folder_permission = FolderPermission.objects.get(
            project_membership__member__pk=initial_users["tum_member_user"].pk,
            folder=general_folder,
        )

        assert admin_folder_permission.is_folder_admin is True
        assert admin_folder_permission.is_metadata_template_admin is True
        assert admin_folder_permission.can_edit is True

        auth_user(client, initial_users["tum_member_user"])
        url = reverse("folder-permission-detail", kwargs={"pk": admin_folder_permission.pk})
        response = client.delete(url)

        assert response.status_code == status.HTTP_204_NO_CONTENT

        assert not FolderPermission.objects.filter(
            project_membership__member__pk=initial_users["tum_member_user"].pk,
            folder=general_folder,
        ).exists()


@pytest.mark.django_db
class TestFolderMetadataAPI:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.project_1 = Project.objects.create(
            name="Project 1",
        )

        self.project_2 = Project.objects.create(
            name="Project 2",
        )

        self.folder_metadata_1 = set_metadata(
            assigned_to_content_type=self.project_1.folders.first().get_content_type(),
            assigned_to_object_id=self.project_1.folders.first().pk,
            custom_key="custom_key_1",
            value="custom_value_1",
        )

    def test_create_and_update_folder_with_metadata(self, client):
        """
        Ensure we can create a new folder and update folders with metadata.
        """
        url = reverse("folder-list")

        response = client.post(
            url,
            {
                "name": "Folder 2",
                "project": str(self.project_1.pk),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Folder.objects.filter(project=self.project_1).count() == 2

        folder = response.data

        url = reverse(
            "folder-detail",
            kwargs={
                "pk": folder["pk"],
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["metadata"]) == 0

        url = reverse("folder-list")

        response = client.post(
            url,
            {
                "name": "Folder 3",
                "project": str(self.project_1.pk),
                "metadata": [
                    {
                        "custom_key": "custom_key_1",
                        "field_type": MetadataFieldType.TEXT,
                        "value": "custom value 1",
                    },
                    {
                        "custom_key": "custom_key_2",
                        "field_type": MetadataFieldType.TEXT,
                        "value": "custom value 2",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Folder.objects.filter(project=self.project_1).count() == 3

        folder = response.data

        assert Folder.objects.get(pk=folder["pk"]).metadata.count() == 2

        url = reverse(
            "folder-detail",
            kwargs={
                "pk": folder["pk"],
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["metadata"]) == 2

        response = client.patch(
            url,
            {
                "metadata": [
                    {
                        "custom_key": "custom_key_1",
                        "field_type": MetadataFieldType.TEXT,
                        "value": "custom value 1",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["metadata"]) == 1

        response = client.patch(
            url,
            {
                "name": "Folder 3 edited",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["metadata"]) == 1

        response = client.patch(
            url,
            {
                "metadata": [],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["metadata"]) == 0

        response = client.put(
            url,
            {
                "name": "Folder 3 edited again",
                "project": str(self.project_1.pk),
                "metadata": [
                    {
                        "custom_key": "custom_key_1",
                        "field_type": MetadataFieldType.TEXT,
                        "value": "custom value 1",
                    },
                    {
                        "custom_key": "custom_key_2",
                        "field_type": MetadataFieldType.TEXT,
                        "value": "custom value 2",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["metadata"]) == 2

    def test_read_folder_metadata_list(self, client):
        """
        Ensure we can read the folder metadata list.
        """
        url = reverse("metadata-list")

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0

        url_filter = (
            f"{url}?assigned_to_content_type={get_content_type_for_object(self.project_1.folders.first().get_content_type())}"
            f"&assigned_to_object_id={self.project_1.folders.first().pk}"
        )
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        url_filter = (
            f"{url}?assigned_to_content_type={get_content_type_for_object(self.project_2.folders.first().get_content_type())}"
            f"&assigned_to_object_id={self.project_2.folders.first().pk}"
        )
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0


@pytest.mark.django_db
class TestFolderLockStatusMixin:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.project_1 = Project.objects.create(
            name="Project 1",
        )

        self.folder_1 = self.project_1.folders.first()

    def test_lock(self, client, initial_users):
        """
        Ensure we have an expected lock behavior.
        """
        set_request_for_user(initial_users["user_1"])

        url = reverse(
            "folder-detail",
            kwargs={
                "pk": self.folder_1.pk,
            },
        )

        lock_url = f"{url}lock/"
        unlock_url = f"{url}unlock/"
        status_url = f"{url}status/"

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None

        response = client.patch(
            url,
            {
                "name": "Folder 1",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Folder 1"

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None

        response = client.post(
            lock_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is True
        assert response.data["locked_by"]["pk"] == initial_users["user_1"].pk
        assert response.data["locked_at"] is not None

        last_lock_time = response.data["locked_at"]

        response = client.post(
            lock_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is True
        assert response.data["locked_by"]["pk"] == initial_users["user_1"].pk
        assert response.data["locked_at"] is not None
        assert response.data["locked_at"] > last_lock_time

        response = client.patch(
            url,
            {
                "name": "Folder 1 - Edit #1",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Folder 1 - Edit #1"

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None

        response = client.post(
            lock_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is True
        assert response.data["locked_by"]["pk"] == initial_users["user_1"].pk
        assert response.data["locked_at"] is not None

        response = client.post(
            unlock_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None

        auth_user(client, initial_users["user_2"])

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND

        assert self.folder_1.members_count == 1

        set_request_for_user(initial_users["user_1"])

        project_membership_2 = ProjectMembership.objects.create(
            project=self.project_1,
            member=initial_users["user_2"],
        )

        FolderPermission.objects.create(
            folder=self.folder_1,
            project_membership=project_membership_2,
            is_folder_admin=True,
        )

        assert self.folder_1.members_count == 2

        response = client.patch(
            url,
            {
                "name": "Folder 1 - Edit #3",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Folder 1 - Edit #3"

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None

        response = client.post(
            lock_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is True
        assert response.data["locked_by"]["pk"] == initial_users["user_2"].pk
        assert response.data["locked_at"] is not None

        auth_user(client, initial_users["user_1"])

        response = client.post(
            unlock_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        response = client.post(
            lock_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        auth_user(client, initial_users["user_2"])

        response = client.post(
            unlock_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None

        auth_user(client, initial_users["user_1"])

        response = client.post(
            lock_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is True
        assert response.data["locked_by"]["pk"] == initial_users["user_1"].pk
        assert response.data["locked_at"] is not None

        response = client.post(
            unlock_url,
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        response = client.get(status_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["locked"] is False
        assert response.data["locked_by"] is None
        assert response.data["locked_at"] is None
