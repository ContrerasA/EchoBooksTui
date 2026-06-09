"""The data API: all reads, writes, and stats queries live here so the UI stays thin.

Stats model: a *finish* is a :class:`ReadingSession` row with ``finished_on`` set.
Marking a book READ always guarantees one finished session (see :func:`set_status`
and :func:`create_book`), and re-reads add more. So every "read" metric is derived
purely from sessions — re-reads count, and the numbers stay internally consistent.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date
from typing import TYPE_CHECKING, NamedTuple

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from .models import (
    Author,
    Book,
    BookAuthor,
    BookNarrator,
    MediaType,
    Narrator,
    ReadingSession,
    Status,
    Tag,
    book_tag,
)

if TYPE_CHECKING:
    from echobooks.providers.base import BookDraft

_PAGE_MEDIA = (MediaType.PRINT.value, MediaType.EBOOK.value)


# --------------------------------------------------------------------------- #
# Lookups / upserts of related entities
# --------------------------------------------------------------------------- #
def get_or_create_author(session: Session, name: str, external_id: str | None = None) -> Author:
    name = name.strip()
    stmt = select(Author).where(func.lower(Author.name) == name.lower())
    author = session.scalars(stmt).first()
    if author is None:
        author = Author(name=name, external_id=external_id)
        session.add(author)
        session.flush()
    elif external_id and not author.external_id:
        author.external_id = external_id
    return author


def get_or_create_narrator(session: Session, name: str) -> Narrator:
    name = name.strip()
    stmt = select(Narrator).where(func.lower(Narrator.name) == name.lower())
    narrator = session.scalars(stmt).first()
    if narrator is None:
        narrator = Narrator(name=name)
        session.add(narrator)
        session.flush()
    return narrator


def get_or_create_tag(session: Session, name: str, kind: str = "genre") -> Tag:
    name = name.strip()
    stmt = select(Tag).where(func.lower(Tag.name) == name.lower(), Tag.kind == kind)
    tag = session.scalars(stmt).first()
    if tag is None:
        tag = Tag(name=name, kind=kind)
        session.add(tag)
        session.flush()
    return tag


def set_book_authors(session: Session, book: Book, names: list[str]) -> None:
    book.author_links.clear()
    session.flush()
    seen: set[str] = set()
    pos = 0
    for name in names:
        if not name.strip():
            continue
        author = get_or_create_author(session, name)
        if author.id in seen:  # providers sometimes repeat a contributor
            continue
        seen.add(author.id)
        book.author_links.append(BookAuthor(author=author, position=pos))
        pos += 1


def set_book_narrators(session: Session, book: Book, names: list[str]) -> None:
    book.narrator_links.clear()
    session.flush()
    seen: set[str] = set()
    pos = 0
    for name in names:
        if not name.strip():
            continue
        narrator = get_or_create_narrator(session, name)
        if narrator.id in seen:
            continue
        seen.add(narrator.id)
        book.narrator_links.append(BookNarrator(narrator=narrator, position=pos))
        pos += 1


def set_book_genres(session: Session, book: Book, names: list[str]) -> None:
    # Dedupe by tag id: provider genre lists can repeat (e.g. "Thriller & Suspense"
    # twice), which would violate the book_tag UNIQUE constraint on flush.
    unique: dict[str, Tag] = {}
    for n in names:
        if not n.strip():
            continue
        tag = get_or_create_tag(session, n, "genre")
        unique[tag.id] = tag
    book.tags = list(unique.values())


# --------------------------------------------------------------------------- #
# Book CRUD
# --------------------------------------------------------------------------- #
def create_book(
    session: Session,
    draft: BookDraft,
    *,
    status: Status = Status.WANT,
    started_on: date | None = None,
    finished_on: date | None = None,
    rating: float | None = None,
) -> Book:
    """Create a Book from a draft, attaching authors/narrators/genres."""
    book = Book(
        title=draft.title.strip(),
        subtitle=draft.subtitle,
        sort_title=_sort_key(draft.title),
        media_type=draft.media_type,
        status=status,
        cover_url=draft.cover_url,
        description=draft.description,
        page_count=draft.page_count,
        runtime_min=draft.runtime_min,
        language=draft.language,
        publisher=draft.publisher,
        published_date=draft.published_date,
        series_name=draft.series_name,
        series_position=draft.series_position,
        external_source=draft.external_source,
        external_id=draft.external_id,
    )
    session.add(book)
    session.flush()
    set_book_authors(session, book, draft.authors)
    set_book_narrators(session, book, draft.narrators)
    set_book_genres(session, book, draft.genres)

    if status == Status.READ or finished_on is not None:
        add_session(
            session,
            book,
            started_on=started_on,
            finished_on=finished_on or date.today(),
            rating=rating,
        )
    elif rating is not None or started_on is not None:
        add_session(session, book, started_on=started_on, rating=rating)
    session.flush()
    return book


def update_book(session: Session, book: Book, draft: BookDraft) -> Book:
    """Apply edited draft fields (and relationships) onto an existing book."""
    book.title = draft.title.strip()
    book.subtitle = draft.subtitle
    book.sort_title = _sort_key(draft.title)
    book.media_type = draft.media_type
    book.cover_url = draft.cover_url
    book.description = draft.description
    book.page_count = draft.page_count
    book.runtime_min = draft.runtime_min
    book.language = draft.language
    book.publisher = draft.publisher
    book.published_date = draft.published_date
    book.series_name = draft.series_name
    book.series_position = draft.series_position
    book.external_source = draft.external_source
    book.external_id = draft.external_id
    book.dirty = True
    set_book_authors(session, book, draft.authors)
    set_book_narrators(session, book, draft.narrators)
    set_book_genres(session, book, draft.genres)
    session.flush()
    return book


def book_to_draft(book: Book) -> BookDraft:
    """Snapshot an existing book into an editable draft (for the edit form)."""
    from echobooks.providers.base import BookDraft as _BookDraft

    return _BookDraft(
        title=book.title,
        subtitle=book.subtitle,
        authors=[a.name for a in book.authors],
        narrators=[n.name for n in book.narrators],
        genres=[t.name for t in book.tags if t.kind == "genre"],
        media_type=book.media_type,
        cover_url=book.cover_url,
        description=book.description,
        page_count=book.page_count,
        runtime_min=book.runtime_min,
        language=book.language,
        publisher=book.publisher,
        published_date=book.published_date,
        series_name=book.series_name,
        series_position=book.series_position,
        external_source=book.external_source,
        external_id=book.external_id,
    )


def get_book(session: Session, book_id: str) -> Book | None:
    stmt = (
        select(Book)
        .where(Book.id == book_id, Book.deleted_at.is_(None))
        .options(
            selectinload(Book.author_links).selectinload(BookAuthor.author),
            selectinload(Book.narrator_links).selectinload(BookNarrator.narrator),
            selectinload(Book.tags),
            selectinload(Book.sessions),
        )
    )
    return session.scalars(stmt).first()


def list_books(
    session: Session,
    *,
    status: Status | None = None,
    media_type: MediaType | None = None,
    search: str = "",
    sort: str = "title",
) -> list[Book]:
    stmt = (
        select(Book)
        .where(Book.deleted_at.is_(None))
        .options(
            selectinload(Book.author_links).selectinload(BookAuthor.author),
            selectinload(Book.sessions),
        )
    )
    if status is not None:
        stmt = stmt.where(Book.status == status)
    if media_type is not None:
        stmt = stmt.where(Book.media_type == media_type)
    if search.strip():
        like = f"%{search.strip().lower()}%"
        author_match = (
            select(BookAuthor.book_id)
            .join(Author, Author.id == BookAuthor.author_id)
            .where(func.lower(Author.name).like(like))
        )
        stmt = stmt.where(
            or_(func.lower(Book.title).like(like), Book.id.in_(author_match))
        )

    if sort == "author":
        # Author asc, then series, then book number (reading order) — done in
        # Python so we can key off the primary author across the many-to-many.
        stmt = stmt.order_by(Book.sort_title)
        books = list(session.scalars(stmt).unique().all())
        books.sort(key=_author_sort_key)
        return books

    sort_map = {
        "title": Book.sort_title,
        "added": Book.created_at.desc(),
        "updated": Book.updated_at.desc(),
        "runtime": Book.runtime_min.desc(),
        "pages": Book.page_count.desc(),
    }
    stmt = stmt.order_by(sort_map.get(sort, Book.sort_title))
    return list(session.scalars(stmt).unique().all())


def set_status(session: Session, book: Book, status: Status) -> None:
    book.status = status
    book.dirty = True
    if status == Status.READ and not any(s.finished_on for s in book.sessions):
        add_session(session, book, finished_on=date.today())


def soft_delete_book(session: Session, book: Book) -> None:
    from datetime import UTC, datetime

    book.deleted_at = datetime.now(UTC)
    book.dirty = True


# --------------------------------------------------------------------------- #
# Deduplication / reconciliation
# --------------------------------------------------------------------------- #
# Two entries are "the same book" when they share a natural key. A provider id
# (source + external id, e.g. an Audible ASIN) is authoritative; manual entries
# fall back to normalized title + primary author. Media type is always part of
# the key, so the audiobook and the print edition of one title stay distinct.
MatchKey = tuple[str, ...]


def _match_key(
    *,
    external_source: str | None,
    external_id: str | None,
    title: str,
    sort_title: str | None,
    first_author: str,
    media_type: MediaType,
) -> MatchKey:
    src = (external_source or "").strip().lower()
    ext = (external_id or "").strip().lower()
    if src and ext and src != "manual":
        return ("ext", src, ext, media_type.value)
    norm_title = (sort_title or _sort_key(title or "")).strip().lower()
    norm_author = " ".join((first_author or "").lower().split())
    return ("meta", norm_title, norm_author, media_type.value)


def book_match_key(book: Book) -> MatchKey:
    return _match_key(
        external_source=book.external_source,
        external_id=book.external_id,
        title=book.title,
        sort_title=book.sort_title,
        first_author=book.authors[0].name if book.authors else "",
        media_type=book.media_type,
    )


def draft_match_key(draft: BookDraft) -> MatchKey:
    return _match_key(
        external_source=draft.external_source,
        external_id=draft.external_id,
        title=draft.title,
        sort_title=None,
        first_author=draft.authors[0] if draft.authors else "",
        media_type=draft.media_type,
    )


def _live_books_with_authors(session: Session) -> list[Book]:
    stmt = (
        select(Book)
        .where(Book.deleted_at.is_(None))
        .options(selectinload(Book.author_links).selectinload(BookAuthor.author))
    )
    return list(session.scalars(stmt).unique().all())


def find_duplicate(session: Session, key: MatchKey, *, exclude_id: str | None = None) -> Book | None:
    """Return a live book matching ``key`` (other than ``exclude_id``), if any."""
    for book in _live_books_with_authors(session):
        if book.id == exclude_id:
            continue
        if book_match_key(book) == key:
            return book
    return None


def find_duplicate_groups(session: Session) -> list[list[Book]]:
    """Group live books by natural key; return only the groups with >1 member."""
    groups: dict[MatchKey, list[Book]] = {}
    for book in _live_books_with_authors(session):
        groups.setdefault(book_match_key(book), []).append(book)
    return [g for g in groups.values() if len(g) > 1]


def merge_books(session: Session, survivor_id: str, loser_ids: list[str]) -> None:
    """Fold ``loser_ids`` into ``survivor_id``: move reading history over, carry
    favorite/non-WANT status, then soft-delete the losers so the merge syncs.

    The caller picks a deterministic survivor (e.g. ``min(id)``) so every device
    converges on the same winner without coordinating.
    """
    survivor = session.get(Book, survivor_id)
    if survivor is None or survivor.deleted_at is not None:
        return
    for loser_id in loser_ids:
        loser = session.get(Book, loser_id)
        if loser is None or loser.deleted_at is not None or loser.id == survivor.id:
            continue
        for rs in list(loser.sessions):
            rs.book_id = survivor.id  # onupdate bumps updated_at so it wins on sync
            rs.dirty = True
        if loser.is_favorite and not survivor.is_favorite:
            survivor.is_favorite = True
            survivor.dirty = True
        if survivor.status == Status.WANT and loser.status != Status.WANT:
            survivor.status = loser.status
            survivor.dirty = True
        soft_delete_book(session, loser)
    session.flush()


# --------------------------------------------------------------------------- #
# Reading sessions (reads / re-reads)
# --------------------------------------------------------------------------- #
def add_session(
    session: Session,
    book: Book,
    *,
    started_on: date | None = None,
    finished_on: date | None = None,
    rating: float | None = None,
    review: str | None = None,
    media_type: MediaType | None = None,
) -> ReadingSession:
    rs = ReadingSession(
        book_id=book.id,
        started_on=started_on,
        finished_on=finished_on,
        rating=rating,
        review=review,
        media_type=media_type,
    )
    session.add(rs)
    session.flush()
    return rs


def delete_session(session: Session, rs: ReadingSession) -> None:
    session.delete(rs)


# --------------------------------------------------------------------------- #
# Stats
# --------------------------------------------------------------------------- #
class Totals(NamedTuple):
    books: int
    want: int
    reading: int
    read: int
    finishes: int
    minutes_listened: int
    pages_read: int

    @property
    def hours_listened(self) -> float:
        return round(self.minutes_listened / 60, 1)


def _finished_join(stmt):
    return stmt.join(ReadingSession, ReadingSession.book_id == Book.id).where(
        ReadingSession.finished_on.is_not(None)
    )


def _effective_media():
    return func.coalesce(ReadingSession.media_type, Book.media_type)


def totals(session: Session) -> Totals:
    live = Book.deleted_at.is_(None)
    books = session.scalar(select(func.count()).select_from(Book).where(live)) or 0

    def count_status(s: Status) -> int:
        return session.scalar(
            select(func.count()).select_from(Book).where(live, Book.status == s)
        ) or 0

    finishes = (
        session.scalar(
            _finished_join(select(func.count()).select_from(Book)).where(live)
        )
        or 0
    )
    minutes = (
        session.scalar(
            _finished_join(select(func.coalesce(func.sum(Book.runtime_min), 0)))
            .where(live, _effective_media() == MediaType.AUDIOBOOK.value)
        )
        or 0
    )
    pages = (
        session.scalar(
            _finished_join(select(func.coalesce(func.sum(Book.page_count), 0)))
            .where(live, _effective_media().in_(_PAGE_MEDIA))
        )
        or 0
    )
    return Totals(
        books=books,
        want=count_status(Status.WANT),
        reading=count_status(Status.READING),
        read=count_status(Status.READ),
        finishes=finishes,
        minutes_listened=int(minutes),
        pages_read=int(pages),
    )


def top_authors(session: Session, limit: int = 10) -> list[tuple[str, int]]:
    finished = (
        select(ReadingSession.book_id)
        .where(ReadingSession.finished_on.is_not(None))
        .distinct()
        .subquery()
    )
    n = func.count(func.distinct(BookAuthor.book_id))
    stmt = (
        select(Author.name, n)
        .join(BookAuthor, BookAuthor.author_id == Author.id)
        .join(finished, finished.c.book_id == BookAuthor.book_id)
        .group_by(Author.name)
        .order_by(n.desc(), Author.name)
        .limit(limit)
    )
    return [(name, count) for name, count in session.execute(stmt)]


def top_narrators(session: Session, limit: int = 10) -> list[tuple[str, int]]:
    finished = (
        select(ReadingSession.book_id)
        .where(ReadingSession.finished_on.is_not(None))
        .distinct()
        .subquery()
    )
    n = func.count(func.distinct(BookNarrator.book_id))
    stmt = (
        select(Narrator.name, n)
        .join(BookNarrator, BookNarrator.narrator_id == Narrator.id)
        .join(finished, finished.c.book_id == BookNarrator.book_id)
        .group_by(Narrator.name)
        .order_by(n.desc(), Narrator.name)
        .limit(limit)
    )
    return [(name, count) for name, count in session.execute(stmt)]


def finishes_by_year(session: Session) -> list[tuple[int, int]]:
    dates = session.scalars(
        select(ReadingSession.finished_on).where(ReadingSession.finished_on.is_not(None))
    ).all()
    counts = Counter(d.year for d in dates if d is not None)
    return sorted(counts.items())


def rating_distribution(session: Session) -> list[tuple[float, int]]:
    ratings = session.scalars(
        select(ReadingSession.rating).where(ReadingSession.rating.is_not(None))
    ).all()
    counts = Counter(float(r) for r in ratings if r is not None)
    return sorted(counts.items())


def media_breakdown(session: Session) -> list[tuple[MediaType, int]]:
    stmt = (
        select(Book.media_type, func.count())
        .where(Book.deleted_at.is_(None))
        .group_by(Book.media_type)
    )
    return [(mt, n) for mt, n in session.execute(stmt)]


def genre_breakdown(session: Session, limit: int = 10) -> list[tuple[str, int]]:
    finished = (
        select(ReadingSession.book_id)
        .where(ReadingSession.finished_on.is_not(None))
        .distinct()
        .subquery()
    )
    n = func.count(func.distinct(book_tag.c.book_id))
    stmt = (
        select(Tag.name, n)
        .join(book_tag, book_tag.c.tag_id == Tag.id)
        .join(finished, finished.c.book_id == book_tag.c.book_id)
        .where(Tag.kind == "genre")
        .group_by(Tag.name)
        .order_by(n.desc(), Tag.name)
        .limit(limit)
    )
    return [(name, count) for name, count in session.execute(stmt)]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
_ARTICLES = ("the ", "a ", "an ")


def _author_sort_key(book: Book) -> tuple[str, str, float, str, str]:
    """Author, then series, then book number, then release date, then title.

    Within a series, books order by their position (book #1, #2, #3…) — the
    reading order. Release date and title only break ties (or order books that
    carry no position, which sort last within the series). ``published_date`` is
    ISO-ish ("2021-05-04") or a year ("1965"), both of which order
    chronologically as plain strings. Books with no author sort last overall.
    """
    if book.authors:
        author = (book.authors[0].sort_name or book.authors[0].name or "").lower()
    else:
        author = "￿"
    series = (book.series_name or "").lower()
    position = _series_position_key(book.series_position)
    released = book.published_date or "9999"
    title = (book.sort_title or book.title or "").lower()
    return (author, series, position, released, title)


def _series_position_key(pos: str | None) -> float:
    """Parse a series position ("1", "2", "1.5", "Book 3") to a sortable number.

    Missing or non-numeric positions sort last within their series.
    """
    if not pos:
        return float("inf")
    match = re.search(r"\d+(?:\.\d+)?", pos)
    return float(match.group()) if match else float("inf")


def _sort_key(title: str) -> str:
    t = title.strip().lower()
    for art in _ARTICLES:
        if t.startswith(art):
            return t[len(art) :]
    return t
