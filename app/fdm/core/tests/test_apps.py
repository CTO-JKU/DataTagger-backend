import pytest
from django.apps import apps
from django.conf import settings
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType

from fdm.core.utils.permissions import get_permission_name

_LOCAL_APPS = [app for app in apps.get_app_configs() if "/app/fdm" in app.path]


@pytest.mark.django_db
@pytest.mark.parametrize("app", _LOCAL_APPS)
def test_app_module(app):
    """
    Checks that each local app has a proper AppConfig defined in apps.py.
    """
    assert hasattr(app, "module")
    assert hasattr(app, "name"), f"Make sure you defined an AppConfig in the 'apps.py' of the app '{app.label}'"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "app,model",
    [(app, model) for app in _LOCAL_APPS for model in app.get_models() if getattr(model._meta, "managed", True)],
)
@pytest.mark.parametrize("permission_check", settings.GLOBAL_MODEL_PERMISSIONS)
def test_availability_of_permissions(app, model, permission_check):
    """
    This test makes sure that all defined permissions are available for all local apps.
    """

    content_type = ContentType.objects.get_for_model(model)
    permission_name = get_permission_name(model, permission_check)

    permission = Permission.objects.filter(content_type=content_type, codename=permission_name.split(".")[1]).first()
    assert permission is not None, (
        f"Permission {permission_check} for model {model.__name__} is not available in "
        f"app {app.name}. Make sure that the 'apps.py' includes the post migrate "
        f"configuration as defined in the README.md"
    )
