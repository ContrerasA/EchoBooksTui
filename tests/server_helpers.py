"""Shared helpers for server tests: a fake Google identity provider.

Generates an RSA keypair, exposes a JWKS document for it, and signs OIDC ID
tokens the way Google would. Tests mount the JWKS + token endpoints with respx so
the server's *real* verification path (PyJWKClient + RS256 + audience/issuer
checks) runs unchanged against tokens we control.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

KID = "test-key-1"
CLIENT_ID = "test-client-id.apps.googleusercontent.com"
ISSUER = "https://accounts.google.com"

# One keypair for the whole test session.
_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _b64u(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def jwks() -> dict:
    """The public JWKS document Google would publish for our key."""
    numbers = _private_key.public_key().public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": KID,
                "n": _b64u(numbers.n),
                "e": _b64u(numbers.e),
            }
        ]
    }


def jwks_json() -> str:
    return json.dumps(jwks())


def make_id_token(sub: str, email: str, *, aud: str = CLIENT_ID) -> str:
    """Sign an ID token the way Google's token endpoint returns one."""
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": ISSUER,
            "sub": sub,
            "email": email,
            "aud": aud,
            "iat": now,
            "exp": now + timedelta(hours=1),
        },
        _private_key,
        algorithm="RS256",
        headers={"kid": KID},
    )
