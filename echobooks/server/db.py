"""Server database: Postgres engine + Session factory + schema creation.

Uses the synchronous psycopg driver and plain SQLAlchemy ORM sessions. FastAPI
runs ``def`` route handlers in a threadpool, so sync DB calls don't block the
event loop — this keeps the data layer identical to the client's and avoids a
second async-ORM stack.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from echobooks.db.models import Base

# Importing models registers User + the catalog tables on Base.metadata.
from echobooks.server import models  # noqa: F401
from echobooks.server.config import get_settings

_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _Session
    if _engine is None:
        url = get_settings().database_url
        _engine = create_engine(url, future=True, pool_pre_ping=True)
        _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def init_db() -> Engine:
    engine = get_engine()
    Base.metadata.create_all(engine)
    return engine


def get_sessionmaker() -> sessionmaker[Session]:
    if _Session is None:
        get_engine()
    assert _Session is not None
    return _Session


@contextmanager
def session_scope() -> Iterator[Session]:
    factory = get_sessionmaker()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency: yields a session, commits on success."""
    with session_scope() as session:
        yield session
