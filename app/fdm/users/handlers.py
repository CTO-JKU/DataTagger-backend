from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models.signals import pre_delete, pre_save
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _
from django_rest_passwordreset.signals import reset_password_token_created

from fdm.users.models import User


@receiver(reset_password_token_created)
def password_reset_token_created(
    sender: any,
    reset_password_token: any,
    account_created: bool = False,
    *args,
    **kwargs,
):
    """
    Handles password reset tokens
    When a token is created, an email needs to be sent to the user
    :param sender: View Class that sent the signal
    :param reset_password_token: Token Model Object
    :param account_created: Whether a user account has been created
    :param args:
    :param kwargs:
    :return:
    """

    # Send welcome email
    if account_created:
        reset_password_token.user.send_email_notification_for_account_creation(
            reset_password_token=reset_password_token.key,
        )

    # Check if the user is an internal user and immediately delete the reset token as the user should only
    # use authentication via Shibboleth and must not reset his password to circumvent this measurement.
    elif reset_password_token.user.is_internal_user:
        reset_password_token.delete()

    # Send password reset email
    else:
        reset_password_token.user.send_email_notification_for_forgotten_password(
            reset_password_token=reset_password_token.key,
        )


@receiver(pre_save, sender=User)
def pre_save(sender, instance, **kwargs):
    if not instance.settings:
        instance.settings = {}

    if not isinstance(instance.settings, dict):
        raise ValidationError(_("Settings value must be of type dict"))

    # If the user is created, then we don't need to check anything
    if instance.pk is None:
        return

    # Get the old value for this user if it already exists, else abort
    try:
        old_user_data = User.objects.get(pk=instance.pk)
    except User.DoesNotExist:
        return

    # Check if the superuser flag has been dropped for this user
    if not (old_user_data.is_superuser and not instance.is_superuser):
        return

    # Check if there are still other superusers left
    other_superusers = (
        User.objects.filter(
            is_superuser=True,
        )
        .exclude(
            pk=instance.pk,
        )
        .count()
    )

    if not other_superusers:
        raise PermissionDenied(_("You must not delete the last superuser."))


@receiver(pre_delete, sender=User)
def pre_delete(sender, instance, **kwargs):
    if instance.is_superuser:
        raise PermissionDenied(_("You must not delete superusers."))
