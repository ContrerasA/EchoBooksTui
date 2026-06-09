"""Google device-flow login + EchoBooks JWT issuance.

Flow (RFC 8628, Google's "TV and Limited Input devices" client):
1. TUI calls ``/auth/device/start`` -> we ask Google for a device + user code and
   relay them to the TUI, which shows the URL + code to the user.
2. The user opens the URL on a phone/laptop and approves.
3. The TUI polls ``/auth/device/poll``; we exchange the device code with Google.
   While the user hasn't approved we return 202; on success Google hands back an
   OIDC ``id_token`` which we verify to get the user's ``sub`` + ``email``.
4. We upsert the ``User`` and mint our *own* access + refresh JWTs. From then on
   the TUI authenticates to us with our token — never Google's.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import httpx
import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from echobooks.server.config import ServerSettings, get_settings
from echobooks.server.db import get_session
from echobooks.server.models import User

router = APIRouter(prefix="/auth", tags=["auth"])

GOOGLE_DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
SCOPE = "openid email profile"


# --------------------------------------------------------------------------- #
# Request / response DTOs
# --------------------------------------------------------------------------- #
class DeviceStartResponse(BaseModel):
    device_code: str
    user_code: str
    verification_uri: str
    interval: int = 5
    expires_in: int = 600


class DevicePollRequest(BaseModel):
    device_code: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    email: str = ""


# --------------------------------------------------------------------------- #
# JWT helpers
# --------------------------------------------------------------------------- #
def _issue(user: User, settings: ServerSettings) -> TokenResponse:
    now = datetime.now(UTC)
    access = jwt.encode(
        {"sub": user.id, "email": user.email, "type": "access",
         "exp": now + timedelta(seconds=settings.jwt_access_ttl)},
        settings.jwt_secret,
        algorithm="HS256",
    )
    refresh = jwt.encode(
        {"sub": user.id, "type": "refresh",
         "exp": now + timedelta(seconds=settings.jwt_refresh_ttl)},
        settings.jwt_secret,
        algorithm="HS256",
    )
    return TokenResponse(access_token=access, refresh_token=refresh, email=user.email)


def _decode(token: str, settings: ServerSettings) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from exc


# --------------------------------------------------------------------------- #
# Current-user dependency
# --------------------------------------------------------------------------- #
def current_user(
    authorization: Annotated[str | None, Header()] = None,
    session: Session = Depends(get_session),
    settings: ServerSettings = Depends(get_settings),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    payload = _decode(authorization.split(" ", 1)[1], settings)
    if payload.get("type") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not an access token")
    user = session.get(User, payload.get("sub"))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown user")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


# --------------------------------------------------------------------------- #
# Google ID-token verification
# --------------------------------------------------------------------------- #
async def _verify_google_id_token(id_token: str, settings: ServerSettings) -> dict[str, Any]:
    """Verify the OIDC ID token against Google's published signing keys.

    We fetch Google's JWKS over httpx (so it shares the server's HTTP stack and is
    mockable in tests), select the key matching the token's ``kid``, and verify
    signature + audience + issuer + expiry.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(GOOGLE_CERTS_URL)
        resp.raise_for_status()
        jwks = resp.json()
    try:
        header = jwt.get_unverified_header(id_token)
        jwk_set = jwt.PyJWKSet.from_dict(jwks)
        signing_key = next(k for k in jwk_set.keys if k.key_id == header["kid"])
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.google_client_id,
            issuer=list(GOOGLE_ISSUERS),
        )
    except (jwt.PyJWTError, KeyError, StopIteration) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid Google token") from exc
    return claims


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@router.post("/device/start", response_model=DeviceStartResponse)
async def device_start(
    settings: ServerSettings = Depends(get_settings),
) -> DeviceStartResponse:
    if not settings.configured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "server not configured")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            GOOGLE_DEVICE_CODE_URL,
            data={"client_id": settings.google_client_id, "scope": SCOPE},
        )
    if resp.status_code >= 400:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Google device-code request failed")
    data = resp.json()
    return DeviceStartResponse(
        device_code=data["device_code"],
        user_code=data["user_code"],
        # Google calls it verification_url; OIDC spec calls it verification_uri.
        verification_uri=data.get("verification_url") or data["verification_uri"],
        interval=data.get("interval", 5),
        expires_in=data.get("expires_in", 600),
    )


@router.post("/device/poll", response_model=None)
async def device_poll(
    body: DevicePollRequest,
    response: Response,
    session: Session = Depends(get_session),
    settings: ServerSettings = Depends(get_settings),
) -> TokenResponse | Response:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "device_code": body.device_code,
                "grant_type": GOOGLE_DEVICE_GRANT,
            },
        )
    data = resp.json()
    if resp.status_code >= 400:
        # Still waiting for the user to approve → 202 tells the client to keep
        # polling. Any other error is terminal.
        if data.get("error") in ("authorization_pending", "slow_down"):
            return Response(status_code=status.HTTP_202_ACCEPTED)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, data.get("error", "login failed"))

    claims = await _verify_google_id_token(data["id_token"], settings)
    sub, email = claims["sub"], claims.get("email", "")

    user = session.scalars(select(User).where(User.google_sub == sub)).first()
    if user is None:
        user = User(google_sub=sub, email=email)
        session.add(user)
        session.flush()
    elif email and user.email != email:
        user.email = email
    return _issue(user, settings)


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    body: RefreshRequest,
    session: Session = Depends(get_session),
    settings: ServerSettings = Depends(get_settings),
) -> TokenResponse:
    payload = _decode(body.refresh_token, settings)
    if payload.get("type") != "refresh":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not a refresh token")
    user = session.get(User, payload.get("sub"))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown user")
    return _issue(user, settings)
