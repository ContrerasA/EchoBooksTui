"""Sync serialize round-trip, last-write-wins merge, and the engine cycle.

These tests use the in-memory SQLite ``session`` fixture (conftest) for the
serialize/merge units, and a pair of independent in-memory DBs driven through a
fake in-memory transport for the end-to-end engine cycle — no network.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session, sessionmaker

from echobooks.db import session as dbsession
from echobooks.db.models import Book, MediaType, Status
from echobooks.db.repository import create_book
from echobooks.providers.base import BookDraft
from echobooks.sync.engine import import_local, sync
from echobooks.sync.serialize import (
    SyncPayload,
    apply_remote,
    dump_books,
    dump_dirty,
    entity_to_wire,
)


def _draft(title: str, author: str = "Andy Weir") -> BookDraft:
    return BookDraft(
        title=title,
        authors=[author],
        genres=["Science Fiction"],
        media_type=MediaType.PRINT,
        page_count=400,
    )


# --------------------------------------------------------------------------- #
# Serialize round-trip
# --------------------------------------------------------------------------- #
def test_book_round_trips_through_wire(session: Session):
    book = create_book(session, _draft("Project Hail Mary"), status=Status.READ)
    session.commit()

    wire = entity_to_wire("book", book)
    assert wire.table == "book"
    assert wire.id == book.id
    assert wire.fields["title"] == "Project Hail Mary"
    # media_type enum is serialized to its value, not the enum object.
    assert wire.fields["media_type"] == MediaType.PRINT.value
    assert wire.fields["status"] == Status.READ.value
    # Links travel inside the book wire row.
    assert wire.authors and wire.authors[0].target_id == book.authors[0].id
    assert wire.tags and wire.tags[0].target_id == book.tags[0].id


def test_dump_dirty_collects_all_entities(session: Session):
    create_book(session, _draft("Dune", "Frank Herbert"), status=Status.READ)
    session.commit()
    payload = dump_dirty(session)
    tables = {r.table for r in payload.rows}
    # A READ book makes a book, an author, a tag, and a finished reading session.
    assert {"book", "author", "tag", "reading_session"} <= tables


# --------------------------------------------------------------------------- #
# Apply / last-write-wins, into a *separate* DB
# --------------------------------------------------------------------------- #
@pytest.fixture
def factory_b() -> Iterator[sessionmaker[Session]]:
    """A second independent in-memory DB, simulating a different device."""
    import sqlalchemy as sa

    from echobooks.db.models import Base

    engine = sa.create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def test_apply_creates_book_on_empty_db(session: Session, factory_b):
    # Device A creates a book.
    book = create_book(session, _draft("Recursion", "Blake Crouch"), status=Status.WANT)
    session.commit()
    payload = dump_dirty(session)

    # Device B (empty) applies A's payload.
    with factory_b() as b:
        applied = apply_remote(b, payload)
        b.commit()
        assert applied >= 1
        got = b.get(Book, book.id)
        assert got is not None
        assert got.title == "Recursion"
        assert got.authors[0].name == "Blake Crouch"
        assert got.dirty is False  # pulled rows are clean


def test_lww_newer_wins(session: Session, factory_b):
    book = create_book(session, _draft("Old Title"), status=Status.WANT)
    session.commit()

    # Seed device B with the same id but an OLDER updated_at.
    with factory_b() as b:
        apply_remote(b, dump_dirty(session))
        b.commit()

    # Now device A edits with a NEWER timestamp.
    newer = datetime.now(UTC) + timedelta(hours=1)
    book.title = "New Title"
    book.updated_at = newer
    session.commit()

    with factory_b() as b:
        apply_remote(b, dump_dirty(session))
        b.commit()
        assert b.get(Book, book.id).title == "New Title"


def test_lww_older_loses(session: Session, factory_b):
    book = create_book(session, _draft("Canonical"), status=Status.WANT)
    book.updated_at = datetime.now(UTC)
    session.commit()

    # Device B already has a NEWER version of the same row.
    with factory_b() as b:
        apply_remote(b, dump_dirty(session))
        # Make B's copy newer than anything A will send.
        bk = b.get(Book, book.id)
        bk.title = "Newer On B"
        bk.updated_at = datetime.now(UTC) + timedelta(hours=2)
        b.commit()

    # A sends an OLDER edit; B must keep its newer title.
    with factory_b() as b:
        apply_remote(b, dump_dirty(session))
        b.commit()
        assert b.get(Book, book.id).title == "Newer On B"


def test_soft_delete_propagates(session: Session, factory_b):
    book = create_book(session, _draft("To Delete"), status=Status.WANT)
    session.commit()
    with factory_b() as b:
        apply_remote(b, dump_dirty(session))
        b.commit()

    # Device A soft-deletes (newer timestamp).
    book.deleted_at = datetime.now(UTC) + timedelta(hours=1)
    book.updated_at = book.deleted_at
    session.commit()

    with factory_b() as b:
        apply_remote(b, dump_dirty(session))
        b.commit()
        assert b.get(Book, book.id).deleted_at is not None


def test_book_arriving_before_its_author_keeps_the_link(session: Session, factory_b):
    """A book pulled before its author entity must not lose its author.

    The incremental ``since`` cursor can split a book and the author it references
    across two pulls (the book was touched more recently than the long-lived,
    shared author — e.g. a series author on many books). If the book applied first
    and dropped the link, the author arriving later would never re-attach. Regression
    test: the link survives as a placeholder and the real author fills it in.
    """
    book = create_book(session, _draft("Mistborn", "Brandon Sanderson"), status=Status.READ)
    session.commit()
    payload = dump_dirty(session)
    author_rows = SyncPayload(rows=[r for r in payload.rows if r.table == "author"])
    other_rows = SyncPayload(rows=[r for r in payload.rows if r.table != "author"])

    with factory_b() as b:
        # Round 1: the book arrives WITHOUT its author.
        apply_remote(b, other_rows)
        b.commit()
        got = b.get(Book, book.id)
        assert [link.author_id for link in got.author_links] == [book.authors[0].id]

        # Round 2: the author entity arrives and backfills the name.
        apply_remote(b, author_rows)
        b.commit()
        got = b.get(Book, book.id)
        assert got.author_names == "Brandon Sanderson"
        # The placeholder must never have been pushed back / marked dirty.
        assert b.get(type(book.authors[0]), book.authors[0].id).dirty is False


# --------------------------------------------------------------------------- #
# import_local: only chosen books go up
# --------------------------------------------------------------------------- #
def test_dump_books_selects_only_chosen(session: Session):
    keep = create_book(session, _draft("Keeper"), status=Status.READ)
    create_book(session, _draft("Skipped", "Other Author"), status=Status.READ)
    session.commit()

    payload = dump_books(session, [keep.id])
    book_titles = {r.fields["title"] for r in payload.rows if r.table == "book"}
    assert book_titles == {"Keeper"}


# --------------------------------------------------------------------------- #
# Engine cycle through a fake transport
# --------------------------------------------------------------------------- #
class FakeServer:
    """An in-memory stand-in for the sync server: stores rows, LWW on push."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], object] = {}

    async def push(self, payload: SyncPayload) -> None:
        for row in payload.rows:
            key = (row.table, row.id)
            existing = self.store.get(key)
            if existing is None or row.updated_at >= existing.updated_at:  # type: ignore[attr-defined]
                self.store[key] = row

    async def pull(self, since: str | None) -> SyncPayload:
        rows = list(self.store.values())
        if since:
            cutoff = datetime.fromisoformat(since)
            rows = [r for r in rows if r.updated_at > cutoff]  # type: ignore[attr-defined]
        return SyncPayload(rows=rows)  # type: ignore[arg-type]


async def test_engine_sync_pushes_then_clears_dirty(session: Session):
    factory = dbsession.get_sessionmaker()
    create_book(session, _draft("Synced Book"), status=Status.READ)
    session.commit()

    server = FakeServer()
    result = await sync(factory, server, since=None)

    assert result.pushed >= 1
    assert result.at  # ISO timestamp recorded
    # Server now holds the book; dirty flags cleared locally.
    assert any(t == "book" for (t, _) in server.store)
    with factory() as s:
        assert dump_dirty(s).rows == []  # nothing left dirty


async def test_import_local_only_pushes_selected(session: Session):
    factory = dbsession.get_sessionmaker()
    keep = create_book(session, _draft("Chosen"), status=Status.READ)
    create_book(session, _draft("Unchosen", "Nope"), status=Status.READ)
    session.commit()

    server = FakeServer()
    uploaded = await import_local(factory, server, [keep.id])

    assert uploaded >= 1
    server_titles = {
        r.fields["title"]  # type: ignore[attr-defined]
        for (t, _), r in server.store.items()
        if t == "book"
    }
    assert server_titles == {"Chosen"}
