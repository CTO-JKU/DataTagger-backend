import logging

from asgiref.sync import async_to_sync
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.test import RequestFactory
from django.utils.translation import gettext_lazy as _
from django_rest_passwordreset.signals import reset_password_token_created
from django_rest_passwordreset.views import generate_token_for_email
from django_userforeignkey.request import set_current_request

from fdm.dbsettings.functions import get_dbsettings_value

__all__ = [
    "get_content_type_for_object",
    "get_content_type_for_model",
    "get_content_type_instance",
    "clean_assigned_content_type",
    "get_max_lock_time",
    "get_metadata_value_file_size",
    "format_storage_size",
    "set_request_for_user",
    "get_or_create_user",
    "send_websocket_message",
    "check_empty_value",
    "version_diff_sort_key",
]


User = get_user_model()

logger = logging.getLogger(__name__)


def set_request_for_user(user):
    request = RequestFactory().request(**{})
    setattr(request, "user", user)
    set_current_request(request)

    # Check if the user has been set correctly
    request_user = getattr(request, "user")
    if request_user == user:
        logger.debug(f"User for request has been set to: {getattr(request, 'user')}")
    else:
        raise ValueError("Could net set user for request")

    return request


def get_content_type_for_object(content_type_object: any) -> str:
    return f"{content_type_object.app_label}.{content_type_object.model}"


def get_content_type_for_model(model: any) -> str:
    from django.contrib.contenttypes.models import ContentType

    content_type = ContentType.objects.get_for_model(model)

    return get_content_type_for_object(content_type)


def get_content_type_instance(content_type: any) -> any:
    from django.contrib.contenttypes.models import ContentType

    if isinstance(content_type, ContentType):
        return content_type

    try:
        app_label, model = content_type.split(".")
    except AttributeError:
        return None
    except ValueError:
        raise ValueError(
            _("Invalid content type string: {content_type}").format(
                content_type=content_type,
            ),
        )

    return ContentType.objects.get(
        app_label=app_label,
        model=model,
    )


def clean_assigned_content_type(assigned_to_content_type, assigned_to_object_id, allowed_content_models):
    from django.contrib.contenttypes.models import ContentType

    if bool(assigned_to_content_type) ^ bool(assigned_to_object_id):
        raise ValidationError(_("You must either provide a content type and an object id or none of them."))

    elif assigned_to_content_type and assigned_to_object_id:
        # Check if the content type to link is allowed
        if assigned_to_content_type not in [
            ContentType.objects.get_for_model(allowed_content_model) for allowed_content_model in allowed_content_models
        ]:
            raise ValidationError(_("You must not link this object to this content type."))

        # Check if the object id and the content type match an actual data object
        model = assigned_to_content_type.model_class()
        try:
            model.objects.get(pk=assigned_to_object_id)
        except model.DoesNotExist:
            raise ValidationError(_("An object type with this object id does not exist."))


def get_max_lock_time() -> int:
    time = get_dbsettings_value("MAX_LOCK_TIME", settings.MAX_LOCK_TIME)

    try:
        return int(time)
    except ValueError:
        return settings.MAX_LOCK_TIME


def get_metadata_value_file_size(metadata_value: any) -> int:
    if not isinstance(metadata_value, dict):
        return 0

    raw_value = metadata_value.get("value")

    if raw_value is None:
        return 0

    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return 0


def format_storage_size(storage_size: any) -> str:
    try:
        size = float(storage_size)
    except (TypeError, ValueError):
        size = 0

    if size < 0:
        size = 0

    if size >= 1024**4:
        return f"{size / (1024**4):.2f} TB"
    if size >= 1024**3:
        return f"{size / (1024**3):.2f} GB"
    if size >= 1024**2:
        return f"{size / (1024**2):.2f} MB"
    if size >= 1024:
        return f"{size / 1024:.2f} KB"

    return f"{int(size)} Byte"


def get_or_create_user(email: str, notification: bool = True) -> User:
    email = email.lower()
    validate_email(email)

    try:
        return User.objects.get(email__iexact=email)
    except User.DoesNotExist:
        user = User.objects.create_user(
            username=email.split("@")[0],
            email=email,
            password=make_password(None),
        )

        if notification:
            reset_password_token_created.send(
                sender=user.__class__,
                instance=user,
                reset_password_token=generate_token_for_email(email),
                account_created=True,
            )

        return user


def send_websocket_message(channel_layer, group_name, message):
    async_to_sync(channel_layer.group_send)(
        group_name,
        message,
    )


def check_empty_value(value: any) -> any:
    if isinstance(value, str) and value.strip(" ") == "":
        return None

    return value


def version_diff_sort_key(item):
    new_value = item["new"]
    old_value = item["old"]

    # Criteria 1: new is not None, and old is None
    if new_value is not None and old_value is None:
        return 0, 0
    # Criteria 2: new is not None, and old is not None
    elif new_value is not None and old_value is not None:
        return 1, 0
    # Criteria 3: new is None, and old is not None
    elif new_value is None and old_value is not None:
        return 2, 0
    # Default criteria
    else:
        return 3, 0
