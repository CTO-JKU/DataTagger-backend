import logging

from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)

User = get_user_model()


def fake_rest_auth(auth_token, scope, *args, **kwargs):
    user = None
    request = None
    if auth_token:
        try:
            from rest_framework_simplejwt.tokens import AccessToken

            validated_token = AccessToken(auth_token)
            user = User.objects.get(id=validated_token["user_id"])
        except Exception as e:
            logger.error(e)
    return user, request
