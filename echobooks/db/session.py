"""Engine / Session factory and one-time schema creation."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from echobooks.config import database_url

from .models import Base

_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None


def _enable_sqlite_fks(engine: Engine) -> None:
    """SQLite ignores foreign keys unless asked; turn them on per connection."""

    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


def get_engine(url: str | None = None, echo: bool = False) -> Engine:
    global _engine, _Session
    if _engine is None:
        _engine = create_engine(url or database_url(), echo=echo, future=True)
        _enable_sqlite_fks(_engine)
        _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def init_db(url: str | None = None, echo: bool = False) -> Engine:
    """Create the engine (if needed) and ensure all tables exist."""
    engine = get_engine(url, echo=echo)
    Base.metadata.create_all(engine)
    return engine


def get_sessionmaker() -> sessionmaker[Session]:
    if _Session is None:
        init_db()
    assert _Session is not None
    return _Session


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on error."""
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
