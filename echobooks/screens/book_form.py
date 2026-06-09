"""Editable book form, shared by 'add' (create) and 'edit' (update) flows."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Input, Label, Select, Static, TextArea

from echobooks.db.models import MediaType, Status
from echobooks.db.repository import create_book, get_book, set_status, update_book
from echobooks.db.session import session_scope
from echobooks.providers.base import BookDraft
from echobooks.util import format_runtime, parse_date, parse_rating, parse_runtime, split_csv

_MEDIA_OPTIONS = [(m.label, m.value) for m in MediaType]
_STATUS_OPTIONS = [(s.label, s.value) for s in Status]


def _field(label: str, widget: Input | Select | TextArea) -> Vertical:
    """A labelled field; its label underlines when the field has focus."""
    if isinstance(widget, Input):
        # Don't select-all on focus — that draws an accent box over the value,
        # which reads as "highlighting the field". The label underline is the cue.
        widget.select_on_focus = False
    return Vertical(Label(label), widget, classes="field")


class BookFormScreen(Screen[bool]):
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
    ) -> None:
        super().__init__()
        self.draft = draft
        self.book_id = book_id
        self.initial_status = status

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

            if not self.is_edit:
                yield Horizontal(
                    _field("Started (YYYY-MM-DD)", Input(id="f-started")),
                    _field("Finished (YYYY-MM-DD)", Input(id="f-finished")),
                    _field("Rating (0.5–5)", Input(id="f-rating")),
                    classes="form-row",
                )

    def on_mount(self) -> None:
        # Focus Save so Enter accepts right away; Tab then reaches the fields.
        self.query_one("#save", Button).focus()

    def on_descendant_focus(self) -> None:
        self.call_after_refresh(self._highlight_active_field)

    def on_descendant_blur(self) -> None:
        self.call_after_refresh(self._highlight_active_field)

    def _highlight_active_field(self) -> None:
        """Underline the label of whichever field currently holds focus."""
        for field in self.query(".field"):
            active = "focus-within" in field.pseudo_classes
            for label in field.query(Label):
                label.set_class(active, "active-label")

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
        with session_scope() as session:
            if self.is_edit:
                book = get_book(session, self.book_id)  # type: ignore[arg-type]
                if book is None:
                    self.dismiss(False)
                    return
                update_book(session, book, draft)
                if book.status != status:
                    set_status(session, book, status)
            else:
                started = parse_date(self.query_one("#f-started", Input).value)
                finished = parse_date(self.query_one("#f-finished", Input).value)
                rating = parse_rating(self.query_one("#f-rating", Input).value)
                create_book(
                    session,
                    draft,
                    status=status,
                    started_on=started,
                    finished_on=finished,
                    rating=rating,
                )
        self.app.notify("Saved" if self.is_edit else f"Added “{draft.title}”")
        self.app.schedule_sync()  # type: ignore[attr-defined]
        self.dismiss(True)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(False)
