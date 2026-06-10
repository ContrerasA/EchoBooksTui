"""Editable book form, shared by 'add' (create) and 'edit' (update) flows."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Input, Select, Static, TextArea

from echobooks.db.models import MediaType, Status
from echobooks.db.repository import (
    create_book,
    draft_match_key,
    find_duplicate,
    get_book,
    set_status,
    update_book,
)
from echobooks.db.session import session_scope
from echobooks.providers.base import BookDraft
from echobooks.screens.fields import LabelledFields
from echobooks.screens.fields import field as _field
from echobooks.util import format_runtime, parse_date, parse_rating, parse_runtime, split_csv

_MEDIA_OPTIONS = [(m.label, m.value) for m in MediaType]
_STATUS_OPTIONS = [(s.label, s.value) for s in Status]


class BookFormScreen(LabelledFields, Screen[bool]):
    BINDINGS = [
        Binding("ctrl+s", "save", "Save"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        draft: BookDraft,
        *,
        book_id: str | None = None,
        status: Status = Status.WANT,
        rating: float | None = None,
        review: str | None = None,
    ) -> None:
        super().__init__()
        self.draft = draft
        self.book_id = book_id
        self.initial_status = status
        self.rating = rating
        self.review = review

    @property
    def is_edit(self) -> bool:
        return self.book_id is not None

    def compose(self) -> ComposeResult:
        d = self.draft
        heading = "Edit book" if self.is_edit else "Add book"
        runtime = format_runtime(d.runtime_min) if d.runtime_min else ""
        pages = str(d.page_count) if d.page_count else ""
        with VerticalScroll(classes="panel"):
            # Actions first so you can accept immediately without scrolling/tabbing.
            with Horizontal(classes="actions"):
                yield Button("Save", variant="success", id="save")
                yield Button("Cancel", variant="default", id="cancel")
                yield Static(
                    f"[b]{heading}[/b]  [dim]Ctrl+S save · Esc cancel[/dim]", classes="hint"
                )

            # Each row holds a left + right field, so Tab moves left→right then
            # down to the next row. Media type & status come first.
            yield Horizontal(
                _field("Media type", Select(_MEDIA_OPTIONS, value=d.media_type.value,
                                            allow_blank=False, id="f-media")),
                _field("Status", Select(_STATUS_OPTIONS, value=self.initial_status.value,
                                        allow_blank=False, id="f-status")),
                classes="form-row",
            )
            yield Horizontal(
                _field("Title", Input(d.title, id="f-title")),
                _field("Subtitle", Input(d.subtitle or "", id="f-subtitle")),
                classes="form-row",
            )
            yield Horizontal(
                _field("Authors (comma-separated)", Input(", ".join(d.authors), id="f-authors")),
                _field("Narrators (comma-separated)",
                       Input(", ".join(d.narrators), id="f-narrators")),
                classes="form-row",
            )
            yield Horizontal(
                _field("Series name", Input(d.series_name or "", id="f-series")),
                _field("Series position", Input(d.series_position or "", id="f-series-pos")),
                classes="form-row",
            )
            yield Horizontal(
                _field("Runtime (16:10 / 970)  •  audio", Input(runtime, id="f-runtime")),
                _field("Page count  •  print / ebook", Input(pages, id="f-pages")),
                classes="form-row",
            )
            yield Horizontal(
                _field("Genres (comma-separated)", Input(", ".join(d.genres), id="f-genres")),
                _field("Publisher", Input(d.publisher or "", id="f-publisher")),
                classes="form-row",
            )
            yield Horizontal(
                _field("Published date", Input(d.published_date or "", id="f-published")),
                Vertical(classes="field"),  # spacer keeps the date field half-width
                classes="form-row",
            )
            yield _field("Description", TextArea(d.description or "", id="f-description"))

            # The verdict — your overall rating + review for the book (both modes).
            rating_str = str(self.rating) if self.rating is not None else ""
            yield Horizontal(
                _field("Rating (0.5–5)", Input(rating_str, id="f-rating")),
                Vertical(classes="field"),  # spacer keeps the rating field half-width
                classes="form-row",
            )
            yield _field("Review", TextArea(self.review or "", id="f-review"))

            # Reading dates seed the first session and only make sense at create.
            if not self.is_edit:
                yield Horizontal(
                    _field("Started (YYYY-MM-DD)", Input(id="f-started")),
                    _field("Finished (YYYY-MM-DD)", Input(id="f-finished")),
                    classes="form-row",
                )

    def on_mount(self) -> None:
        # Focus Save so Enter accepts right away; Tab then reaches the fields.
        self.query_one("#save", Button).focus()

    def _collect(self) -> BookDraft:
        def val(wid: str) -> str:
            return self.query_one(f"#{wid}", Input).value.strip()

        media = MediaType(str(self.query_one("#f-media", Select).value))
        pages = val("f-pages")
        return BookDraft(
            title=val("f-title") or "Untitled",
            subtitle=val("f-subtitle") or None,
            authors=split_csv(val("f-authors")),
            narrators=split_csv(val("f-narrators")),
            genres=split_csv(val("f-genres")),
            media_type=media,
            cover_url=self.draft.cover_url,
            description=self.query_one("#f-description", TextArea).text.strip() or None,
            runtime_min=parse_runtime(val("f-runtime")),
            page_count=int(pages) if pages.isdigit() else None,
            publisher=val("f-publisher") or None,
            published_date=val("f-published") or None,
            series_name=val("f-series") or None,
            series_position=val("f-series-pos") or None,
            language=self.draft.language,
            external_source=self.draft.external_source,
            external_id=self.draft.external_id,
        )

    @on(Button.Pressed, "#save")
    def action_save(self) -> None:
        draft = self._collect()
        status = Status(str(self.query_one("#f-status", Select).value))
        if not self.is_edit:
            with session_scope() as session:
                dup = find_duplicate(session, draft_match_key(draft))
                dup_info = (dup.id, dup.title, dup.status.label) if dup else None
            if dup_info is not None:
                self._prompt_duplicate(dup_info, draft, status)
                return
        self._commit(draft, status)

    def _prompt_duplicate(
        self, dup_info: tuple[str, str, str], draft: BookDraft, status: Status
    ) -> None:
        from echobooks.screens.duplicate_prompt import DuplicateBookScreen

        dup_id, dup_title, dup_status = dup_info

        def _after(choice: str | None) -> None:
            if choice == "add":
                self._commit(draft, status)
            elif choice == "open":
                # Land on the existing book: stamp it for focus and unwind the
                # add flow (dismiss True so AddBookScreen closes too).
                self.app.pending_focus_book_id = dup_id  # type: ignore[attr-defined]
                self.app.notify(f"“{dup_title}” is already in your library")
                self.dismiss(True)
            # "cancel"/None: stay on the form so they can tweak and retry.

        self.app.push_screen(DuplicateBookScreen(dup_title, dup_status), _after)

    def _commit(self, draft: BookDraft, status: Status) -> None:
        rating = parse_rating(self.query_one("#f-rating", Input).value)
        review = self.query_one("#f-review", TextArea).text.strip() or None
        with session_scope() as session:
            if self.is_edit:
                book = get_book(session, self.book_id)  # type: ignore[arg-type]
                if book is None:
                    self.dismiss(False)
                    return
                update_book(session, book, draft)
                book.rating = rating
                book.review = review
                if book.status != status:
                    set_status(session, book, status)
                self.app.pending_focus_book_id = book.id  # type: ignore[attr-defined]
            else:
                started = parse_date(self.query_one("#f-started", Input).value)
                finished = parse_date(self.query_one("#f-finished", Input).value)
                book = create_book(
                    session,
                    draft,
                    status=status,
                    started_on=started,
                    finished_on=finished,
                    rating=rating,
                    review=review,
                )
                self.app.pending_focus_book_id = book.id  # type: ignore[attr-defined]
        self.app.notify("Saved" if self.is_edit else f"Added “{draft.title}”")
        self.app.schedule_sync()  # type: ignore[attr-defined]
        self.dismiss(True)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(False)
