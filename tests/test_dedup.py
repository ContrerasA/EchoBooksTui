"""Deduplication: natural match keys, duplicate detection, and merging."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from echobooks.db import repository as repo
from echobooks.db.models import MediaType, Status
from echobooks.providers.base import BookDraft


def _draft(title, author, media=MediaType.AUDIOBOOK, *, source=None, ext=None) -> BookDraft:
    return BookDraft(
        title=title, authors=[author], media_type=media,
        external_source=source, external_id=ext,
    )


# -- match keys ------------------------------------------------------------- #
def test_provider_id_keys_match_regardless_of_title():
    # Same source + external id = same book, even if a title was edited.
    a = repo.draft_match_key(_draft("The Martian", "Andy Weir", source="audible", ext="B00B5HZGUG"))
    b = repo.draft_match_key(_draft("The Martian (Unabridged)", "A. Weir",
                                    source="Audible", ext="b00b5hzgug"))
    assert a == b  # source/id are case-insensitive


def test_media_type_splits_the_key():
    audio = repo.draft_match_key(_draft("Dune", "Frank Herbert", MediaType.AUDIOBOOK))
    print_ = repo.draft_match_key(_draft("Dune", "Frank Herbert", MediaType.PRINT))
    assert audio != print_


def test_manual_entries_fall_back_to_title_author():
    a = repo.draft_match_key(_draft("The Hobbit", "J.R.R. Tolkien", MediaType.PRINT))
    b = repo.draft_match_key(_draft("the   hobbit", "j.r.r. tolkien", MediaType.PRINT))
    assert a == b
    assert a[0] == "meta"


def test_manual_source_is_not_a_provider_key():
    # external_source="manual" must not be treated as a real provider id.
    k = repo.draft_match_key(_draft("Notes", "Me", MediaType.EBOOK, source="manual", ext=None))
    assert k[0] == "meta"


# -- detection -------------------------------------------------------------- #
def test_find_duplicate_matches_existing(session: Session):
    repo.create_book(session, _draft("Dune", "Frank Herbert", source="audible", ext="X1"),
                     status=Status.WANT)
    session.flush()
    dup = repo.find_duplicate(session, repo.draft_match_key(
        _draft("Dune", "Frank Herbert", source="audible", ext="X1")))
    assert dup is not None and dup.title == "Dune"
    # A different ASIN is a different book.
    assert repo.find_duplicate(session, repo.draft_match_key(
        _draft("Dune", "Frank Herbert", source="audible", ext="X2"))) is None


def test_find_duplicate_groups(session: Session):
    # Two devices added the same manual book (different ids), plus an unrelated one.
    repo.create_book(session, _draft("1984", "George Orwell", MediaType.PRINT), status=Status.WANT)
    repo.create_book(session, _draft("1984", "George Orwell", MediaType.PRINT), status=Status.READ)
    repo.create_book(session, _draft("Brave New World", "Aldous Huxley", MediaType.PRINT),
                     status=Status.WANT)
    session.flush()
    groups = repo.find_duplicate_groups(session)
    assert len(groups) == 1
    assert {b.title for b in groups[0]} == {"1984"}
    assert len(groups[0]) == 2


# -- merging ---------------------------------------------------------------- #
def test_merge_moves_history_and_tombstones_loser(session: Session):
    survivor = repo.create_book(session, _draft("1984", "George Orwell", MediaType.PRINT),
                                status=Status.WANT)
    loser = repo.create_book(session, _draft("1984", "George Orwell", MediaType.PRINT),
                             status=Status.READ)
    repo.add_session(session, loser, finished_on=date(2025, 6, 1))
    loser.rating = 4.5
    loser.is_favorite = True
    session.flush()

    # Deterministic survivor = min(id); compute the same way the UI does.
    ids = sorted([survivor.id, loser.id])
    repo.merge_books(session, ids[0], ids[1:])
    session.flush()

    keep = repo.get_book(session, ids[0])
    gone = repo.get_book(session, ids[1])
    assert gone is None  # soft-deleted, hidden from get_book
    # Reading history + favorite carried onto the survivor (the loser had an
    # auto-created READ session plus the explicit one we added).
    assert len(keep.sessions) == 2
    # The book-level rating carries onto the survivor (which had none).
    assert keep.rating == 4.5
    assert keep.is_favorite is True
    # Survivor was WANT, adopts the loser's more meaningful READ status.
    assert keep.status == Status.READ
    # Only one live "1984" remains, so it's no longer flagged as a duplicate.
    assert repo.find_duplicate_groups(session) == []


def test_merge_carries_authors_when_survivor_has_none(session: Session):
    """A merge must never leave an authorless survivor when a loser had authors.

    The deterministic survivor (min id) is an arbitrary winner; if it's the copy
    that lost its author links to a sync race, fold the loser's contributors in
    rather than soft-deleting the only authored copy.
    """
    a = repo.create_book(session, _draft("Dune", "Frank Herbert", MediaType.PRINT),
                         status=Status.WANT)
    b = repo.create_book(session, _draft("Dune", "Frank Herbert", MediaType.PRINT),
                         status=Status.WANT)
    ids = sorted([a.id, b.id])
    # Strip the *survivor*'s authors to simulate the link-drop the merge repairs;
    # the loser keeps "Frank Herbert".
    survivor = repo.get_book(session, ids[0])
    survivor.author_links.clear()
    session.flush()
    assert survivor.author_names == "—"

    repo.merge_books(session, ids[0], ids[1:])
    session.flush()

    keep = repo.get_book(session, ids[0])
    assert keep.author_names == "Frank Herbert"
    assert keep.dirty is True  # repaired survivor re-syncs


# -- one-shot repair of books left authorless by the sync bug --------------- #
def test_repair_borrows_authors_from_a_soft_deleted_duplicate(session: Session):
    keep = repo.create_book(session, _draft("Mistborn", "Brandon Sanderson"), status=Status.READ)
    dup = repo.create_book(session, _draft("Mistborn", "Brandon Sanderson"), status=Status.READ)
    keep.author_links.clear()       # survivor lost its authors to the sync bug
    repo.soft_delete_book(session, dup)  # the authored copy was already merged away
    session.flush()
    assert keep.author_names == "—"

    assert repo.repair_missing_authors(session) == 1
    session.flush()
    assert repo.get_book(session, keep.id).author_names == "Brandon Sanderson"


def test_repair_recovers_authors_from_provider_cache(session: Session):
    import json

    from echobooks.db.models import ProviderCache

    book = repo.create_book(
        session,
        _draft("Sorcerer's Stone", "J.K. Rowling", source="audible", ext="ASIN1"),
        status=Status.READ,
    )
    book.author_links.clear()
    session.add(ProviderCache(
        key="detail:audible:ASIN1",
        value=json.dumps({"authors": ["J.K. Rowling"], "narrators": ["Jim Dale"]}),
    ))
    session.flush()

    assert repo.repair_missing_authors(session) == 1
    session.flush()
    fixed = repo.get_book(session, book.id)
    assert fixed.author_names == "J.K. Rowling"
    assert fixed.narrator_names == "Jim Dale"
    assert fixed.dirty is True  # repair syncs


def test_repair_is_idempotent_and_skips_unrecoverable(session: Session):
    # No donor, no cache → stays authorless, and a second run is a no-op.
    book = repo.create_book(session, _draft("Mystery", "Unknown"), status=Status.WANT)
    book.author_links.clear()
    session.flush()

    assert repo.repair_missing_authors(session) == 0
    assert repo.get_book(session, book.id).author_names == "—"
    assert repo.repair_missing_authors(session) == 0
