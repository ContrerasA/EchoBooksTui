"""Server auth: Google device flow → our JWTs → authenticated access.

Google's device-code, token, and JWKS endpoints are mocked with respx; the
server's real verification (RS256 against the JWKS, audience/issuer checks) runs
against tokens we sign with a test keypair (tests/server_helpers).
"""

from __future__ import annotations

import httpx
import pytest
import respx

pytest.importorskip("fastapi")

from echobooks.server.auth import (  # noqa: E402
    GOOGLE_CERTS_URL,
    GOOGLE_DEVICE_CODE_URL,
    GOOGLE_TOKEN_URL,
)
from tests.server_helpers import jwks, make_id_token  # noqa: E402


def _mock_google(*, sub: str, email: str, pending_first: bool = True) -> None:
    respx.post(GOOGLE_DEVICE_CODE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "device_code": "DEV-CODE",
                "user_code": "WDJB-MJHT",
                "verification_url": "https://google.com/device",
                "interval": 5,
                "expires_in": 600,
            },
        )
    )
    id_token = make_id_token(sub, email)
    token_responses = []
    if pending_first:
        token_responses.append(
            httpx.Response(400, json={"error": "authorization_pending"})
        )
    token_responses.append(httpx.Response(200, json={"id_token": id_token}))
    respx.post(GOOGLE_TOKEN_URL).mock(side_effect=token_responses)
    respx.get(GOOGLE_CERTS_URL).mock(return_value=httpx.Response(200, json=jwks()))


@respx.mock
def test_device_flow_issues_jwt(server_client):
    _mock_google(sub="google-sub-1", email="reader@example.com")

    start = server_client.post("/auth/device/start")
    assert start.status_code == 200
    body = start.json()
    assert body["user_code"] == "WDJB-MJHT"
    assert body["verification_uri"] == "https://google.com/device"

    # First poll: Google says pending → 202.
    pending = server_client.post("/auth/device/poll", json={"device_code": "DEV-CODE"})
    assert pending.status_code == 202

    # Second poll: approved → our tokens.
    ok = server_client.post("/auth/device/poll", json={"device_code": "DEV-CODE"})
    assert ok.status_code == 200
    tokens = ok.json()
    assert tokens["access_token"] and tokens["refresh_token"]
    assert tokens["email"] == "reader@example.com"

    # The access token authenticates a sync call.
    pull = server_client.get(
        "/sync/pull", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert pull.status_code == 200
    assert pull.json() == {"rows": []}


@respx.mock
def test_refresh_rotates_access_token(server_client):
    _mock_google(sub="google-sub-2", email="x@example.com", pending_first=False)
    server_client.post("/auth/device/start")
    tokens = server_client.post(
        "/auth/device/poll", json={"device_code": "DEV-CODE"}
    ).json()

    refreshed = server_client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"]


def test_sync_requires_auth(server_client):
    assert server_client.get("/sync/pull").status_code == 401
    assert server_client.post("/sync/push", json={"rows": []}).status_code == 401


def test_health_is_open(server_client):
    assert server_client.get("/health").json() == {"status": "ok"}
