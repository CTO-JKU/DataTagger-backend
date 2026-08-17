from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import EmailField
from django.db.models.functions import Lower

__all__ = [
    "AuthenticateByEmailPasswordBackend",
]

EmailField.register_lookup(Lower)


class AuthenticateByEmailPasswordBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        User = get_user_model()

        if username is None:
            username = kwargs.get(User.USERNAME_FIELD)

        try:
            user = User.objects.get(email__iexact=username.lower())
        except User.DoesNotExist:
            return None
        else:
            if user.check_password(password) and self.user_can_authenticate(user):
                return user

        return None
