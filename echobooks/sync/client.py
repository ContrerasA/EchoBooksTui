"""HTTP client for the EchoBooks sync server.

Talks to the server's ``/auth/*`` and ``/sync/*`` endpoints over HTTPS with a
bearer JWT. Mirrors the ownership pattern of
:class:`~echobooks.providers.registry.ProviderRegistry`: the app creates one,
hands it the shared :class:`httpx.AsyncClient`, and closes it on unmount.

Depends only on httpx + pydantic — never on :mod:`echobooks.server`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel

from echobooks.sync.serialize import SyncPayload

if TYPE_CHECKING:
    from echobooks.config import Settings


class DeviceLogin(BaseModel):
    """What the server returns to start a device-flow login."""

    device_code: str
    user_code: str
    verification_uri: str
    interval: int = 5
    expires_in: int = 600


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    email: str = ""


class AuthPending(Exception):
    """Raised while the user hasn't approved the device login yet."""


class AuthError(Exception):
    """Login failed or expired."""


class SyncClient:
    """Stateless-ish wrapper; reads tokens from the shared :class:`Settings`."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        # Reuse a shared client if given (app-owned); otherwise own one.
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            headers={"Accept": "application/json"},
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # -- helpers ---------------------------------------------------------- #
    @property
    def _base(self) -> str:
        return self.settings.server_url.rstrip("/")

    def _auth_headers(self) -> dict[str, str]:
        token = self.settings.access_token
        return {"Authorization": f"Bearer {token}"} if token else {}

    async def _request(self, method: str, path: str, **kw: object) -> httpx.Response:
        """Authenticated request with one transparent token refresh on 401."""
        url = f"{self._base}{path}"
        headers = {**self._auth_headers(), **kw.pop("headers", {})}  # type: ignore[dict-item]
        resp = await self._client.request(method, url, headers=headers, **kw)  # type: ignore[arg-type]
        if resp.status_code == 401 and self.settings.refresh_token:
            await self.refresh()
            headers = {**self._auth_headers(), **headers}
            resp = await self._client.request(method, url, headers=headers, **kw)  # type: ignore[arg-type]
        resp.raise_for_status()
        return resp

    # -- auth ------------------------------------------------------------- #
    async def start_device_login(self) -> DeviceLogin:
        resp = await self._client.post(f"{self._base}/auth/device/start")
        resp.raise_for_status()
        return DeviceLogin.model_validate(resp.json())

    async def poll_device_login(self, device_code: str) -> TokenPair:
        """Poll once. Raises :class:`AuthPending` until the user approves."""
        resp = await self._client.post(
            f"{self._base}/auth/device/poll", json={"device_code": device_code}
        )
        if resp.status_code == 202:  # still waiting
            raise AuthPending
        if resp.status_code >= 400:
            raise AuthError(resp.text)
        return TokenPair.model_validate(resp.json())

    async def refresh(self) -> None:
        resp = await self._client.post(
            f"{self._base}/auth/refresh",
            json={"refresh_token": self.settings.refresh_token},
        )
        if resp.status_code >= 400:
            raise AuthError("token refresh failed")
        pair = TokenPair.model_validate(resp.json())
        self.settings.access_token = pair.access_token
        self.settings.refresh_token = pair.refresh_token or self.settings.refresh_token
        self.settings.save()

    # -- sync (SyncTransport protocol) ------------------------------------ #
    async def push(self, payload: SyncPayload) -> None:
        await self._request("POST", "/sync/push", json=payload.model_dump(mode="json"))

    async def pull(self, since: str | None) -> SyncPayload:
        params = {"since": since} if since else {}
        resp = await self._request("GET", "/sync/pull", params=params)
        return SyncPayload.model_validate(resp.json())


__all__ = [
    "AuthError",
    "AuthPending",
    "DeviceLogin",
    "SyncClient",
    "TokenPair",
]
