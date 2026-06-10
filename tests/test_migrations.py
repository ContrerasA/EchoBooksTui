"""Schema-migration / backfill behaviour."""

from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from echobooks.db import session as dbsession
from echobooks.db.models import Base, Book, ProviderCache, ReadingSession
from echobooks.db.schema import add_missing_columns


def _legacy_engine(tmp_path):
    """An engine whose reading_session still carries the old rating/review."""
    engine = create_engine(f"sqlite:///{tmp_path}/old.db", future=True)
    Base.metadata.create_all(engine)  # book already has the new columns (NULL)
    add_missing_columns(
        engine, "reading_session", {"rating": "FLOAT", "review": "TEXT"}
    )
    return engine


def test_backfill_lifts_session_rating_to_book(tmp_path):
    engine = _legacy_engine(tmp_path)
    Session = sessionmaker(bind=engine, future=True)
    with Session() as s:
        book = Book(title="Dune")
        s.add(book)
        s.flush()
        rs = ReadingSession(book_id=book.id)
        s.add(rs)
        s.flush()
        book_id, rs_id = book.id, rs.id
        # Stamp the legacy verdict directly on the session row.
        s.execute(
            text("UPDATE reading_session SET rating = 4.5, review = :r WHERE id = :i"),
            {"r": "a classic", "i": rs_id},
        )
        s.commit()

    dbsession._migrate_book_rating_review_once(engine)

    with Session() as s:
        book = s.get(Book, book_id)
        assert book.rating == 4.5
        assert book.review == "a classic"
        assert book.dirty is True  # so it pushes up on the next sync
        # Marker recorded → it won't run again.
        assert s.get(ProviderCache, "_migrate:rating_review_to_book:v1") is not None


def test_backfill_picks_best_rated_session(tmp_path):
    engine = _legacy_engine(tmp_path)
    Session = sessionmaker(bind=engine, future=True)
    with Session() as s:
        book = Book(title="Reread")
        s.add(book)
        s.flush()
        low = ReadingSession(book_id=book.id)
        high = ReadingSession(book_id=book.id)
        s.add_all([low, high])
        s.flush()
        book_id = book.id
        s.execute(
            text("UPDATE reading_session SET rating = 3.0, review = 'meh' WHERE id = :i"),
            {"i": low.id},
        )
        s.execute(
            text("UPDATE reading_session SET rating = 5.0, review = 'loved it' WHERE id = :i"),
            {"i": high.id},
        )
        s.commit()

    dbsession._migrate_book_rating_review_once(engine)

    with Session() as s:
        book = s.get(Book, book_id)
        assert book.rating == 5.0
        assert book.review == "loved it"


def test_backfill_noop_on_fresh_schema(tmp_path):
    # A catalog created after the move has no legacy columns; the backfill should
    # quietly mark itself done without touching anything.
    engine = create_engine(f"sqlite:///{tmp_path}/fresh.db", future=True)
    Base.metadata.create_all(engine)
    dbsession._migrate_book_rating_review_once(engine)
    Session = sessionmaker(bind=engine, future=True)
    with Session() as s:
        assert s.get(ProviderCache, "_migrate:rating_review_to_book:v1") is not None
