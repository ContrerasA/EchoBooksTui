"""Wire format + last-write-wins merge for the sync engine.

The catalog splits into two kinds of rows:

* **Entities** carrying :class:`~echobooks.db.models.SyncMixin` columns
  (``book``, ``author``, ``narrator``, ``tag``, ``reading_session``) — each has a
  client-generated ``id`` and an ``updated_at`` / ``deleted_at`` pair, so they
  merge independently by last-write-wins.
* **Link rows** (``book_author``, ``book_narrator``, ``book_tag``) have no
  timestamps of their own. They travel *inside* their parent book's wire row and
  are replaced wholesale whenever that book wins a merge.

The wire format is deliberately generic — one :class:`EntityWire` shape keyed by
table name — so the merge logic is written once and every entity reuses it. The
server speaks the exact same DTOs (it imports this module), so there is no
duplicate schema to keep in step.

``dirty`` never crosses the wire: it is a purely local "needs pushing" marker.
``user_id`` never crosses the wire either: the client always leaves it NULL and
the server assigns it from the authenticated user.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from echobooks.db.models import (
    Author,
    Book,
    BookAuthor,
    BookNarrator,
    Narrator,
    ReadingSession,
    Tag,
)

# Table name -> mapped class, for every syncable entity (all carry SyncMixin's
# id / updated_at / deleted_at / dirty / user_id columns). Typed loosely as
# ``type[Any]`` because the generic merge code drives these classes dynamically —
# reading SQLAlchemy column attributes (``model.dirty.is_(...)``) and constructing
# rows by name — which a precise static type can't express cleanly. Order matters
# on apply: parents (book references author/narrator/tag) come after them so FK
# targets already exist.
ENTITY_MODELS: dict[str, type[Any]] = {
    "author": Author,
    "narrator": Narrator,
    "tag": Tag,
    "book": Book,
    "reading_session": ReadingSession,
}

# Per-entity content columns (everything except the SyncMixin bookkeeping columns,
# which are carried in dedicated wire fields). Computed once from the mapping.
_SYNC_COLS = {"id", "created_at", "updated_at", "deleted_at", "dirty", "user_id"}

# Naive-UTC epoch sentinel for placeholder entities created when a book's link
# target hasn't synced yet — guarantees the real entity wins LWW on arrival.
_EPOCH = datetime(1970, 1, 1)


def _content_columns(model: type[Any]) -> list[str]:
    return [c.key for c in model.__table__.columns if c.key not in _SYNC_COLS]


class LinkRow(BaseModel):
    """A book's membership in an association table (author / narrator / tag)."""

    target_id: str
    position: int = 0


class EntityWire(BaseModel):
    """One syncable row in transit."""

    table: str
    id: str
    updated_at: datetime
    deleted_at: datetime | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    # Only populated for books: their author/narrator/tag link rows.
    authors: list[LinkRow] | None = None
    narrators: list[LinkRow] | None = None
    tags: list[LinkRow] | None = None


class SyncPayload(BaseModel):
    """A batch of entity rows pushed or pulled in one request."""

    rows: list[EntityWire] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Dump (DB row -> wire)
# --------------------------------------------------------------------------- #
def _json_scalar(value: Any) -> Any:
    """Coerce a column value to something JSON/pydantic-friendly."""
    # date / datetime serialize fine via pydantic; enums need their value.
    from enum import Enum

    if isinstance(value, Enum):
        return value.value
    return value


def entity_to_wire(table: str, obj: Any) -> EntityWire:
    model = ENTITY_MODELS[table]
    fields = {col: _json_scalar(getattr(obj, col)) for col in _content_columns(model)}
    wire = EntityWire(
        table=table,
        id=obj.id,
        updated_at=obj.updated_at,
        deleted_at=obj.deleted_at,
        fields=fields,
    )
    if table == "book":
        wire.authors = [
            LinkRow(target_id=link.author_id, position=link.position)
            for link in obj.author_links
        ]
        wire.narrators = [
            LinkRow(target_id=link.narrator_id, position=link.position)
            for link in obj.narrator_links
        ]
        wire.tags = [LinkRow(target_id=tag.id) for tag in obj.tags]
    return wire


def dump_dirty(session: Session) -> SyncPayload:
    """Collect every locally-modified row (``dirty is True``) into a payload."""
    rows: list[EntityWire] = []
    for table, model in ENTITY_MODELS.items():
        for obj in session.scalars(select(model).where(model.dirty.is_(True))):
            rows.append(entity_to_wire(table, obj))
    return SyncPayload(rows=rows)


def dump_books(session: Session, book_ids: list[str]) -> SyncPayload:
    """Collect a chosen set of books and everything they reference.

    Used by the first-login import picker: only the selected titles (plus their
    authors, narrators, tags and reading sessions) go up to the account.
    """
    ids = set(book_ids)
    books = list(session.scalars(select(Book).where(Book.id.in_(ids)))) if ids else []

    author_ids: set[str] = set()
    narrator_ids: set[str] = set()
    tag_ids: set[str] = set()
    rows: list[EntityWire] = []

    for book in books:
        for a_link in book.author_links:
            author_ids.add(a_link.author_id)
        for n_link in book.narrator_links:
            narrator_ids.add(n_link.narrator_id)
        for tag in book.tags:
            tag_ids.add(tag.id)

    if author_ids:
        for a in session.scalars(select(Author).where(Author.id.in_(author_ids))):
            rows.append(entity_to_wire("author", a))
    if narrator_ids:
        for n in session.scalars(select(Narrator).where(Narrator.id.in_(narrator_ids))):
            rows.append(entity_to_wire("narrator", n))
    if tag_ids:
        for t in session.scalars(select(Tag).where(Tag.id.in_(tag_ids))):
            rows.append(entity_to_wire("tag", t))
    for book in books:
        rows.append(entity_to_wire("book", book))
    if ids:
        sessions = session.scalars(
            select(ReadingSession).where(ReadingSession.book_id.in_(ids))
        )
        for rs in sessions:
            rows.append(entity_to_wire("reading_session", rs))
    return SyncPayload(rows=rows)


# --------------------------------------------------------------------------- #
# Apply (wire -> DB) with last-write-wins
# --------------------------------------------------------------------------- #
def as_naive_utc(dt: datetime | None) -> datetime | None:
    """Normalize a datetime for storage/comparison.

    The catalog's ``DateTime`` columns are timezone-naive, so SQLite/Postgres
    hand back naive datetimes, while freshly-minted timestamps (``datetime.now(UTC)``)
    are aware. Comparing the two raises ``TypeError``. We canonicalize everything to
    *naive UTC* — matching what the columns actually store — so LWW comparisons and
    writes are consistent regardless of which side a value came from.
    """
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _ensure_entity(session: Session, model: type[Any], target_id: str, owner_id: str | None) -> Any:
    """Fetch a link target, materializing an empty placeholder if it hasn't synced yet.

    A book's link rows travel *with the book*, but the author/narrator/tag entity
    they point at is a separate syncable row that may arrive in a *different* pull —
    the incremental ``since`` cursor can deliver a book before (or after) an entity
    it references. If we dropped a link whose target wasn't present yet, the link
    would be lost forever: the book is marked clean and never re-applied, so the
    entity arriving in a later round would never re-attach. Instead we create a
    placeholder row with the known id; when the real entity arrives it overwrites
    this stub by last-write-wins on the same id, filling in name/bio/etc.
    """
    obj = session.get(model, target_id)
    if obj is None:
        # updated_at at the epoch so the real entity (any real timestamp) always
        # wins LWW when it arrives; dirty=False so this stub is never pushed and
        # can't clobber the real row on the server with an empty name.
        obj = model(
            id=target_id,
            user_id=owner_id,
            created_at=_EPOCH,
            updated_at=_EPOCH,
            dirty=False,
        )
        session.add(obj)
        session.flush()
    return obj


def _set_links(
    session: Session, book: Book, wire: EntityWire, owner_id: str | None = None
) -> None:
    """Replace a book's author/narrator/tag links from the wire row.

    Link targets are usually merged in the same payload (entities sort before
    books). When a target hasn't arrived yet we materialize a placeholder rather
    than drop the link — see :func:`_ensure_entity`.
    """
    if wire.authors is not None:
        book.author_links.clear()
        session.flush()
        for link in wire.authors:
            author = _ensure_entity(session, Author, link.target_id, owner_id)
            book.author_links.append(BookAuthor(author=author, position=link.position))
    if wire.narrators is not None:
        book.narrator_links.clear()
        session.flush()
        for link in wire.narrators:
            narrator = _ensure_entity(session, Narrator, link.target_id, owner_id)
            book.narrator_links.append(
                BookNarrator(narrator=narrator, position=link.position)
            )
    if wire.tags is not None:
        tags = [_ensure_entity(session, Tag, lk.target_id, owner_id) for lk in wire.tags]
        book.tags = tags


def _coerce_field(model: type[Any], col: str, value: Any) -> Any:
    """Turn a wire scalar back into the column's Python type (enum/date)."""
    if value is None:
        return None
    python_type = model.__table__.columns[col].type.python_type
    from datetime import date

    if isinstance(value, str):
        # Enum columns store StrEnum members; rebuild from value.
        try:
            import enum

            if isinstance(python_type, type) and issubclass(python_type, enum.Enum):
                return python_type(value)
        except (TypeError, ValueError):
            pass
        if python_type is date:
            return date.fromisoformat(value)
    return value


def merge_payload(
    session: Session,
    payload: SyncPayload,
    *,
    owner_id: str | None = None,
    scope_to_owner: bool = False,
) -> list[EntityWire]:
    """Merge wire rows into ``session`` by last-write-wins. Shared by both sides.

    A remote row wins iff its ``updated_at`` is strictly newer than the stored
    row's (or no stored row exists). Winners overwrite content + ``deleted_at``
    (so a soft delete propagates) and are marked ``dirty=False``.

    * **Client** calls this with ``owner_id=None``: rows are looked up by id alone
      and ``user_id`` stays NULL (the offline catalog has no notion of owners).
    * **Server** calls it with ``owner_id=<user>`` and ``scope_to_owner=True``:
      lookups are filtered to that owner (so one user can never overwrite
      another's row) and every winner is stamped with the owner's id.

    Returns the winning rows as wire DTOs (the server echoes these back to the
    client; the client ignores the return value).
    """
    # Apply in dependency order (entities, then book, then sessions). The payload
    # may arrive in any order, so bucket by table first.
    by_table: dict[str, list[EntityWire]] = {t: [] for t in ENTITY_MODELS}
    for wire in payload.rows:
        if wire.table in by_table:
            by_table[wire.table].append(wire)

    winners: list[EntityWire] = []
    for table, model in ENTITY_MODELS.items():
        for wire in by_table[table]:
            # wire.updated_at is required, so the normalized value is non-None.
            remote_ts = as_naive_utc(wire.updated_at)
            assert remote_ts is not None

            local = session.get(model, wire.id)
            if scope_to_owner and local is not None and local.user_id != owner_id:
                # The id exists but belongs to a *different* user. IDs are
                # client-generated UUIDs so a genuine cross-user collision is
                # vanishingly unlikely, but we refuse to read or overwrite another
                # owner's row rather than crash on the primary-key conflict. Skip.
                continue

            if local is not None:
                local_ts = as_naive_utc(local.updated_at)
                if local_ts is not None and local_ts >= remote_ts:
                    continue  # stored copy is same-or-newer: keep it (LWW)
            if local is None:
                local = model(id=wire.id)
                session.add(local)
            for col, value in wire.fields.items():
                setattr(local, col, _coerce_field(model, col, value))
            local.updated_at = remote_ts
            local.deleted_at = as_naive_utc(wire.deleted_at)
            local.dirty = False
            local.user_id = owner_id
            session.flush()
            if table == "book":
                _set_links(session, local, wire, owner_id=owner_id)
            winners.append(entity_to_wire(table, local))
    session.flush()
    return winners


def apply_remote(session: Session, payload: SyncPayload) -> int:
    """Client-side merge: pull rows into the local DB. Returns rows applied."""
    return len(merge_payload(session, payload, owner_id=None, scope_to_owner=False))


__all__ = [
    "ENTITY_MODELS",
    "EntityWire",
    "LinkRow",
    "SyncPayload",
    "apply_remote",
    "dump_books",
    "dump_dirty",
    "entity_to_wire",
    "merge_payload",
]
