"""Shared fixtures: an isolated in-memory database per test."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from echobooks.db import session as dbsession


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
