"""Engine / Session factory and one-time schema creation."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from echobooks.config import database_url

from .models import Base
from .schema import add_missing_columns

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


def _migrate_book_rating_review_once(engine: Engine) -> None:
    """Lift rating/review from reading sessions up to the book, exactly once.

    These fields used to live on each ``ReadingSession``; they now belong to the
    ``Book``. After :func:`echobooks.db.schema.add_missing_columns` adds the new
    columns, this backfills each book from its sessions: ``rating`` = the best
    session rating, ``review`` = the review of that best-rated session. Touched
    books are marked dirty so the values sync up. Guarded by a marker row in
    ``provider_cache`` (local-only, un-synced) so it runs once.

    Reads the old session columns through the raw table, since the ORM model no
    longer declares them.
    """
    from .models import Book, ProviderCache

    marker = "_migrate:rating_review_to_book:v1"
    factory = sessionmaker(bind=engine, future=True)
    with factory() as session:
        if session.get(ProviderCache, marker) is not None:
            return
        inspector = inspect(engine)
        rs_cols = {c["name"] for c in inspector.get_columns("reading_session")}
        if not {"rating", "review"} <= rs_cols:
            # Fresh DB created after the split — nothing to lift.
            session.add(ProviderCache(key=marker, value="done"))
            session.commit()
            return
        try:
            # Best-rated session per book (and its review). NULLs sort last so a
            # rated session wins over an unrated one; ties break on recency.
            rows = session.execute(
                text(
                    "SELECT book_id, rating, review FROM reading_session "
                    "WHERE deleted_at IS NULL AND rating IS NOT NULL "
                    "ORDER BY rating DESC, finished_on DESC, created_at DESC"
                )
            ).all()
            best: dict[str, tuple[float, str | None]] = {}
            for book_id, rating, review in rows:
                best.setdefault(book_id, (rating, review))
            for book_id, (rating, review) in best.items():
                book = session.get(Book, book_id)
                if book is None or book.deleted_at is not None:
                    continue
                book.rating = rating
                book.review = review
                book.dirty = True
            session.add(ProviderCache(key=marker, value="done"))
            session.commit()
        except Exception:
            # Never block startup; leave the marker unset to retry next launch.
            session.rollback()


def _repair_missing_authors_once(engine: Engine) -> None:
    """Run the authorless-book backfill exactly once per database.

    A now-fixed sync bug left some books without authors (see
    ``repository.repair_missing_authors``). We backfill them on the next launch,
    guarded by a marker row in ``provider_cache`` (a local-only, un-synced table)
    so the sweep runs once and never again — even though it is itself idempotent.
    """
    from .models import ProviderCache
    from .repository import repair_missing_authors

    marker = "_repair:missing_authors:v1"
    factory = sessionmaker(bind=engine, future=True)
    with factory() as session:
        if session.get(ProviderCache, marker) is not None:
            return
        try:
            repair_missing_authors(session)
            session.add(ProviderCache(key=marker, value="done"))
            session.commit()
        except Exception:
            # A repair hiccup must never block startup; leave the marker unset so
            # the sweep retries next launch. The repair itself is idempotent.
            session.rollback()


def init_db(url: str | None = None, echo: bool = False) -> Engine:
    """Create the engine (if needed) and ensure all tables exist."""
    engine = get_engine(url, echo=echo)
    Base.metadata.create_all(engine)
    _migrate_user_id(engine)
    add_missing_columns(engine, "book", {"rating": "FLOAT", "review": "TEXT"})
    _migrate_book_rating_review_once(engine)
    _repair_missing_authors_once(engine)
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
