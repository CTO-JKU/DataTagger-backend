from django.conf import settings
from rest_framework_simplejwt.authentication import JWTAuthentication


class JWTCookieAuthentication(JWTAuthentication):
    """
    Extends simplejwt's JWTAuthentication to also check for
    a JWT token in cookies (matching the old drf-jwt behavior).
    """

    def authenticate(self, request):
        header_result = super().authenticate(request)
        if header_result is not None:
            return header_result

        cookie_name = settings.SIMPLE_JWT.get("AUTH_COOKIE", "token")
        raw_token = request.COOKIES.get(cookie_name)
        if raw_token is None:
            return None

        validated_token = self.get_validated_token(raw_token)
        return self.get_user(validated_token), validated_token
