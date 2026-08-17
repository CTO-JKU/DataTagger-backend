import datetime
import re

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import Group as DjangoGroup
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.db import models
from django.db.models import UniqueConstraint
from django.db.models.functions import Lower
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _
from django_userforeignkey.request import get_current_user

from fdm.core.models import BaseModel
from fdm.core.tasks import send_mail_via_celery
from fdm.dbsettings.functions import get_dbsettings_value
from fdm.users.models.managers import *

__all__ = [
    "get_user_role_for_folder_permission",
    "get_contact_email",
    "User",
    "Group",
]


def get_user_role_for_folder_permission(folder_permission: any) -> str:
    if folder_permission.is_folder_admin:
        return _("Admin")
    elif folder_permission.is_metadata_template_admin:
        return _("Metadata template admin")
    elif folder_permission.can_edit:
        return _("Editor")

    return _("Viewer")


def get_contact_email() -> str:
    return get_dbsettings_value("CONTACT_EMAIL", "") or settings.EMAIL_SENDER


class User(BaseModel, AbstractUser):
    class AuthenticationProvider(models.TextChoices):
        APPLICATION = "APPLICATION", _("Application")
        SHIBBOLETH = "SHIBBOLETH", _("Shibboleth")

    objects = UserManager()

    username_validator = UnicodeUsernameValidator()

    username = models.CharField(
        verbose_name=_("username"),
        max_length=150,
        unique=False,
        help_text=_(
            "Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only.",
        ),
        validators=[username_validator],
    )

    email = models.EmailField(
        verbose_name=_("email address"),
        unique=True,
        blank=False,
        null=False,
    )

    can_create_projects = models.BooleanField(
        default=False,
    )

    can_hard_delete_datasets = models.BooleanField(
        default=False,
    )

    is_global_metadata_template_admin = models.BooleanField(
        default=False,
    )

    is_global_approval_queue_admin = models.BooleanField(
        default=False,
    )

    authentication_provider = models.CharField(
        max_length=16,
        choices=AuthenticationProvider.choices,
        default=AuthenticationProvider.APPLICATION,
    )

    given_name = models.CharField(
        max_length=255,
        blank=True,
        null=True,
    )

    sn = models.CharField(
        max_length=255,
        blank=True,
        null=True,
    )

    edu_person_affiliation = models.CharField(
        max_length=255,
        blank=True,
        null=True,
    )

    im_org_zug_mitarbeiter = models.CharField(
        max_length=255,
        blank=True,
        null=True,
    )

    im_org_zug_gast = models.CharField(
        max_length=255,
        blank=True,
        null=True,
    )

    im_org_zug_student = models.CharField(
        max_length=255,
        blank=True,
        null=True,
    )

    im_akademischer_grad = models.CharField(
        max_length=255,
        blank=True,
        null=True,
    )

    im_titel_anrede = models.CharField(
        max_length=255,
        blank=True,
        null=True,
    )

    im_titel_pre = models.CharField(
        max_length=255,
        blank=True,
        null=True,
    )

    im_titel_post = models.CharField(
        max_length=255,
        blank=True,
        null=True,
    )

    is_anonymized = models.BooleanField(
        default=False,
    )

    settings = models.JSONField(
        blank=True,
        null=False,
        default=dict,
    )

    storage_size = models.BigIntegerField(
        blank=False,
        null=False,
        default=0,
    )

    def update_storage_size(self) -> None:
        from django.contrib.contenttypes.models import ContentType

        from fdm.core.helpers import get_metadata_value_file_size
        from fdm.metadata.models import Metadata
        from fdm.uploads.models import UploadsVersionFile

        total_size = 0
        file_ids = list(
            UploadsVersionFile.objects.filter(
                created_by=self.pk,
            ).values_list("id", flat=True),
        )

        if file_ids:
            metadata_values = Metadata.objects.filter(
                assigned_to_content_type=ContentType.objects.get_for_model(UploadsVersionFile),
                assigned_to_object_id__in=file_ids,
                custom_key="FILE_SIZE",
            ).values_list("value", flat=True)

            for metadata_value in metadata_values:
                total_size += get_metadata_value_file_size(metadata_value)

        self.storage_size = total_size
        self.save(update_fields=["storage_size"])

    @property
    def display_name(self) -> str:
        if self.first_name and self.last_name:
            return f"{self.first_name} {self.last_name}"

        return self.email

    @property
    def is_internal_user(self) -> bool:
        internal_tlds = get_dbsettings_value("INTERNAL_TLDS")

        if not internal_tlds:
            return False

        return self.email.split("@")[1].lower() in [
            tld.replace("@", "").lower().strip() for tld in internal_tlds.replace(",", "\n").split("\n")
        ]

    @property
    def is_external_user(self) -> bool:
        return not self.is_internal_user

    USERNAME_FIELD = "email"

    REQUIRED_FIELDS = [
        "username",
    ]

    class Meta:
        constraints = [
            UniqueConstraint(
                Lower("email"),
                name="email_unique",
            ),
        ]

    def anonymize(self):
        now = datetime.datetime.now()
        iso = re.sub(r"\D", "", now.isoformat())

        self.username = f"anonymous-user-{self.pk}"
        self.first_name = "Anonymous"
        self.last_name = "User"
        self.email = f"anonymous-user-{self.pk}-{iso}@domain.com"
        self.set_unusable_password()
        self.is_superuser = False
        self.is_staff = False
        self.is_active = False
        self.is_anonymized = True
        self.can_create_projects = False
        self.can_hard_delete_datasets = False
        self.authentication_provider = self.AuthenticationProvider.APPLICATION
        self.given_name = None
        self.sn = None
        self.edu_person_affiliation = None
        self.im_org_zug_mitarbeiter = None
        self.im_org_zug_gast = None
        self.im_org_zug_student = None
        self.im_akademischer_grad = None
        self.im_titel_anrede = None
        self.im_titel_pre = None
        self.im_titel_post = None

        self.save()

    def send_mail_via_celery(self, subject: str, message: str, html_message: str) -> int:
        if not self.is_active or self.is_anonymized:
            return 0

        return send_mail_via_celery(
            recipient_list=[
                self.email,
            ],
            subject=subject,
            message=message,
            html_message=html_message,
        )

    def send_mail(self, subject: str, context: dict, template_name: str):
        full_context = {
            "APP_TITLE": "TUM {}".format(get_dbsettings_value("APP_TITLE")),
            "APP_CONTACT_EMAIL": get_contact_email(),
            "APP_LEGAL_NOTICE_URL": "https://example.com/legal-notice",  # TODO: Make this configurable via settings
            "APP_PRIVACY_POLICY_URL": f"{settings.FRONTEND_WEB_URL}/privacy-policy",  # TODO: Make this configurable via settings
            "user": self.display_name,
            "subject": subject,
        }
        full_context.update(context)

        plaintext_message = render_to_string(
            template_name=f"{template_name}.txt",
            context=full_context,
        )

        html_message = render_to_string(
            template_name=f"{template_name}.html",
            context=full_context,
        )

        self.send_mail_via_celery(
            subject=subject,
            message=plaintext_message,
            html_message=html_message,
        )

    def send_email_notification_for_account_creation(self, reset_password_token: str):
        current_user = get_current_user()

        self.send_mail(
            subject=_("Account created at TUM {APP_TITLE}").format(
                APP_TITLE=get_dbsettings_value("APP_TITLE"),
            ),
            context={
                "current_user": current_user.display_name if current_user.pk else current_user,
                "is_external_user": self.is_external_user,
                "login_url": f"{settings.FRONTEND_WEB_URL}/login",
                "external_login_url": f"{settings.FRONTEND_WEB_URL}/external-login",
                "reset_password_url": f"{settings.FRONTEND_WEB_URL}/reset-password?token={reset_password_token}",
            },
            template_name="users/emails/account_creation",
        )

    def send_email_notification_for_forgotten_password(self, reset_password_token: str):
        self.send_mail(
            subject=_("Password reset at TUM {APP_TITLE}").format(
                APP_TITLE=get_dbsettings_value("APP_TITLE"),
            ),
            context={
                "reset_password_url": f"{settings.FRONTEND_WEB_URL}/reset-password?token={reset_password_token}",
            },
            template_name="users/emails/password_reset",
        )

    def send_email_notification_for_new_project(self, project: any):
        self.send_mail(
            subject=_("New project at TUM {APP_TITLE}").format(
                APP_TITLE=get_dbsettings_value("APP_TITLE"),
            ),
            context={
                "project_name": project.name,
                "project_creator": project.created_by.display_name,
                "project_url": f"{settings.FRONTEND_WEB_URL}/projects/{project.pk}/",
            },
            template_name="projects/emails/project_information",
        )

    def send_email_notification_for_new_project_membership(self, project: any):
        current_user = get_current_user()

        self.send_mail(
            subject=_("Welcome to {project_name} at TUM {APP_TITLE}").format(
                project_name=project.name,
                APP_TITLE=get_dbsettings_value("APP_TITLE"),
            ),
            context={
                "project_name": project.name,
                "current_user": current_user.display_name if current_user.pk else current_user,
                "project_url": f"{settings.FRONTEND_WEB_URL}/projects/{project.pk}/",
            },
            template_name="projects/emails/new_project_membership",
        )

    def send_email_notification_for_changed_project_membership(self, project: any):
        self.send_mail(
            subject=_("Your permissions have been changed for {project_name} at TUM {APP_TITLE}").format(
                project_name=project.name,
                APP_TITLE=get_dbsettings_value("APP_TITLE"),
            ),
            context={
                "project_name": project.name,
            },
            template_name="projects/emails/changed_project_membership",
        )

    def send_email_notification_for_deleted_project_membership(self, project: any):
        current_user = get_current_user()

        self.send_mail(
            subject=_("No access to {project_name} at TUM {APP_TITLE}").format(
                project_name=project.name,
                APP_TITLE=get_dbsettings_value("APP_TITLE"),
            ),
            context={
                "project_name": project.name,
                "current_user": current_user.display_name if current_user.pk else current_user,
            },
            template_name="projects/emails/deleted_project_membership",
        )

    def send_email_notification_for_new_folder_permission(self, folder_permission: any):
        current_user = get_current_user()

        self.send_mail(
            subject=_("New folder in {project_name} at TUM {APP_TITLE}").format(
                project_name=folder_permission.folder.project.name,
                APP_TITLE=get_dbsettings_value("APP_TITLE"),
            ),
            context={
                "project_name": folder_permission.folder.project.name,
                "folder_name": folder_permission.folder.name,
                "folder_role": get_user_role_for_folder_permission(folder_permission=folder_permission),
                "current_user": current_user.display_name if current_user.pk else current_user,
                "folder_url": f"{settings.FRONTEND_WEB_URL}/projects/{folder_permission.folder.project.pk}/folders/{folder_permission.folder.pk}/",
            },
            template_name="folders/emails/new_folder_permission",
        )

    def send_email_notification_for_changed_folder_permission(self, folder_permission: any):
        self.send_mail(
            subject=_(
                "Your permissions have been changed for {folder_name} in {project_name} at TUM {APP_TITLE}",
            ).format(
                folder_name=folder_permission.folder.name,
                project_name=folder_permission.folder.project.name,
                APP_TITLE=get_dbsettings_value("APP_TITLE"),
            ),
            context={
                "project_name": folder_permission.folder.project.name,
                "folder_name": folder_permission.folder.name,
                "folder_role": get_user_role_for_folder_permission(folder_permission=folder_permission),
                "folder_url": f"{settings.FRONTEND_WEB_URL}/projects/{folder_permission.folder.project.pk}/folders/{folder_permission.folder.pk}/",
            },
            template_name="folders/emails/changed_folder_permission",
        )

    def send_email_notification_for_deleted_folder_permission(self, folder_permission: any):
        current_user = get_current_user()

        self.send_mail(
            subject=_("No access to {folder_name} in {project_name} at TUM {APP_TITLE}").format(
                folder_name=folder_permission.folder.name,
                project_name=folder_permission.folder.project.name,
                APP_TITLE=get_dbsettings_value("APP_TITLE"),
            ),
            context={
                "project_name": folder_permission.folder.project.name,
                "folder_name": folder_permission.folder.name,
                "current_user": current_user.display_name if current_user.pk else current_user,
            },
            template_name="folders/emails/deleted_folder_permission",
        )

    def __str__(self):
        return self.username


class Group(DjangoGroup):
    objects = GroupManager()

    class Meta:
        proxy = True
