import uuid

import pytest
from conftest import auth_user
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_rest_passwordreset.models import ResetPasswordToken
from rest_framework import status

from fdm.core.helpers import get_content_type_for_object, set_request_for_user
from fdm.folders.models import Folder, FolderPermission
from fdm.metadata.enums import MetadataFieldType
from fdm.metadata.helpers import set_metadata
from fdm.metadata.models import MetadataTemplate, MetadataTemplateContainer, MetadataTemplateField
from fdm.projects.models import Project, ProjectMembership
from fdm.projects.rest.filter import ProjectMembershipFilter
from fdm.uploads.models import UploadsDataset

User = get_user_model()


@pytest.mark.django_db
class TestProjectAPI:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.project_1 = Project.objects.create(
            name="Project 1",
        )

        self.project_2 = Project.objects.create(
            name="Project 2",
        )

        self.metadata_template_container_1 = MetadataTemplateContainer.objects.create(
            name="Project metadata template container 1",
            assigned_to_content_type=self.project_1.get_content_type(),
            assigned_to_object_id=self.project_1.pk,
        )

        self.metadata_template_1 = MetadataTemplate.objects.create(
            metadata_template_container=self.metadata_template_container_1,
        )

        set_metadata(
            assigned_to_content_type=self.project_1.get_content_type(),
            assigned_to_object_id=self.project_1.pk,
            custom_key="custom_key_1",
            value="custom_value_1",
        )

    def test_read_project_list(self, client):
        """
        Ensure we can read the project list.
        """
        url = reverse("project-list")

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 2

    def test_filter_by_repeated_pk(self, client):
        """
        Ensure repeated pk query parameters narrow the project list to those projects.
        """
        url = reverse("project-list")

        response = client.get(
            f"{url}?pk={self.project_1.pk}&pk={self.project_2.pk}",
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert {project["pk"] for project in response.data["results"]} == {
            str(self.project_1.pk),
            str(self.project_2.pk),
        }

        response = client.get(
            f"{url}?pk={self.project_1.pk}",
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert [project["pk"] for project in response.data["results"]] == [str(self.project_1.pk)]

    def test_filter_by_pk_omits_missing_and_inaccessible_projects(self, client, initial_users):
        """
        Ensure project IDs outside the visible queryset are omitted.
        """
        set_request_for_user(initial_users["user_2"])
        inaccessible_project = Project.objects.create(
            name="Project owned by user 2",
        )
        set_request_for_user(initial_users["user_1"])

        url = reverse("project-list")
        response = client.get(
            f"{url}?pk={self.project_1.pk}&pk={inaccessible_project.pk}&pk={uuid.uuid4()}",
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert [project["pk"] for project in response.data["results"]] == [str(self.project_1.pk)]

    def test_filter_by_pk_rejects_invalid_values(self, client):
        """
        Ensure malformed project IDs are rejected.
        """
        url = reverse("project-list")

        response = client.get(f"{url}?pk=invalid-project-id", format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_filter_by_pk_rejects_more_than_50_values(self, client):
        """
        Ensure the project list rejects too many project IDs.
        """
        url = reverse("project-list")
        query_string = "&".join([f"pk={self.project_1.pk}" for _ in range(51)])

        response = client.get(f"{url}?{query_string}", format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_project_response_includes_dashboard_summary_fields(self, client, initial_users):
        """
        Ensure project responses include current-user membership and visible summary counts.
        """
        visible_folder = self.project_1.folders.first()
        hidden_folder = Folder.objects.create(
            name="Admin-only folder",
            project=self.project_1,
        )

        UploadsDataset.objects.create(
            name="Published dataset 1",
            folder=visible_folder,
            publication_date=timezone.now(),
        )
        UploadsDataset.objects.create(
            name="Draft dataset",
            folder=visible_folder,
        )
        UploadsDataset.objects.create(
            name="Published dataset 2",
            folder=hidden_folder,
            publication_date=timezone.now(),
        )

        project_membership = ProjectMembership.objects.create(
            project=self.project_1,
            member=initial_users["user_2"],
        )

        FolderPermission.objects.create(
            folder=visible_folder,
            project_membership=project_membership,
        )

        list_url = reverse("project-list")
        detail_url = reverse(
            "project-detail",
            kwargs={
                "pk": self.project_1.pk,
            },
        )

        response = client.get(f"{list_url}?pk={self.project_1.pk}", format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["results"][0]["my_membership"] == "admin"
        assert response.data["results"][0]["folders_count"] == 2
        assert response.data["results"][0]["datasets_count"] == 2

        response = client.get(detail_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["my_membership"] == "admin"
        assert response.data["folders_count"] == 2
        assert response.data["datasets_count"] == 2

        auth_user(client, initial_users["user_2"])

        response = client.get(f"{list_url}?pk={self.project_1.pk}", format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["results"][0]["my_membership"] == "member"
        assert response.data["results"][0]["folders_count"] == 1
        assert response.data["results"][0]["datasets_count"] == 1

        response = client.get(detail_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["my_membership"] == "member"
        assert response.data["folders_count"] == 1
        assert response.data["datasets_count"] == 1

    def test_read_project_details(self, client):
        """
        Ensure we can read the project details.
        """
        url = reverse(
            "project-detail",
            kwargs={
                "pk": self.project_1.pk,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == self.project_1.name
        assert response.data["folders_count"] == 1
        assert self.project_1.project_members.count() == 1

    def test_read_project_folders_count(self, client, initial_users):
        """
        Ensure we get the correct folders count.
        """
        url = reverse(
            "project-detail",
            kwargs={
                "pk": self.project_1.pk,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["folders_count"] == 1
        assert Folder.objects.filter(project=self.project_1).count() == 1

        url = reverse("folder-list")

        response = client.post(
            url,
            {
                "name": "Folder 2 by user 1",
                "project": str(self.project_1.pk),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Folder.objects.filter(project=self.project_1).count() == 2

        url = reverse(
            "project-detail",
            kwargs={
                "pk": self.project_1.pk,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["folders_count"] == 2

        auth_user(client, initial_users["user_2"])

        url = reverse(
            "project-detail",
            kwargs={
                "pk": self.project_1.pk,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND

        set_request_for_user(initial_users["user_1"])

        ProjectMembership.objects.create(
            project=self.project_1,
            member=initial_users["user_2"],
            can_create_folders=True,
        )

        url = reverse(
            "project-detail",
            kwargs={
                "pk": self.project_1.pk,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["folders_count"] == 0

        url = reverse("folder-list")

        response = client.post(
            url,
            {
                "name": "Folder 3 by user 2",
                "project": str(self.project_1.pk),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Folder.objects.filter(project=self.project_1).count() == 3

        url = reverse(
            "project-detail",
            kwargs={
                "pk": self.project_1.pk,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["folders_count"] == 1

        auth_user(client, initial_users["user_1"])

        url = reverse(
            "project-detail",
            kwargs={
                "pk": self.project_1.pk,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["folders_count"] == 3

    def test_read_project_folders_count_with_folders_action(self, client, initial_users):
        """
        Ensure we get the correct folders count.
        """
        url = reverse(
            "project-detail",
            kwargs={
                "pk": self.project_1.pk,
            },
        )
        action_url = f"{url}folders/"

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert Folder.objects.filter(project=self.project_1).count() == 1

        url = reverse("folder-list")

        response = client.post(
            url,
            {
                "name": "Folder 2 by user 1",
                "project": str(self.project_1.pk),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Folder.objects.filter(project=self.project_1).count() == 2

        url = reverse(
            "project-detail",
            kwargs={
                "pk": self.project_1.pk,
            },
        )
        action_url = f"{url}folders/"

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

        auth_user(client, initial_users["user_2"])

        url = reverse(
            "project-detail",
            kwargs={
                "pk": self.project_1.pk,
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND

        set_request_for_user(initial_users["user_1"])

        ProjectMembership.objects.create(
            project=self.project_1,
            member=initial_users["user_2"],
            can_create_folders=True,
        )

        url = reverse(
            "project-detail",
            kwargs={
                "pk": self.project_1.pk,
            },
        )
        action_url = f"{url}folders/"

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0

        url = reverse("folder-list")

        response = client.post(
            url,
            {
                "name": "Folder 3 by user 2",
                "project": str(self.project_1.pk),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Folder.objects.filter(project=self.project_1).count() == 3

        url = reverse(
            "project-detail",
            kwargs={
                "pk": self.project_1.pk,
            },
        )
        action_url = f"{url}folders/"

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        auth_user(client, initial_users["user_1"])

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 3

    def test_change_project_details(self, client):
        """
        Ensure we can change the project details.
        """
        url = reverse(
            "project-detail",
            kwargs={
                "pk": self.project_1.pk,
            },
        )

        project_name = "Altered project name"

        response = client.patch(
            url,
            {
                "name": project_name,
                "metadata_template": self.metadata_template_1.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == project_name
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

    def test_change_project_details_and_create_a_new_metadata_template(self, client, initial_users):
        """
        Ensure we can change the project details and create a new metadata template.
        """
        url = reverse(
            "project-detail",
            kwargs={
                "pk": self.project_1.pk,
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
        Ensure we can delete an empty project.
        """
        project_pk = self.project_1.pk

        url = reverse(
            "project-detail",
            kwargs={
                "pk": project_pk,
            },
        )

        response = client.delete(url)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert Project.objects.filter(pk=project_pk).count() == 0
        assert ProjectMembership.objects.filter(project=project_pk).count() == 0
        assert FolderPermission.objects.filter(project_membership__project=project_pk).count() == 0
        assert Folder.objects.filter(project=project_pk).count() == 0

    def test_delete_protection(self, client):
        """
        Ensure we can't delete a project if it isn't empty.
        """
        project_pk = self.project_1.pk

        url = reverse(
            "project-detail",
            kwargs={
                "pk": project_pk,
            },
        )

        assert self.project_1.is_deletable is True

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_deletable"] is True

        UploadsDataset.objects.create(
            name="Dataset 1",
            folder=self.project_1.folders.first(),
        )

        assert self.project_1.is_deletable is False

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_deletable"] is False

        response = client.delete(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert Project.objects.filter(pk=project_pk).count() == 1
        assert ProjectMembership.objects.filter(project=project_pk).count() == 1
        assert FolderPermission.objects.filter(project_membership__project=project_pk).count() == 1
        assert Folder.objects.filter(project=project_pk).count() == 1

    def test_folder_metadata_template_container_action(self, client):
        """
        Ensure we can read the available metadata template container list for a project.
        """
        url = reverse(
            "project-detail",
            kwargs={
                "pk": self.project_1.pk,
            },
        )

        # We can't use reverse for the action as this would collide with other routers
        action_url = f"{url}metadata-template-container/"

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        url = reverse("project-list")

        response = client.post(
            url,
            {
                "name": "Project 3",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Project.objects.count() == 3

        project_3 = response.data

        MetadataTemplateContainer.objects.create(
            name="Project metadata template container 2",
            assigned_to_content_type=ContentType.objects.get_for_model(Project),
            assigned_to_object_id=self.project_2.pk,
        )

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        MetadataTemplateContainer.objects.create(
            name="Project metadata template container 3",
            assigned_to_content_type=ContentType.objects.get_for_model(Project),
            assigned_to_object_id=project_3["pk"],
        )

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        MetadataTemplateContainer.objects.create(
            name="Folder metadata template container 1",
            assigned_to_content_type=ContentType.objects.get_for_model(Folder),
            assigned_to_object_id=self.project_1.folders.first().pk,
        )

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        MetadataTemplateContainer.objects.create(
            name="Global metadata template container 1",
        )

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

    def test_project_metadata_templates_action(self, client):
        """
        Ensure we can read the available metadata templates list for a project.
        """
        url = reverse(
            "project-detail",
            kwargs={
                "pk": self.project_1.pk,
            },
        )

        # We can't use reverse for the action as this would collide with other routers
        action_url = f"{url}metadata-templates/"

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        url = reverse("project-list")

        response = client.post(
            url,
            {
                "name": "Project 3",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Project.objects.count() == 3

        project_3 = response.data

        metadata_template_container_2 = MetadataTemplateContainer.objects.create(
            name="Project metadata template container 2",
            assigned_to_content_type=ContentType.objects.get_for_model(Project),
            assigned_to_object_id=self.project_2.pk,
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_2,
        )

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        metadata_template_container_3 = MetadataTemplateContainer.objects.create(
            name="Project metadata template container 3",
            assigned_to_content_type=ContentType.objects.get_for_model(Project),
            assigned_to_object_id=project_3["pk"],
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_3,
        )

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        metadata_template_container_4 = MetadataTemplateContainer.objects.create(
            name="Folder metadata template container 1",
            assigned_to_content_type=ContentType.objects.get_for_model(Folder),
            assigned_to_object_id=self.project_1.folders.first().pk,
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_4,
        )

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        metadata_template_container_5 = MetadataTemplateContainer.objects.create(
            name="Global metadata template container 1",
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_5,
        )

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

    def test_create_project(self, client):
        """
        Ensure we can create a new project.
        """
        url = reverse("project-list")

        response = client.post(
            url,
            {
                "name": "Project 3",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["metadata_template"] is None
        assert Project.objects.all().count() == 3

        project = response.data

        url = reverse(
            "project-detail",
            kwargs={
                "pk": project["pk"],
            },
        )
        action_url = f"{url}folders/"

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        folders = response.data

        # Get the details of the automatically created folder
        url = reverse(
            "folder-detail",
            kwargs={
                "pk": folders[0]["pk"],
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == _("General")

        # Get the permissions of the automatically created folder
        url = reverse("folder-permission-list")

        response = client.get(f"{url}?folder={response.data['pk']}", format="json")
        assert response.status_code == status.HTTP_200_OK
        assert FolderPermission.objects.filter(folder__project=project["pk"]).count() == 1

        # Get the members of the project
        url = reverse("project-membership-list")

        response = client.get(f"{url}?project={project['pk']}", format="json")
        assert response.status_code == status.HTTP_200_OK
        assert ProjectMembership.objects.filter(project=project["pk"]).count() == 1

    def test_create_project_with_custom_folder_name(self, client):
        """
        Ensure we can create a new project with a custom folder name.
        """
        url = reverse("project-list")

        custom_folder_name = "Custom folder name"

        response = client.post(
            url,
            {
                "name": "Project 3",
                "folder_name": custom_folder_name,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        project = response.data

        url = reverse(
            "project-detail",
            kwargs={
                "pk": project["pk"],
            },
        )
        action_url = f"{url}folders/"

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["name"] == custom_folder_name

        folders = response.data

        # Get the details of the automatically created folder
        url = reverse(
            "folder-detail",
            kwargs={
                "pk": folders[0]["pk"],
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == custom_folder_name

    def test_create_project_with_user_permissions(self, client, initial_users):
        """
        Ensure we can create a new project with user permissions.
        """
        url = reverse("project-list")

        response = client.post(
            url,
            {
                "name": "Project 4",
                "project_users": [
                    {
                        "email": initial_users["user_2"].email,
                        "is_project_admin": True,
                        "is_project_metadata_template_admin": True,
                        "can_create_folders": True,
                        "is_folder_admin": True,
                        "is_folder_metadata_template_admin": True,
                        "can_edit_folder": True,
                        "can_view_folder": True,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        project = response.data

        url = reverse(
            "project-detail",
            kwargs={
                "pk": project["pk"],
            },
        )
        action_url = f"{url}folders/"

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        folders = response.data

        url = reverse("project-membership-list")

        url_filter = f"{url}?project={project['pk']}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

        url_filter = f"{url}?project={project['pk']}&member={initial_users['user_2'].pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert str(response.data[0]["member"]["pk"]) == str(initial_users["user_2"].pk)
        assert response.data[0]["is_project_admin"] is True
        assert response.data[0]["is_metadata_template_admin"] is True
        assert response.data[0]["can_create_folders"] is True

        url = reverse("folder-permission-list")

        url_filter = f"{url}?folder={folders[0]['pk']}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

        url_filter = f"{url}?folder={folders[0]['pk']}&project_membership__member={initial_users['user_2'].pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert str(response.data[0]["project_membership"]["member"]["pk"]) == str(initial_users["user_2"].pk)
        assert response.data[0]["is_folder_admin"] is True
        assert response.data[0]["is_metadata_template_admin"] is True
        assert response.data[0]["can_edit"] is True

        url = reverse("project-list")

        response = client.post(
            url,
            {
                "name": "Project 5",
                "project_users": [
                    {
                        "email": initial_users["user_2"].email,
                        "is_project_admin": True,
                        "is_project_metadata_template_admin": False,
                        "can_create_folders": False,
                        "is_folder_admin": False,
                        "is_folder_metadata_template_admin": False,
                        "can_edit_folder": False,
                        "can_view_folder": False,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        project = response.data

        url = reverse(
            "project-detail",
            kwargs={
                "pk": project["pk"],
            },
        )
        action_url = f"{url}folders/"

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        folders = response.data

        url = reverse("project-membership-list")

        url_filter = f"{url}?project={project['pk']}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

        url_filter = f"{url}?project={project['pk']}&member={initial_users['user_2'].pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert str(response.data[0]["member"]["pk"]) == str(initial_users["user_2"].pk)
        assert response.data[0]["is_project_admin"] is True
        assert response.data[0]["is_metadata_template_admin"] is True
        assert response.data[0]["can_create_folders"] is True

        url = reverse("folder-permission-list")

        url_filter = f"{url}?folder={folders[0]['pk']}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

        url_filter = f"{url}?folder={folders[0]['pk']}&project_membership__member={initial_users['user_2'].pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert str(response.data[0]["project_membership"]["member"]["pk"]) == str(initial_users["user_2"].pk)
        assert response.data[0]["is_folder_admin"] is True
        assert response.data[0]["is_metadata_template_admin"] is True
        assert response.data[0]["can_edit"] is True

        url = reverse("project-list")

        response = client.post(
            url,
            {
                "name": "Project 6",
                "project_users": [
                    {
                        "email": initial_users["user_2"].email,
                        "is_project_admin": False,
                        "is_project_metadata_template_admin": True,
                        "can_create_folders": True,
                        "is_folder_admin": False,
                        "is_folder_metadata_template_admin": True,
                        "can_edit_folder": False,
                        "can_view_folder": True,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        project = response.data

        url = reverse(
            "project-detail",
            kwargs={
                "pk": project["pk"],
            },
        )
        action_url = f"{url}folders/"

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        folders = response.data

        url = reverse("project-membership-list")

        url_filter = f"{url}?project={project['pk']}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

        url_filter = f"{url}?project={project['pk']}&member={initial_users['user_2'].pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert str(response.data[0]["member"]["pk"]) == str(initial_users["user_2"].pk)
        assert response.data[0]["is_project_admin"] is False
        assert response.data[0]["is_metadata_template_admin"] is True
        assert response.data[0]["can_create_folders"] is True

        url = reverse("folder-permission-list")

        url_filter = f"{url}?folder={folders[0]['pk']}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

        url_filter = f"{url}?folder={folders[0]['pk']}&project_membership__member={initial_users['user_2'].pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert str(response.data[0]["project_membership"]["member"]["pk"]) == str(initial_users["user_2"].pk)
        assert response.data[0]["is_folder_admin"] is False
        assert response.data[0]["is_metadata_template_admin"] is True
        assert response.data[0]["can_edit"] is False

    def test_create_project_with_user_permissions_for_an_unknown_user(self, client):
        """
        Ensure we can create a new project with user permissions for an unknown user.
        """
        assert User.objects.count() == 6
        assert ResetPasswordToken.objects.count() == 0

        url = reverse("project-list")

        response = client.post(
            url,
            {
                "name": "Project 5",
                "project_users": [
                    {
                        "email": "unknown@test.local",
                        "is_project_admin": True,
                        "is_project_metadata_template_admin": True,
                        "can_create_folders": True,
                        "is_folder_admin": True,
                        "is_folder_metadata_template_admin": True,
                        "can_edit_folder": True,
                        "can_view_folder": True,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        project = Project.objects.get(pk=response.data["pk"])
        assert project.project_members.count() == 2

        assert User.objects.count() == 7
        assert ResetPasswordToken.objects.count() == 1

    def test_create_project_with_metadata_template(self, client, initial_users):
        """
        Ensure we can create a new project with a metadata template.
        """
        url = reverse("metadata-template-list")

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        url = reverse("project-list")

        response = client.post(
            url,
            {
                "name": "Project 6",
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

        project = response.data

        url = reverse("metadata-template-list")

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 2

        url = reverse(
            "metadata-template-detail",
            kwargs={
                "pk": project["metadata_template"]["pk"],
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] is None
        assert response.data["metadata_template_container"]["name"] == "Metadata template 2"

        content_type = ContentType.objects.get_for_model(Project)
        assert response.data["metadata_template_container"]["assigned_to_content_type"] == get_content_type_for_object(
            content_type,
        )
        assert response.data["metadata_template_container"]["assigned_to_object_id"] == project["pk"]

        assert response.data["metadata_template_container"]["project"] is not None
        assert response.data["metadata_template_container"]["project"]["pk"] == project["pk"]
        assert response.data["metadata_template_container"]["project"]["name"] == project["name"]

        url = reverse("metadata-template-field-list")
        url_filter = f"{url}?metadata_template={project['metadata_template']['pk']}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 2

        assert MetadataTemplateField.objects.filter(metadata_template=project["metadata_template"]["pk"]).count() == 2

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

    def test_project_creation_permission(self, client, initial_users):
        # With can_create_projects Permission
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("project-list")
        response = client.post(
            url,
            {
                "name": "New Project",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED

        # As Regular User
        auth_user(client, initial_users["regular_user"])
        response = client.post(
            url,
            {
                "name": "Another Project",
            },
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_project_members_action(self, client, initial_users):
        """
        Ensure we can create, update and delete project members with a single request.
        """
        project_users = [
            {
                "email": initial_users["user_1"].email,
                "is_project_admin": True,
                "is_metadata_template_admin": True,
                "can_create_folders": True,
            },
            {
                "email": initial_users["user_2"].email,
                "is_project_admin": True,
                "is_metadata_template_admin": True,
                "can_create_folders": False,
            },
            {
                "email": "unknown@test.local",
                "is_project_admin": False,
                "is_metadata_template_admin": False,
                "can_create_folders": False,
            },
        ]

        folder_1 = self.project_1.folder.first()

        url = reverse(
            "project-detail",
            kwargs={
                "pk": self.project_1.pk,
            },
        )
        action_url = f"{url}members/"

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

        assert ProjectMembership.objects.filter(project=self.project_1).count() == 1
        assert not User.objects.filter(email="unknown@test.local").exists()

        response = client.put(
            action_url,
            {
                "project_users": project_users,
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
                member__email__in=[user["email"] for user in project_users],
            ).count()
            == 3
        )

        project_membership_1 = ProjectMembership.objects.get(
            project=self.project_1,
            member__email=project_users[0]["email"],
        )
        assert project_membership_1.is_project_admin is True
        assert project_membership_1.is_metadata_template_admin is True
        assert project_membership_1.can_create_folders is True

        folder_permission_1 = FolderPermission.objects.get(
            project_membership__member__email=project_users[0]["email"],
            folder=folder_1,
        )
        assert folder_permission_1.is_folder_admin is True
        assert folder_permission_1.is_metadata_template_admin is True
        assert folder_permission_1.can_edit is True

        project_membership_2 = ProjectMembership.objects.get(
            project=self.project_1,
            member__email=project_users[1]["email"],
        )
        assert project_membership_2.is_project_admin is True
        assert project_membership_2.is_metadata_template_admin is True
        assert project_membership_2.can_create_folders is True

        folder_permission_2 = FolderPermission.objects.get(
            project_membership__member__email=project_users[1]["email"],
            folder=folder_1,
        )
        assert folder_permission_2.is_folder_admin is True
        assert folder_permission_2.is_metadata_template_admin is True
        assert folder_permission_2.can_edit is True

        project_membership_3 = ProjectMembership.objects.get(
            project=self.project_1,
            member__email=project_users[2]["email"],
        )
        assert project_membership_3.is_project_admin is False
        assert project_membership_3.is_metadata_template_admin is False
        assert project_membership_3.can_create_folders is False

        assert not FolderPermission.objects.filter(
            folder=folder_1,
            project_membership__project=self.project_1,
            project_membership__member__email=project_users[2]["email"],
        ).exists()

        response = client.put(
            action_url,
            {
                "project_users": [
                    project_users[0],
                    project_users[1],
                    {
                        "email": project_users[2]["email"],
                        "is_project_admin": False,
                        "is_metadata_template_admin": True,
                        "can_create_folders": True,
                    },
                ],
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
                member__email__in=[user["email"] for user in project_users],
            ).count()
            == 3
        )

        project_membership_1 = ProjectMembership.objects.get(
            project=self.project_1,
            member__email=project_users[0]["email"],
        )
        assert project_membership_1.is_project_admin is True
        assert project_membership_1.is_metadata_template_admin is True
        assert project_membership_1.can_create_folders is True

        folder_permission_1 = FolderPermission.objects.get(
            project_membership__member__email=project_users[0]["email"],
            folder=folder_1,
        )
        assert folder_permission_1.is_folder_admin is True
        assert folder_permission_1.is_metadata_template_admin is True
        assert folder_permission_1.can_edit is True

        project_membership_2 = ProjectMembership.objects.get(
            project=self.project_1,
            member__email=project_users[1]["email"],
        )
        assert project_membership_2.is_project_admin is True
        assert project_membership_2.is_metadata_template_admin is True
        assert project_membership_2.can_create_folders is True

        folder_permission_2 = FolderPermission.objects.get(
            project_membership__member__email=project_users[1]["email"],
            folder=folder_1,
        )
        assert folder_permission_2.is_folder_admin is True
        assert folder_permission_2.is_metadata_template_admin is True
        assert folder_permission_2.can_edit is True

        project_membership_3 = ProjectMembership.objects.get(
            project=self.project_1,
            member__email=project_users[2]["email"],
        )
        assert project_membership_3.is_project_admin is False
        assert project_membership_3.is_metadata_template_admin is True
        assert project_membership_3.can_create_folders is True

        assert not FolderPermission.objects.filter(
            folder=folder_1,
            project_membership__project=self.project_1,
            project_membership__member__email=project_users[2]["email"],
        ).exists()

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
        assert Folder.objects.filter(project=self.project_1).count() == 2
        assert FolderPermission.objects.filter(folder_id=response.data["pk"]).count() == 2

        UploadsDataset.objects.create(
            name="Dataset 1",
            folder_id=response.data["pk"],
        )

        auth_user(client, initial_users["user_1"])

        response = client.put(
            action_url,
            {
                "project_users": [
                    project_users[0],
                    project_users[2],
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2
        assert ProjectMembership.objects.filter(project=self.project_1).count() == 2
        assert (
            ProjectMembership.objects.filter(
                project=self.project_1,
                member__email__in=[
                    project_users[0]["email"],
                    project_users[2]["email"],
                ],
            ).count()
            == 2
        )

        project_membership_1.refresh_from_db()
        assert project_membership_1.is_project_admin is True
        assert project_membership_1.is_metadata_template_admin is True
        assert project_membership_1.can_create_folders is True

        folder_permission_1.refresh_from_db()
        assert folder_permission_1.is_folder_admin is True
        assert folder_permission_1.is_metadata_template_admin is True
        assert folder_permission_1.can_edit is True

        project_membership_3.refresh_from_db()
        assert project_membership_3.is_project_admin is False
        assert project_membership_3.is_metadata_template_admin is False
        assert project_membership_3.can_create_folders is False

        assert not FolderPermission.objects.filter(
            folder=folder_1,
            project_membership__project=self.project_1,
            project_membership__member__email=project_users[2]["email"],
        ).exists()

        response = client.put(
            action_url,
            {
                "project_users": [],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert ProjectMembership.objects.filter(project=self.project_1).count() == 2

        response = client.put(
            action_url,
            {
                "project_users": [
                    project_users[2],
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert ProjectMembership.objects.filter(project=self.project_1).count() == 2

        response = client.put(
            action_url,
            {
                "project_users": [
                    {
                        "email": project_users[0]["email"],
                        "is_project_admin": False,
                        "is_metadata_template_admin": False,
                        "can_create_folders": False,
                    },
                    project_users[2],
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert ProjectMembership.objects.filter(project=self.project_1).count() == 2

        response = client.put(
            action_url,
            {
                "project_users": [
                    project_users[0],
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert ProjectMembership.objects.filter(project=self.project_1).count() == 1
        assert (
            ProjectMembership.objects.filter(
                project=self.project_1,
                member__email__in=[
                    project_users[0]["email"],
                ],
            ).count()
            == 1
        )

        project_membership_1.refresh_from_db()
        assert project_membership_1.is_project_admin is True
        assert project_membership_1.is_metadata_template_admin is True
        assert project_membership_1.can_create_folders is True

        folder_permission_1.refresh_from_db()
        assert folder_permission_1.is_folder_admin is True
        assert folder_permission_1.is_metadata_template_admin is True
        assert folder_permission_1.can_edit is True

        response = client.put(
            action_url,
            {
                "project_users": [
                    {
                        "email": project_users[0]["email"],
                        "is_project_admin": False,
                        "is_metadata_template_admin": False,
                        "can_create_folders": False,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert ProjectMembership.objects.filter(project=self.project_1).count() == 1

        response = client.put(
            action_url,
            {
                "project_users": [
                    {
                        "email": initial_users["project_admin_user"].email,
                        "is_project_admin": True,
                        "is_metadata_template_admin": True,
                        "can_create_folders": True,
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert ProjectMembership.objects.filter(project=self.project_1).count() == 1

        folder_permission_4 = FolderPermission.objects.get(
            project_membership__member__email=initial_users["project_admin_user"].email,
            folder=folder_1,
        )
        assert folder_permission_4.is_folder_admin is True
        assert folder_permission_4.is_metadata_template_admin is True
        assert folder_permission_4.can_edit is True

        response = client.put(
            action_url,
            {
                "project_users": project_users,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

        auth_user(client, initial_users["project_admin_user"])

        response = client.put(
            action_url,
            {
                "project_users": project_users,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert ProjectMembership.objects.filter(project=self.project_1).count() == 3

        project_membership_1 = ProjectMembership.objects.get(
            project=self.project_1,
            member__email=project_users[0]["email"],
        )
        assert project_membership_1.is_project_admin is True
        assert project_membership_1.is_metadata_template_admin is True
        assert project_membership_1.can_create_folders is True

        folder_permission_1 = FolderPermission.objects.get(
            project_membership__member__email=project_users[0]["email"],
            folder=folder_1,
        )
        assert folder_permission_1.is_folder_admin is True
        assert folder_permission_1.is_metadata_template_admin is True
        assert folder_permission_1.can_edit is True

        project_membership_2 = ProjectMembership.objects.get(
            project=self.project_1,
            member__email=project_users[1]["email"],
        )
        assert project_membership_2.is_project_admin is True
        assert project_membership_2.is_metadata_template_admin is True
        assert project_membership_2.can_create_folders is True

        folder_permission_2 = FolderPermission.objects.get(
            project_membership__member__email=project_users[1]["email"],
            folder=folder_1,
        )
        assert folder_permission_2.is_folder_admin is True
        assert folder_permission_2.is_metadata_template_admin is True
        assert folder_permission_2.can_edit is True

        project_membership_3 = ProjectMembership.objects.get(
            project=self.project_1,
            member__email=project_users[2]["email"],
        )
        assert project_membership_3.is_project_admin is False
        assert project_membership_3.is_metadata_template_admin is False
        assert project_membership_3.can_create_folders is False

        assert not FolderPermission.objects.filter(
            folder=folder_1,
            project_membership__project=self.project_1,
            project_membership__member__email=project_users[2]["email"],
        ).exists()

        auth_user(client, initial_users["regular_user"])

        response = client.put(
            action_url,
            {
                "project_users": project_users,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_filter_by_creator(self, client, initial_users):
        """
        Ensure we can filter the project list by creator.
        """
        url = reverse("project-list")

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 2

        url_filter = f"{url}?created_by=me"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 2

        url_filter = f"{url}?created_by=others"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0

        set_request_for_user(initial_users["user_2"])

        project_3 = Project.objects.create(
            name="Project 3",
        )

        ProjectMembership.objects.create(
            project=project_3,
            member=initial_users["user_1"],
        )

        auth_user(client, initial_users["user_1"])

        url_filter = f"{url}?created_by=others"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        auth_user(client, initial_users["user_2"])

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        url_filter = f"{url}?created_by=me"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        url_filter = f"{url}?created_by=others"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0

        set_request_for_user(initial_users["user_1"])

        ProjectMembership.objects.create(
            project=self.project_1,
            member=initial_users["user_2"],
        )

        ProjectMembership.objects.create(
            project=self.project_2,
            member=initial_users["user_2"],
        )

        auth_user(client, initial_users["user_2"])

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 3

        url_filter = f"{url}?created_by=me"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        url_filter = f"{url}?created_by=others"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 2

    def test_filter_by_membership_status(self, client, initial_users):
        """
        Ensure we can filter the project list by membership status.
        """
        url = reverse("project-list")

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 2

        url_filter = f"{url}?membership=admin"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 2

        url_filter = f"{url}?membership=member"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0

        set_request_for_user(initial_users["user_2"])

        project_3 = Project.objects.create(
            name="Project 3",
        )

        ProjectMembership.objects.create(
            project=project_3,
            member=initial_users["user_1"],
            is_project_admin=False,
        )

        auth_user(client, initial_users["user_1"])

        url_filter = f"{url}?membership=member"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        auth_user(client, initial_users["user_2"])

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        url_filter = f"{url}?membership=admin"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        url_filter = f"{url}?membership=member"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 0

        set_request_for_user(initial_users["user_1"])

        ProjectMembership.objects.create(
            project=self.project_1,
            member=initial_users["user_2"],
            is_project_admin=False,
        )

        ProjectMembership.objects.create(
            project=self.project_2,
            member=initial_users["user_2"],
            is_project_admin=False,
        )

        auth_user(client, initial_users["user_2"])

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 3

        url_filter = f"{url}?membership=admin"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1

        url_filter = f"{url}?membership=member"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 2

    def test_metadata_template_container_pool(self, client, initial_users):
        """
        Ensure we can set and get the project's metadata template container pool.
        """
        url = reverse(
            "project-detail",
            kwargs={
                "pk": self.project_1.pk,
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

        self.project_1.metadata_template = None
        self.project_1.save()

        response = client.get(action_url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0

    def test_metadata_templates_count(self, initial_users):
        """
        Ensure we get an accurate count of all metadata templates assigned to a project.
        """
        self.project_1.refresh_from_db()
        assert MetadataTemplateContainer.objects.count() == 1
        assert MetadataTemplate.objects.count() == 1
        assert self.project_1.metadata_templates_count == 1

        metadata_template_container_1 = MetadataTemplateContainer.objects.create(
            name="Global metadata template container 1",
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_1,
        )

        self.project_1.refresh_from_db()
        assert MetadataTemplateContainer.objects.count() == 2
        assert MetadataTemplate.objects.count() == 2
        assert self.project_1.metadata_templates_count == 1

        metadata_template_container_2 = MetadataTemplateContainer.objects.create(
            name="Project metadata template container 1",
            assigned_to_content_type=self.project_1.get_content_type(),
            assigned_to_object_id=self.project_1.pk,
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_2,
        )

        self.project_1.refresh_from_db()
        assert MetadataTemplateContainer.objects.count() == 3
        assert MetadataTemplate.objects.count() == 3
        assert self.project_1.metadata_templates_count == 2

        metadata_template_container_3 = MetadataTemplateContainer.objects.create(
            name="Folder metadata template container 1",
            assigned_to_content_type=self.project_1.folders.first().get_content_type(),
            assigned_to_object_id=self.project_1.folders.first().pk,
        )

        MetadataTemplate.objects.create(
            metadata_template_container=metadata_template_container_3,
        )

        self.project_1.refresh_from_db()
        assert MetadataTemplateContainer.objects.count() == 4
        assert MetadataTemplate.objects.count() == 4
        assert self.project_1.metadata_templates_count == 2


@pytest.mark.django_db
class TestProjectMembershipAPI:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.project_1 = Project.objects.create(
            name="Project 1",
        )

    def test_read_project_members_list(self, client, initial_users):
        """
        Ensure we can read the project member list.
        """
        url = reverse("project-membership-list")

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert ProjectMembership.objects.count() == 1

        ProjectMembership.objects.create(
            project=self.project_1,
            member=initial_users["user_2"],
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2
        assert ProjectMembership.objects.count() == 2

        url_filter = f"{url}?member={initial_users['user_1'].pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert ProjectMembership.objects.count() == 2
        assert response.data[0]["member"]["pk"] == initial_users["user_1"].pk

        url_filter = f"{url}?member__email={initial_users['user_1'].email}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert ProjectMembership.objects.count() == 2
        assert response.data[0]["member"]["pk"] == initial_users["user_1"].pk

        url_filter = f"{url}?member={ProjectMembershipFilter.Member.ME}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert ProjectMembership.objects.count() == 2
        assert response.data[0]["member"]["pk"] == initial_users["user_1"].pk

        url_filter = f"{url}?member={initial_users['user_2'].pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert ProjectMembership.objects.count() == 2
        assert response.data[0]["member"]["pk"] == initial_users["user_2"].pk

        url_filter = f"{url}?member="
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2
        assert ProjectMembership.objects.count() == 2

        set_request_for_user(initial_users["user_1"])

        Project.objects.create(
            name="Project 2",
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 3
        assert ProjectMembership.objects.count() == 3

        url_filter = f"{url}?project={self.project_1.pk}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2
        assert ProjectMembership.objects.count() == 3

        url_filter = f"{url}?project={self.project_1.pk}&member={ProjectMembershipFilter.Member.ME}"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert ProjectMembership.objects.count() == 3

        url_filter = f"{url}?member=invalid_value"
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0
        assert ProjectMembership.objects.count() == 3

    def test_add_project_membership_permission(self, client, initial_users):
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
        project = Project.objects.get(pk=project_id)

        # add another admin
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("project-membership-list")
        add_admin_response = client.post(
            url,
            {
                "member": initial_users["project_admin_user"].pk,
                "project": project.pk,
                "is_project_admin": True,
            },
        )
        assert add_admin_response.status_code == status.HTTP_201_CREATED

        # Test that the project_admin_user is a member
        auth_user(client, initial_users["project_admin_user"])
        url = reverse("project-membership-list")

        members_response = client.get(f"{url}?project={project.pk}", format="json")
        assert members_response.status_code == status.HTTP_200_OK
        members_data = members_response.data
        assert any(member["member"]["pk"] == initial_users["project_admin_user"].id for member in members_data), (
            "Admin user should be in project members"
        )

        project.unlock()

        # add a regular member as an admin
        auth_user(client, initial_users["project_admin_user"])
        url = reverse("project-membership-list")
        add_regular_response = client.post(
            url,
            {
                "member": initial_users["regular_user"].pk,
                "project": project.pk,
                "is_project_admin": False,
            },
        )
        assert add_regular_response.status_code == status.HTTP_201_CREATED

        # Test that the regular_user is a member
        auth_user(client, initial_users["project_admin_user"])
        url = reverse("project-membership-list")

        members_response = client.get(f"{url}?project={project.pk}", format="json")
        assert members_response.status_code == status.HTTP_200_OK
        members_data = members_response.data
        assert any(member["member"]["pk"] == initial_users["regular_user"].id for member in members_data), (
            "Admin user should be in project members"
        )

        # Try to add another regular member as regular member
        auth_user(client, initial_users["regular_user"])
        url = reverse("project-membership-list")
        add_regular_response = client.post(
            url,
            {
                "member": initial_users["regular_user_2"].pk,
                "project": project.pk,
                "is_project_admin": False,
            },
        )
        assert add_regular_response.status_code == status.HTTP_403_FORBIDDEN

    def test_project_update_permission(self, client, initial_users):
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
        project = Project.objects.get(pk=project_id)

        # add another admin
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("project-membership-list")
        add_admin_response = client.post(
            url,
            {
                "member": initial_users["project_admin_user"].pk,
                "project": project.pk,
                "is_project_admin": True,
            },
        )
        assert add_admin_response.status_code == status.HTTP_201_CREATED

        # Test that the project_admin_user can indeed access the project
        auth_user(client, initial_users["project_admin_user"])
        url = reverse("project-membership-list")

        members_response = client.get(f"{url}?project={project.pk}", format="json")
        assert members_response.status_code == status.HTTP_200_OK
        members_data = members_response.data
        assert any(member["member"]["pk"] == initial_users["project_admin_user"].id for member in members_data), (
            "Admin user should be in project members"
        )

        # add a regular member
        auth_user(client, initial_users["tum_member_user"])
        url = reverse("project-membership-list")
        add_regular_response = client.post(
            url,
            {
                "member": initial_users["regular_user"].pk,
                "project": project.pk,
                "is_project_admin": False,
            },
        )
        assert add_regular_response.status_code == status.HTTP_201_CREATED

        # Test that the regular_user can indeed access the project
        auth_user(client, initial_users["project_admin_user"])
        url = reverse("project-membership-list")

        members_response = client.get(f"{url}?project={project.pk}", format="json")
        assert members_response.status_code == status.HTTP_200_OK
        members_data = members_response.data
        assert any(member["member"]["pk"] == initial_users["regular_user"].id for member in members_data), (
            "Admin user should be in project members"
        )

        # Test update permissions
        auth_user(client, initial_users["tum_member_user"])
        url = reverse(
            "project-detail",
            kwargs={
                "pk": project_id,
            },
        )
        update_response = client.put(
            url,
            {
                "name": "Updated Project Name",
            },
        )
        assert update_response.status_code == status.HTTP_200_OK, "Admin user should be able to update the project"
        assert update_response.data["name"] == "Updated Project Name"

        project.unlock()

        # As Project Admin
        auth_user(client, initial_users["project_admin_user"])
        url = reverse(
            "project-detail",
            kwargs={
                "pk": project_id,
            },
        )
        response = client.put(
            url,
            {
                "name": "Updated Project Name Again",
            },
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Updated Project Name Again"

        # As regular_user, should not be able to update
        auth_user(client, initial_users["regular_user"])
        url = reverse(
            "project-detail",
            kwargs={
                "pk": project_id,
            },
        )
        unauthorized_update_response = client.put(
            url,
            {
                "name": "Attempted Unauthorized Update",
            },
        )
        assert unauthorized_update_response.status_code == status.HTTP_403_FORBIDDEN, (
            "Regular user should not be able to update the project"
        )


@pytest.mark.django_db
class TestProjectMetadataAPI:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.project_1 = Project.objects.create(
            name="Project 1",
        )

        self.project_2 = Project.objects.create(
            name="Project 2",
        )

        self.project_metadata_1 = set_metadata(
            assigned_to_content_type=self.project_1.get_content_type(),
            assigned_to_object_id=self.project_1.pk,
            custom_key="custom_key_1",
            value="custom_value_1",
        )

    def test_create_and_update_project_with_metadata(self, client):
        """
        Ensure we can create a new project and update projects with metadata.
        """
        url = reverse("project-list")

        response = client.post(
            url,
            {
                "name": "Project 3",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Project.objects.count() == 3

        project = response.data

        url = reverse(
            "project-detail",
            kwargs={
                "pk": project["pk"],
            },
        )

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["metadata"]) == 0

        url = reverse("project-list")

        response = client.post(
            url,
            {
                "name": "Project 4",
                "metadata": [
                    {
                        "custom_key": "custom_key_1",
                        "value": "custom value 1",
                    },
                    {
                        "custom_key": "custom_key_2",
                        "value": "custom value 2",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Project.objects.count() == 4

        project = response.data

        assert Project.objects.get(pk=project["pk"]).metadata.count() == 2

        url = reverse(
            "project-detail",
            kwargs={
                "pk": project["pk"],
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
                "name": "Project 4 edited",
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
                "name": "Project 4 edited again",
                "metadata": [
                    {
                        "custom_key": "custom_key_1",
                        "value": "custom value 1",
                    },
                    {
                        "custom_key": "custom_key_2",
                        "value": "custom value 2",
                    },
                ],
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["metadata"]) == 2

    def test_read_project_metadata_list(self, client):
        """
        Ensure we can read the project metadata list.
        """
        url = reverse("metadata-list")

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0

        url_filter = (
            f"{url}?assigned_to_content_type={get_content_type_for_object(self.project_1.get_content_type())}"
            f"&assigned_to_object_id={self.project_1.pk}"
        )
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

        url_filter = (
            f"{url}?assigned_to_content_type={get_content_type_for_object(self.project_2.get_content_type())}"
            f"&assigned_to_object_id={self.project_2.pk}"
        )
        response = client.get(url_filter, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 0


@pytest.mark.django_db
class TestProjectLockStatusMixin:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])

        self.project_1 = Project.objects.create(
            name="Project 1",
        )

    def test_lock(self, client, initial_users):
        """
        Ensure we have an expected lock behavior.
        """
        set_request_for_user(initial_users["user_1"])

        url = reverse(
            "project-detail",
            kwargs={
                "pk": self.project_1.pk,
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
                "name": "Project 1",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Project 1"

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
                "name": "Project 1 - Edit #1",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Project 1 - Edit #1"

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

        assert self.project_1.members_count == 1

        ProjectMembership.objects.create(
            project=self.project_1,
            member=initial_users["user_2"],
            is_project_admin=True,
        )

        assert self.project_1.members_count == 2

        response = client.patch(
            url,
            {
                "name": "Project 1 - Edit #3",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        response = client.get(url, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Project 1 - Edit #3"

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


@pytest.mark.django_db
class TestMembershipAPI:
    @pytest.fixture(autouse=True)
    def _setup(self, initial_users):
        set_request_for_user(initial_users["user_1"])
        self.project_1 = Project.objects.create(
            name="Project 1",
        )

    def test_create_project_membership_with_member_pk(self, client, initial_users):
        """
        Ensure we can create a project membership using member's primary key.
        """
        assert Project.objects.get(pk=self.project_1.pk).members_count == 1

        url = reverse("project-membership-list")
        data = {
            "project": self.project_1.pk,
            "member": initial_users["user_2"].pk,  # Using primary key of the user
            "is_project_admin": False,
            "can_create_folders": False,
        }
        auth_user(client, initial_users["user_1"])
        response = client.post(url, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED

        assert ProjectMembership.objects.filter(project=self.project_1, member=initial_users["user_1"]).exists()

        assert ProjectMembership.objects.filter(project=self.project_1, member=initial_users["user_2"]).exists()

        assert not (
            ProjectMembership.objects.get(project=self.project_1, member=initial_users["user_2"]).is_project_admin
        )

        assert not (
            ProjectMembership.objects.get(project=self.project_1, member=initial_users["user_2"]).can_create_folders
        )

        assert Project.objects.get(pk=self.project_1.pk).members_count == 2

    def test_create_project_membership_with_existing_member_email(self, client, initial_users):
        """
        Ensure we can create a project membership using member's email address.
        """
        assert Project.objects.get(pk=self.project_1.pk).members_count == 1

        url = reverse("project-membership-list")
        data = {
            "project": self.project_1.pk,
            "member": initial_users["user_2"].email,  # Using email of the user
            "is_project_admin": False,
            "can_create_folders": True,
        }
        auth_user(client, initial_users["user_1"])
        response = client.post(url, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED

        assert ProjectMembership.objects.filter(project=self.project_1, member=initial_users["user_1"]).exists()

        assert ProjectMembership.objects.filter(project=self.project_1, member=initial_users["user_2"]).exists()

        assert not (
            ProjectMembership.objects.get(project=self.project_1, member=initial_users["user_2"]).is_project_admin
        )

        assert ProjectMembership.objects.get(project=self.project_1, member=initial_users["user_2"]).can_create_folders

        assert Project.objects.get(pk=self.project_1.pk).members_count == 2

    def test_create_project_membership_with_new_member_email(self, client, initial_users):
        """
        Ensure we can create a new user and project membership using a new member's email address.
        """
        assert Project.objects.get(pk=self.project_1.pk).members_count == 1

        new_email = "new_user@example.com"
        url = reverse("project-membership-list")
        data = {
            "project": self.project_1.pk,
            "member": new_email,
            "is_project_admin": False,
            "can_create_folders": False,
        }
        auth_user(client, initial_users["user_1"])
        response = client.post(url, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED

        assert User.objects.filter(email=new_email).exists()

        new_user = User.objects.get(email=new_email)
        assert ProjectMembership.objects.filter(project=self.project_1, member=new_user).exists()

        assert not ProjectMembership.objects.get(project=self.project_1, member=new_user).is_project_admin

        assert not (ProjectMembership.objects.get(project=self.project_1, member=new_user).can_create_folders)

        assert Project.objects.get(pk=self.project_1.pk).members_count == 2

    def test_create_project_membership_with_invalid_new_member_email(self, client, initial_users):
        """
        Ensure we can create a new user and project membership using a new member's email address.
        """
        assert Project.objects.get(pk=self.project_1.pk).members_count == 1

        invalid_email = "new_user@example"
        url = reverse("project-membership-list")
        data = {
            "project": self.project_1.pk,
            "member": invalid_email,
            "is_project_admin": False,
            "can_create_folders": False,
        }
        auth_user(client, initial_users["user_1"])
        response = client.post(url, data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

        expected_error = "Enter a valid email address."
        assert expected_error in response.data["member"][0], (
            f"Expected error message not found. Received: {response.data['member']}"
        )

        assert Project.objects.get(pk=self.project_1.pk).members_count == 1

    def test_change_project_membership(self, client, initial_users):
        """
        Ensure we can update a project membership's permissions.
        """
        # Create a membership for user_2 in project_1
        project_membership = ProjectMembership.objects.create(
            project=self.project_1,
            member=initial_users["user_2"],
            is_project_admin=False,
            can_create_folders=True,
        )

        # Check initial state
        assert ProjectMembership.objects.filter(project=self.project_1, member=initial_users["user_2"]).exists()

        assert not ProjectMembership.objects.get(
            project=self.project_1,
            member=initial_users["user_2"],
        ).is_project_admin

        assert ProjectMembership.objects.get(project=self.project_1, member=initial_users["user_2"]).can_create_folders

        # Prepare update data
        url = reverse("project-membership-detail", kwargs={"pk": project_membership.pk})
        update_data = {
            "is_project_admin": False,
            "can_create_folders": False,
        }

        # Authenticate as user_1 who has permission to update
        auth_user(client, initial_users["user_1"])
        response = client.patch(url, update_data, format="json")

        # Check the response and updated state
        assert response.status_code == status.HTTP_200_OK

        updated_membership = ProjectMembership.objects.get(pk=project_membership.pk)

        assert not updated_membership.is_project_admin

        assert not updated_membership.can_create_folders

        # Send another member in the post data, which should not be updated
        assert ProjectMembership.objects.filter(project_id=self.project_1, member=initial_users["user_2"]).exists()

        url = reverse("project-membership-detail", kwargs={"pk": project_membership.pk})
        update_data = {
            "is_project_admin": False,
            "can_create_folders": False,
        }

        # Authenticate as user_1 who has permission to update
        auth_user(client, initial_users["user_1"])
        response = client.patch(url, update_data, format="json")

        # Check the response and updated state
        assert response.status_code == status.HTTP_200_OK

        updated_membership = ProjectMembership.objects.get(pk=project_membership.pk)

        assert not updated_membership.is_project_admin

        assert not updated_membership.can_create_folders

        assert ProjectMembership.objects.filter(project_id=self.project_1, member=initial_users["user_2"]).exists()

        assert not ProjectMembership.objects.filter(
            project_id=self.project_1,
            member=initial_users["regular_user"],
        ).exists()

    def test_prevent_taking_admin_flags_handlers(self, client, initial_users):
        """
        Ensure the signal handler automatically sets can_create_folders to True for project admins.
        """
        project_membership = ProjectMembership.objects.create(
            project=self.project_1,
            member=initial_users["user_2"],
            is_project_admin=True,
            can_create_folders=False,
        )

        assert project_membership.is_project_admin is True
        assert project_membership.can_create_folders is True

        auth_user(client, initial_users["user_1"])

        url = reverse(
            "project-membership-detail",
            kwargs={
                "pk": project_membership.pk,
            },
        )

        response = client.patch(
            url,
            {
                "is_project_admin": True,
                "can_create_folders": False,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        project_membership.refresh_from_db()

        assert project_membership.is_project_admin is True
        assert project_membership.can_create_folders is True

    def test_prevent_deletion_of_last_admin_with_empty_project(self, client, initial_users):
        """
        Ensure we can delete the last admin of a project, if the project is empty.
        """
        admin_membership = ProjectMembership.objects.get(
            project=self.project_1,
            member=initial_users["user_1"],
        )

        # Check initial state for user_1, the admin
        assert ProjectMembership.objects.filter(project=self.project_1, member=initial_users["user_1"]).exists()
        assert admin_membership.is_project_admin
        assert admin_membership.can_create_folders

        url = reverse(
            "project-membership-detail",
            kwargs={
                "pk": admin_membership.pk,
            },
        )

        response = client.delete(url)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert ProjectMembership.objects.filter(project=self.project_1).count() == 0

    def test_prevent_deletion_of_last_admin(self, client, initial_users):
        """
        Ensure we cannot delete the last admin of a project.
        """
        # create a folder and a dataset so the project isn't empty
        folder_1 = Folder.objects.create(
            name="Folder 1",
            project=self.project_1,
        )
        UploadsDataset.objects.create(folder=folder_1)

        # Create a admin membership for user_2 in project_1
        project_membership = ProjectMembership.objects.create(
            project=self.project_1,
            member=initial_users["user_2"],
            is_project_admin=True,
            can_create_folders=True,
        )

        # Check initial state for user_2, another admin
        assert ProjectMembership.objects.filter(project=self.project_1, member=initial_users["user_2"]).exists()
        assert project_membership.is_project_admin
        assert project_membership.can_create_folders
        assert FolderPermission.objects.filter(
            project_membership__project=self.project_1,
            project_membership__member=initial_users["user_2"],
        ).exists()

        admin_membership = ProjectMembership.objects.get(
            project=self.project_1,
            member=initial_users["user_1"],
        )

        # Check initial state for user_1, the admin
        assert ProjectMembership.objects.filter(project=self.project_1, member=initial_users["user_1"]).exists()
        assert admin_membership.is_project_admin
        assert admin_membership.can_create_folders

        assert ProjectMembership.objects.filter(project=self.project_1).count() == 2

        # one admin can be deleted
        url = reverse(
            "project-membership-detail",
            kwargs={
                "pk": project_membership.pk,
            },
        )

        response = client.delete(url)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert ProjectMembership.objects.filter(project=self.project_1).count() == 1
        # the membership should be deleted
        assert not ProjectMembership.objects.filter(
            project=self.project_1,
            member=initial_users["user_2"],
        ).exists()
        # the folder permissions should have been deleted as well
        assert not FolderPermission.objects.filter(
            project_membership__project=self.project_1,
            project_membership__member=initial_users["user_2"],
        ).exists()

        # the last admin cannot be deleted
        url = reverse(
            "project-membership-detail",
            kwargs={
                "pk": admin_membership.pk,
            },
        )

        response = client.delete(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN

        assert ProjectMembership.objects.filter(project=self.project_1).count() == 1

        updated_membership = ProjectMembership.objects.get(pk=admin_membership.pk)
        assert updated_membership.is_project_admin
        assert updated_membership.can_create_folders

    def test_prevent_demotion_of_last_admin(self, client, initial_users):
        """
        Ensure we cannot demote the last admin of a project.
        """
        # create a folder and a dataset so the project isn't empty
        folder_1 = Folder.objects.create(
            name="Folder 1",
            project=self.project_1,
        )
        UploadsDataset.objects.create(folder=folder_1)

        admin_membership = ProjectMembership.objects.get(
            project=self.project_1,
            member=initial_users["user_1"],
        )

        # Check initial state for user_1, the admin
        assert ProjectMembership.objects.filter(project=self.project_1, member=initial_users["user_1"]).exists()
        assert admin_membership.is_project_admin
        assert admin_membership.can_create_folders

        # Prepare update data
        url = reverse("project-membership-detail", kwargs={"pk": admin_membership.pk})
        update_data = {
            "is_project_admin": False,
            "can_create_folders": False,
        }

        response = client.patch(url, update_data, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN

        assert ProjectMembership.objects.filter(project=self.project_1).count() == 1

        updated_membership = ProjectMembership.objects.get(pk=admin_membership.pk)
        assert updated_membership.is_project_admin
        assert updated_membership.can_create_folders
