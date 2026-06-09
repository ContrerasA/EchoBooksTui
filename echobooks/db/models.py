"""SQLAlchemy 2.0 models for the EchoBooks catalog.

Design notes:
- String UUID primary keys are client-generated so rows created on different
  devices never collide — this is what lets the (later) sync engine merge data
  without a schema change.
- Every catalogued entity carries ``created_at`` / ``updated_at`` / ``deleted_at``
  (soft delete) and a ``dirty`` flag via :class:`SyncMixin`. These are unused in
  the offline MVP but already wired so sync bolts on cleanly.
- Re-reads / re-listens live in their own :class:`ReadingSession` rows, so a book
  can be "read" many times and each pass keeps its own dates, rating, and review.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
)
from sqlalchemy.ext.associationproxy import AssociationProxy, association_proxy
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class MediaType(enum.StrEnum):
    PRINT = "PRINT"
    EBOOK = "EBOOK"
    AUDIOBOOK = "AUDIOBOOK"

    @property
    def label(self) -> str:
        return {"PRINT": "Print", "EBOOK": "Ebook", "AUDIOBOOK": "Audiobook"}[self.value]


class Status(enum.StrEnum):
    WANT = "WANT"
    READING = "READING"
    READ = "READ"
    DNF = "DNF"
    PAUSED = "PAUSED"

    @property
    def label(self) -> str:
        return {
            "WANT": "Want to read",
            "READING": "Reading",
            "READ": "Read",
            "DNF": "Did not finish",
            "PAUSED": "Paused",
        }[self.value]


class Base(DeclarativeBase):
    pass


class SyncMixin:
    """Common columns for every syncable entity."""

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    dirty: Mapped[bool] = mapped_column(Boolean, default=True)
    # Owner of the row on the sync *server*. Always NULL (and ignored) in the
    # offline client — the client never reads or writes it. The server populates
    # it and scopes every query by it; there is deliberately no DB-level FK here
    # so the column stays null-safe on the client's SQLite (which has no `user`
    # table). See echobooks/server/models.py for the server-side relationship.
    user_id: Mapped[str | None] = mapped_column(String(32), default=None, index=True)


book_tag = Table(
    "book_tag",
    Base.metadata,
    Column("book_id", ForeignKey("book.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tag.id", ondelete="CASCADE"), primary_key=True),
)


class Book(SyncMixin, Base):
    __tablename__ = "book"

    title: Mapped[str] = mapped_column(String, default="")
    subtitle: Mapped[str | None] = mapped_column(String, default=None)
    sort_title: Mapped[str | None] = mapped_column(String, default=None)

    media_type: Mapped[MediaType] = mapped_column(Enum(MediaType), default=MediaType.AUDIOBOOK)
    status: Mapped[Status] = mapped_column(Enum(Status), default=Status.WANT)

    cover_url: Mapped[str | None] = mapped_column(String, default=None)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    page_count: Mapped[int | None] = mapped_column(Integer, default=None)
    runtime_min: Mapped[int | None] = mapped_column(Integer, default=None)
    language: Mapped[str | None] = mapped_column(String, default=None)
    publisher: Mapped[str | None] = mapped_column(String, default=None)
    published_date: Mapped[str | None] = mapped_column(String, default=None)
    series_name: Mapped[str | None] = mapped_column(String, default=None)
    series_position: Mapped[str | None] = mapped_column(String, default=None)

    external_source: Mapped[str | None] = mapped_column(String, default=None)
    external_id: Mapped[str | None] = mapped_column(String, default=None)
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)

    author_links: Mapped[list[BookAuthor]] = relationship(
        back_populates="book",
        cascade="all, delete-orphan",
        order_by="BookAuthor.position",
    )
    narrator_links: Mapped[list[BookNarrator]] = relationship(
        back_populates="book",
        cascade="all, delete-orphan",
        order_by="BookNarrator.position",
    )
    tags: Mapped[list[Tag]] = relationship(secondary=book_tag, back_populates="books")
    sessions: Mapped[list[ReadingSession]] = relationship(
        back_populates="book",
        cascade="all, delete-orphan",
        order_by="ReadingSession.started_on",
    )

    authors: AssociationProxy[list[Author]] = association_proxy("author_links", "author")
    narrators: AssociationProxy[list[Narrator]] = association_proxy("narrator_links", "narrator")

    @property
    def author_names(self) -> str:
        return ", ".join(a.name for a in self.authors) or "—"

    @property
    def narrator_names(self) -> str:
        return ", ".join(n.name for n in self.narrators) or "—"

    @property
    def best_rating(self) -> float | None:
        rated = [s.rating for s in self.sessions if s.rating is not None]
        return max(rated) if rated else None


class Author(SyncMixin, Base):
    __tablename__ = "author"

    name: Mapped[str] = mapped_column(String, default="")
    sort_name: Mapped[str | None] = mapped_column(String, default=None)
    bio: Mapped[str | None] = mapped_column(Text, default=None)
    image_url: Mapped[str | None] = mapped_column(String, default=None)
    external_id: Mapped[str | None] = mapped_column(String, default=None)

    book_links: Mapped[list[BookAuthor]] = relationship(back_populates="author")


class Narrator(SyncMixin, Base):
    __tablename__ = "narrator"

    name: Mapped[str] = mapped_column(String, default="")
    image_url: Mapped[str | None] = mapped_column(String, default=None)
    external_id: Mapped[str | None] = mapped_column(String, default=None)

    book_links: Mapped[list[BookNarrator]] = relationship(back_populates="narrator")


class BookAuthor(Base):
    __tablename__ = "book_author"

    book_id: Mapped[str] = mapped_column(
        ForeignKey("book.id", ondelete="CASCADE"), primary_key=True
    )
    author_id: Mapped[str] = mapped_column(
        ForeignKey("author.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0)

    book: Mapped[Book] = relationship(back_populates="author_links")
    author: Mapped[Author] = relationship(back_populates="book_links")

    def __init__(self, author: Author | None = None, position: int = 0, **kw: object) -> None:
        super().__init__(**kw)
        if author is not None:
            self.author = author
        self.position = position


class BookNarrator(Base):
    __tablename__ = "book_narrator"

    book_id: Mapped[str] = mapped_column(
        ForeignKey("book.id", ondelete="CASCADE"), primary_key=True
    )
    narrator_id: Mapped[str] = mapped_column(
        ForeignKey("narrator.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0)

    book: Mapped[Book] = relationship(back_populates="narrator_links")
    narrator: Mapped[Narrator] = relationship(back_populates="book_links")

    def __init__(self, narrator: Narrator | None = None, position: int = 0, **kw: object) -> None:
        super().__init__(**kw)
        if narrator is not None:
            self.narrator = narrator
        self.position = position


class Tag(SyncMixin, Base):
    __tablename__ = "tag"

    name: Mapped[str] = mapped_column(String, default="")
    kind: Mapped[str] = mapped_column(String, default="genre")  # genre | shelf | custom

    books: Mapped[list[Book]] = relationship(secondary=book_tag, back_populates="tags")


class ReadingSession(SyncMixin, Base):
    """One pass through a book — a read or a re-read / re-listen."""

    __tablename__ = "reading_session"

    book_id: Mapped[str] = mapped_column(ForeignKey("book.id", ondelete="CASCADE"))
    started_on: Mapped[date | None] = mapped_column(Date, default=None)
    finished_on: Mapped[date | None] = mapped_column(Date, default=None)
    rating: Mapped[float | None] = mapped_column(Float, default=None)  # 0.5 .. 5.0
    review: Mapped[str | None] = mapped_column(Text, default=None)
    # Optional per-session override (e.g. read in print, re-listened on audio).
    media_type: Mapped[MediaType | None] = mapped_column(Enum(MediaType), default=None)
    progress: Mapped[int | None] = mapped_column(Integer, default=None)  # percent 0..100

    book: Mapped[Book] = relationship(back_populates="sessions")


class ProviderCache(Base):
    """Cache of provider responses so re-adds / offline don't re-hit the network."""

    __tablename__ = "provider_cache"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")  # JSON blob
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
