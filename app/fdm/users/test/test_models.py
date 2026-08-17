import pytest
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError

from fdm.dbsettings.functions import get_dbsettings_value, set_dbsettings_value
from fdm.users.models import User, get_contact_email


@pytest.mark.django_db
class TestUserModel:
    def test_email_constraint(self):
        """
        Ensure we can't use the same email address for multiple users. The constraint must be case-insensitive.
        """
        User.objects.create_user(
            username="misterX",
            email="same@email.com",
            password="password",
        )

        with pytest.raises(ValidationError):
            User.objects.create_user(
                username="misterY",
                email="SAME@eMail.com",
                password="password",
            )

    def test_user_deletion(self):
        """
        Ensure we can delete a normal user.
        """
        user = User.objects.create_user(
            username="user",
            email="user@email.com",
            password="password",
        )
        user.delete()

    def test_superuser_protection(self):
        """
        Ensure we can't delete a superuser.
        """
        superuser = User.objects.create_superuser(
            username="superuser",
            email="superuser@email.com",
            password="password",
        )

        with pytest.raises(PermissionDenied):
            superuser.delete()

    def test_superuser_status_change_protection(self, initial_users):
        """
        Ensure we can't remove a superuser role if there's only one superuser left.
        Also, make sure that this does not affect normal users.
        """
        user_1 = initial_users["user_1"]

        # A normal user should not be affected if no superusers exists
        user_1.save()

        superuser_1 = User.objects.create_superuser(
            username="superuser_1",
            email="superuser_1@email.com",
            password="password",
        )

        # A normal user should not be affected by the amount of superusers in existence
        user_1.save()

        superuser_2 = User.objects.create_superuser(
            username="superuser_2",
            email="superuser_2@email.com",
            password="password",
        )

        # Degrade superuser 1
        superuser_1.is_superuser = False
        superuser_1.save()

        # Try to degrade superuser 2
        with pytest.raises(PermissionDenied):
            superuser_2.is_superuser = False
            superuser_2.save()

        # A normal user should not be affected by any superuser flag changes
        user_1.save()

    def test_anonymization(self, initial_users):
        """
        Ensure we can anonymize a user.
        """
        initial_users["user_1"].anonymize()

        assert initial_users["user_1"].username == f"anonymous-user-{initial_users['user_1'].pk}"
        assert initial_users["user_1"].first_name == "Anonymous"
        assert initial_users["user_1"].last_name == "User"
        assert initial_users["user_1"].email.startswith(f"anonymous-user-{initial_users['user_1'].pk}-") is True
        assert initial_users["user_1"].has_usable_password() is False
        assert initial_users["user_1"].is_superuser is False
        assert initial_users["user_1"].is_staff is False
        assert initial_users["user_1"].is_active is False
        assert initial_users["user_1"].is_anonymized is True

    def test_contact_email(self):
        """
        Ensure we get the correct contact email address.
        """
        assert get_dbsettings_value("CONTACT_EMAIL", "") == ""
        assert get_contact_email() == settings.EMAIL_SENDER

        email = "contact@domain.com"
        set_dbsettings_value("CONTACT_EMAIL", email)
        assert get_dbsettings_value("CONTACT_EMAIL", "") == email
        assert get_contact_email() == email

    def test_internal_user(self):
        """
        Ensure we get the correct classification of a user based on the top level domain of his email address.
        """
        user_1_internal = User.objects.create_user("user_1_internal", "user_1_internal@corp.org", "password")
        user_2_internal = User.objects.create_user("user_2_internal", "user_2_internal@corp2.org", "password")
        user_3_external = User.objects.create_user("user_3_external", "user_3_external@external.com", "password")

        assert get_dbsettings_value("INTERNAL_TLDS", "") == ""
        assert user_1_internal.is_internal_user is False
        assert user_2_internal.is_internal_user is False
        assert user_3_external.is_internal_user is False

        internal_tlds = "corp.org\ncorp2.org"
        set_dbsettings_value("INTERNAL_TLDS", internal_tlds)

        assert get_dbsettings_value("INTERNAL_TLDS", "") == internal_tlds
        assert user_1_internal.is_internal_user is True
        assert user_2_internal.is_internal_user is True
        assert user_3_external.is_internal_user is False

        internal_tlds = " @corp.org, @corp2.org "
        set_dbsettings_value("INTERNAL_TLDS", internal_tlds)

        assert get_dbsettings_value("INTERNAL_TLDS", "") == internal_tlds
        assert user_1_internal.is_internal_user is True
        assert user_2_internal.is_internal_user is True
        assert user_3_external.is_internal_user is False

    def test_external_user(self):
        """
        Ensure we get the correct classification of a user based on the top level domain of his email address.
        """
        user_1_internal = User.objects.create_user("user_1_internal", "user_1_internal@corp.org", "password")
        user_2_internal = User.objects.create_user("user_2_internal", "user_2_internal@corp2.org", "password")
        user_3_external = User.objects.create_user("user_3_external", "user_3_external@external.com", "password")

        assert get_dbsettings_value("INTERNAL_TLDS", "") == ""
        assert user_1_internal.is_external_user is True
        assert user_2_internal.is_external_user is True
        assert user_3_external.is_external_user is True

        internal_tlds = "corp.org\ncorp2.org"
        set_dbsettings_value("INTERNAL_TLDS", internal_tlds)

        assert get_dbsettings_value("INTERNAL_TLDS", "") == internal_tlds
        assert user_1_internal.is_external_user is False
        assert user_2_internal.is_external_user is False
        assert user_3_external.is_external_user is True

        internal_tlds = " @corp.org, @corp2.org "
        set_dbsettings_value("INTERNAL_TLDS", internal_tlds)

        assert get_dbsettings_value("INTERNAL_TLDS", "") == internal_tlds
        assert user_1_internal.is_external_user is False
        assert user_2_internal.is_external_user is False
        assert user_3_external.is_external_user is True

    def test_settings_field(self, initial_users):
        """
        Ensure the settings field returns JSON data as a dict
        """
        user_1 = initial_users["user_1"]

        user_1.settings = {
            "custom_key": "custom_value",
            "some_boolean": True,
        }
        user_1.save()

        assert user_1.settings.get("custom_key", None) == "custom_value"
        assert user_1.settings.get("some_boolean", None) is True

        user_1.settings = ""
        user_1.save()
        assert user_1.settings == {}

        user_1.settings = None
        user_1.save()
        assert user_1.settings == {}

        with pytest.raises(ValidationError):
            user_1.settings = "string value"
            user_1.save()
