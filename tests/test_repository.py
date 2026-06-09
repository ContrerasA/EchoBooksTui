"""Repository CRUD + stats over an in-memory DB."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from echobooks.db import repository as repo
from echobooks.db.models import MediaType, Status
from echobooks.providers.base import BookDraft


def _audiobook(title, author, minutes, genre="Science Fiction") -> BookDraft:
    return BookDraft(
        title=title,
        authors=[author],
        narrators=["Ray Porter"],
        genres=[genre],
        media_type=MediaType.AUDIOBOOK,
        runtime_min=minutes,
    )


def _print(title, author, pages, genre="Science Fiction") -> BookDraft:
    return BookDraft(
        title=title, authors=[author], genres=[genre], media_type=MediaType.PRINT, page_count=pages
    )


def _seed(session: Session) -> None:
    repo.create_book(
        session, _audiobook("Project Hail Mary", "Andy Weir", 970),
        status=Status.READ, finished_on=date(2026, 1, 15), rating=5.0,
    )
    repo.create_book(
        session, _print("The Martian", "Andy Weir", 384),
        status=Status.READ, finished_on=date(2025, 12, 1), rating=4.5,
    )
    repo.create_book(session, _print("Dune", "Frank Herbert", 412), status=Status.WANT)
    session.flush()


def test_create_and_list(session: Session):
    _seed(session)
    titles = {b.title for b in repo.list_books(session)}
    assert titles == {"Project Hail Mary", "The Martian", "Dune"}
    # Sort order puts "Dune" before "The Martian" (articles stripped).
    sorted_titles = [b.title for b in repo.list_books(session, sort="title")]
    assert sorted_titles.index("Dune") < sorted_titles.index("The Martian")


def _series_book(title, author, series, pos, released) -> BookDraft:
    return BookDraft(
        title=title, authors=[author], media_type=MediaType.AUDIOBOOK, runtime_min=600,
        series_name=series, series_position=pos, published_date=released,
    )


def test_author_sort_order(session: Session):
    # Within a series, ordering is by book number (reading order), NOT release
    # date or title: book #1 "Zebra" must come before book #2 "Apple" even
    # though "Apple" was published first (a prequel released later, an omnibus
    # re-release, etc. must not scramble the reading order).
    repo.create_book(session, _series_book("Apple", "Brandon Sanderson", "Saga", "2", "2001"),
                     status=Status.WANT)
    repo.create_book(session, _series_book("Zebra", "Brandon Sanderson", "Saga", "1", "2005"),
                     status=Status.WANT)
    repo.create_book(session, _print("Dune", "Frank Herbert", 412), status=Status.WANT)
    repo.create_book(session, _audiobook("Project Hail Mary", "Andy Weir", 970), status=Status.WANT)
    session.flush()

    ordered = [b.title for b in repo.list_books(session, sort="author")]
    # Andy Weir < Brandon Sanderson (Saga, by book number: #1 Zebra then #2 Apple) < Frank Herbert
    assert ordered == ["Project Hail Mary", "Zebra", "Apple", "Dune"]


def test_search_by_author(session: Session):
    _seed(session)
    found = [b.title for b in repo.list_books(session, search="weir")]
    assert set(found) == {"Project Hail Mary", "The Martian"}


def test_status_filter(session: Session):
    _seed(session)
    want = repo.list_books(session, status=Status.WANT)
    assert [b.title for b in want] == ["Dune"]


def test_totals_and_stats(session: Session):
    _seed(session)
    t = repo.totals(session)
    assert t.books == 3
    assert t.read == 2
    assert t.want == 1
    assert t.finishes == 2
    assert t.minutes_listened == 970
    assert t.hours_listened == 16.2
    assert t.pages_read == 384

    assert repo.top_authors(session) == [("Andy Weir", 2)]
    assert repo.finishes_by_year(session) == [(2025, 1), (2026, 1)]
    assert dict(repo.rating_distribution(session)) == {4.5: 1, 5.0: 1}
    assert repo.genre_breakdown(session) == [("Science Fiction", 2)]


def test_rereads_count_as_finishes(session: Session):
    book = repo.create_book(
        session, _audiobook("PHM", "Andy Weir", 970),
        status=Status.READ, finished_on=date(2025, 1, 1),
    )
    repo.add_session(session, book, finished_on=date(2026, 1, 1), rating=5.0)
    session.flush()
    t = repo.totals(session)
    assert t.finishes == 2  # original + re-listen
    assert t.minutes_listened == 970 * 2  # hours counted twice
    assert repo.finishes_by_year(session) == [(2025, 1), (2026, 1)]


def test_marking_read_creates_finish(session: Session):
    book = repo.create_book(session, _print("Dune", "Frank Herbert", 412), status=Status.WANT)
    session.flush()
    assert repo.totals(session).finishes == 0
    repo.set_status(session, book, Status.READ)
    session.flush()
    assert repo.totals(session).finishes == 1


def test_duplicate_contributors_and_genres_are_deduped(session: Session):
    # Providers (e.g. Audnexus for Dungeon Crawler Carl) sometimes repeat a
    # genre or contributor; this must not blow up the book_tag / join inserts.
    draft = BookDraft(
        title="Dungeon Crawler Carl",
        authors=["Matt Dinniman", "Matt Dinniman"],
        narrators=["Jeff Hays", "Jeff Hays"],
        genres=["Thriller & Suspense", "Fantasy", "Thriller & Suspense"],
        media_type=MediaType.AUDIOBOOK,
        runtime_min=811,
    )
    book = repo.create_book(session, draft, status=Status.WANT)
    session.flush()
    fresh = repo.get_book(session, book.id)
    assert [a.name for a in fresh.authors] == ["Matt Dinniman"]
    assert [n.name for n in fresh.narrators] == ["Jeff Hays"]
    assert sorted(t.name for t in fresh.tags) == ["Fantasy", "Thriller & Suspense"]


def test_update_and_soft_delete(session: Session):
    book = repo.create_book(session, _print("Dune", "Frank Herbert", 412))
    session.flush()
    draft = repo.book_to_draft(book)
    draft.title = "Dune (Deluxe)"
    draft.authors = ["Frank Herbert", "Brian Herbert"]
    repo.update_book(session, book, draft)
    session.flush()
    refreshed = repo.get_book(session, book.id)
    assert refreshed.title == "Dune (Deluxe)"
    assert [a.name for a in refreshed.authors] == ["Frank Herbert", "Brian Herbert"]

    repo.soft_delete_book(session, refreshed)
    session.flush()
    assert repo.get_book(session, book.id) is None
    assert repo.list_books(session) == []
