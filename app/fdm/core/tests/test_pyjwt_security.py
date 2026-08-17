import base64
import hashlib
import hmac
import json

import jwt
import pytest


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _make_crit_token(secret: bytes) -> str:
    """Build a HS256 JWT with an unknown critical extension header."""
    header = {"alg": "HS256", "crit": ["x-custom-policy"], "x-custom-policy": "require-mfa"}
    payload = {"sub": "attacker", "role": "admin"}

    h = _b64url(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64url(hmac.new(secret, f"{h}.{p}".encode(), hashlib.sha256).digest())
    return f"{h}.{p}.{sig}"


@pytest.mark.django_db
class TestPyJWTCritHeader:
    def test_token_with_unknown_crit_extension_is_rejected(self):
        """
        CVE-2026-32597 regression: PyJWT must reject JWS tokens whose `crit`
        header lists extensions that PyJWT does not understand (RFC 7515 §4.1.11).
        Accepting such tokens allows policy-bypass attacks (e.g. silently ignoring
        MFA requirements encoded in custom critical headers).
        """
        secret = b"test-secret"
        token = _make_crit_token(secret)

        with pytest.raises(jwt.exceptions.InvalidTokenError):
            jwt.decode(token, secret, algorithms=["HS256"])
