"""Shared fixtures: an isolated in-memory database per test (client + server)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from echobooks.db import session as dbsession


@pytest.fixture(autouse=True)
def _no_real_keyring(monkeypatch, request):
    """Keep tests off the developer's real OS keyring by default.

    Settings.save() would otherwise write account tokens into the live keyring on
    a desktop dev box. Tests that specifically exercise the keyring opt back in by
    requesting the ``mem_keyring`` fixture (which activates an in-memory backend);
    everything else runs through the JSON fallback.
    """
    if "mem_keyring" not in request.fixturenames:
        monkeypatch.setenv("ECHOBOOKS_NO_KEYRING", "1")


@pytest.fixture
def session() -> Iterator[Session]:
    """A Session bound to a fresh in-memory SQLite DB, reset between tests."""
    dbsession._engine = None
    dbsession._Session = None
    dbsession.init_db("sqlite:///:memory:")
    s = dbsession.get_sessionmaker()()
    try:
        yield s
        s.commit()
    finally:
        s.close()
        dbsession._engine = None
        dbsession._Session = None


# --------------------------------------------------------------------------- #
# Server fixtures (require the `server` extra; skipped cleanly if it's absent)
# --------------------------------------------------------------------------- #
@pytest.fixture
def server_client(monkeypatch, tmp_path) -> Iterator[object]:
    """A FastAPI TestClient backed by a fresh in-memory SQLite 'Postgres'.

    Configures Google/JWT env, points the server DB at SQLite (the ORM is
    dialect-agnostic for our needs), and resets the server's cached settings +
    engine so each test is isolated.
    """
    pytest.importorskip("fastapi")
    from tests.server_helpers import CLIENT_ID

    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    # A file-backed SQLite DB shared across connections for this test.
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/server.db")

    from fastapi.testclient import TestClient

    from echobooks.server import db as serverdb
    from echobooks.server.config import get_settings

    get_settings.cache_clear()
    serverdb._engine = None
    serverdb._Session = None

    from echobooks.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        yield client

    serverdb._engine = None
    serverdb._Session = None
    get_settings.cache_clear()
