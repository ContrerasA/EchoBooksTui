"""Server sync: per-user isolation + last-write-wins, end-to-end over HTTP.

Logs in two distinct Google identities, pushes catalogs through ``/sync/push``,
and asserts that each user only ever pulls their own rows and that server-side
LWW keeps the newer edit.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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


def _login(client, *, sub: str, email: str) -> str:
    """Drive the device flow for one identity; return its access token."""
    respx.post(GOOGLE_DEVICE_CODE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "device_code": f"DEV-{sub}",
                "user_code": "WDJB-MJHT",
                "verification_url": "https://google.com/device",
            },
        )
    )
    respx.post(GOOGLE_TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"id_token": make_id_token(sub, email)})
    )
    respx.get(GOOGLE_CERTS_URL).mock(return_value=httpx.Response(200, json=jwks()))
    client.post("/auth/device/start")
    tokens = client.post("/auth/device/poll", json={"device_code": f"DEV-{sub}"}).json()
    return tokens["access_token"]


def _book_row(book_id: str, title: str, when: datetime) -> dict:
    return {
        "table": "book",
        "id": book_id,
        "updated_at": when.isoformat(),
        "deleted_at": None,
        "fields": {"title": title, "media_type": "PRINT", "status": "READ"},
        "authors": [],
        "narrators": [],
        "tags": [],
    }


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _push(client, token: str, *rows: dict):
    return client.post("/sync/push", json={"rows": list(rows)}, headers=_auth(token))


def _pull_rows(client, token: str, **params) -> list[dict]:
    return client.get("/sync/pull", params=params, headers=_auth(token)).json()["rows"]


def _book_titles(rows: list[dict]) -> set[str]:
    return {r["fields"]["title"] for r in rows if r["table"] == "book"}


def _row_by_id(rows: list[dict], row_id: str) -> dict | None:
    return next((r for r in rows if r["id"] == row_id), None)


@respx.mock
def test_push_then_pull_roundtrips(server_client):
    token = _login(server_client, sub="u1", email="u1@example.com")
    now = datetime.now(UTC)
    pushed = _push(server_client, token, _book_row("book-1", "Dune", now))
    assert pushed.status_code == 200
    # Server echoes the winning rows.
    assert any(r["id"] == "book-1" for r in pushed.json()["rows"])

    assert "Dune" in _book_titles(_pull_rows(server_client, token))


@respx.mock
def test_users_are_isolated(server_client):
    t1 = _login(server_client, sub="u1", email="u1@example.com")
    t2 = _login(server_client, sub="u2", email="u2@example.com")
    now = datetime.now(UTC)

    _push(server_client, t1, _book_row("b-a", "User One Book", now))
    _push(server_client, t2, _book_row("b-b", "User Two Book", now))

    assert _book_titles(_pull_rows(server_client, t1)) == {"User One Book"}
    assert _book_titles(_pull_rows(server_client, t2)) == {"User Two Book"}


@respx.mock
def test_same_id_different_users_is_rejected_safely(server_client):
    """A row id already owned by another user is refused, not overwritten.

    Client-generated UUIDs make a genuine cross-user id collision essentially
    impossible; this forces one to prove the server fails *safely* — user 1's row
    is untouched and user 2 simply doesn't get that row — instead of crashing on
    the primary-key conflict or leaking across owners.
    """
    t1 = _login(server_client, sub="u1", email="u1@example.com")
    t2 = _login(server_client, sub="u2", email="u2@example.com")
    now = datetime.now(UTC)

    _push(server_client, t1, _book_row("shared", "Mine", now))
    resp = _push(server_client, t2, _book_row("shared", "Yours", now))
    assert resp.status_code == 200  # safe: no crash
    assert resp.json()["rows"] == []  # nothing applied for user 2

    # User 1 keeps their row; user 2 sees none with that id.
    t1_row = _row_by_id(_pull_rows(server_client, t1), "shared")
    t2_row = _row_by_id(_pull_rows(server_client, t2), "shared")
    assert t1_row is not None and t1_row["fields"]["title"] == "Mine"
    assert t2_row is None


@respx.mock
def test_server_lww_keeps_newer(server_client):
    token = _login(server_client, sub="u1", email="u1@example.com")
    base = datetime.now(UTC)

    # Push v1, then an OLDER edit (should lose), then confirm v1 stands.
    _push(server_client, token, _book_row("b1", "Newer", base))
    _push(server_client, token, _book_row("b1", "Older", base - timedelta(hours=1)))
    assert _row_by_id(_pull_rows(server_client, token), "b1")["fields"]["title"] == "Newer"

    # Now a strictly NEWER edit wins.
    _push(server_client, token, _book_row("b1", "Newest", base + timedelta(hours=1)))
    assert _row_by_id(_pull_rows(server_client, token), "b1")["fields"]["title"] == "Newest"


@respx.mock
def test_pull_since_filters(server_client):
    token = _login(server_client, sub="u1", email="u1@example.com")
    old = datetime.now(UTC) - timedelta(days=2)
    new = datetime.now(UTC)
    _push(server_client, token, _book_row("old", "Old", old))
    _push(server_client, token, _book_row("new", "New", new))

    since = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    ids = {r["id"] for r in _pull_rows(server_client, token, since=since)}
    assert "new" in ids
    assert "old" not in ids
