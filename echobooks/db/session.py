"""Engine / Session factory and one-time schema creation."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event, inspect, text
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


def _migrate_user_id(engine: Engine) -> None:
    """Add the ``user_id`` column to existing DBs that predate it.

    ``create_all`` only creates missing *tables*, never missing *columns*, so a
    catalog created before the sync phase needs this one column backfilled. The
    column is nullable with no FK, so a plain ``ADD COLUMN`` is safe on both
    SQLite and Postgres.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    # Every table using SyncMixin gets the column.
    syncable = [
        t for t in Base.metadata.sorted_tables
        if "user_id" in t.c and t.name in existing_tables
    ]
    with engine.begin() as conn:
        for table in syncable:
            cols = {c["name"] for c in inspector.get_columns(table.name)}
            if "user_id" not in cols:
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN user_id VARCHAR(32)'))


def init_db(url: str | None = None, echo: bool = False) -> Engine:
    """Create the engine (if needed) and ensure all tables exist."""
    engine = get_engine(url, echo=echo)
    Base.metadata.create_all(engine)
    _migrate_user_id(engine)
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
