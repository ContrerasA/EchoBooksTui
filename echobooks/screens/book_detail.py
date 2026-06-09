"""Book detail: full metadata, reading sessions, and per-book actions."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Select, Static

from echobooks.db.models import MediaType, Status
from echobooks.db.repository import book_to_draft, get_book, set_status, soft_delete_book
from echobooks.db.session import session_scope
from echobooks.util import format_runtime, stars

_STATUS_OPTIONS = [(s.label, s.value) for s in Status]


class BookDetailScreen(Screen[None]):
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("e", "edit", "Edit"),
        Binding("n", "add_session", "Add session"),
        Binding("f", "favorite", "Favorite"),
        Binding("d", "delete", "Delete"),
    ]

    def __init__(self, book_id: str) -> None:
        super().__init__()
        self.book_id = book_id
        # Seed the Select with the book's real status so it doesn't fire a
        # spurious Changed (defaulting to the first option) that would overwrite
        # the stored status. _current_status lets us ignore programmatic sets.
        with session_scope() as session:
            book = get_book(session, book_id)
            self._current_status = book.status if book else Status.WANT
        self._status_select = Select(
            _STATUS_OPTIONS,
            value=self._current_status.value,
            allow_blank=False,
            id="status-select",
        )

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            yield Static(id="detail-meta", classes="panel")
            with Horizontal(classes="actions"):
                yield Static("Status:", classes="hint")
                yield self._status_select
                yield Button("Edit (e)", id="edit")
                yield Button("Add session (n)", id="add-session")
                yield Button("Delete (d)", variant="error", id="delete")
            yield Static("[b]Reading sessions[/b]", classes="hint")
            yield DataTable(id="sessions", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#sessions", DataTable)
        table.add_columns("Started", "Finished", "Rating", "Media", "Review")
        self.reload()

    def on_screen_resume(self) -> None:
        self.reload()

    def reload(self) -> None:
        with session_scope() as session:
            book = get_book(session, self.book_id)
            if book is None:
                self.app.notify("Book not found", severity="warning")
                self.dismiss()
                return
            self.query_one("#detail-meta", Static).update(_render_meta(book))
            # Set _current_status first so the resulting Changed is treated as
            # programmatic (a no-op) rather than a user edit.
            self._current_status = book.status
            self._status_select.value = book.status.value
            self.sub_title = book.title

            table = self.query_one("#sessions", DataTable)
            table.clear()
            for s in sorted(book.sessions, key=lambda x: (x.finished_on or x.started_on or _MIN)):
                table.add_row(
                    s.started_on.isoformat() if s.started_on else "—",
                    s.finished_on.isoformat() if s.finished_on else "—",
                    stars(s.rating),
                    (s.media_type.label if s.media_type else "—"),
                    (s.review or "")[:50],
                    key=s.id,
                )

    # -- actions ---------------------------------------------------------- #
    @on(Select.Changed, "#status-select")
    def _status_changed(self, event: Select.Changed) -> None:
        new_status = Status(str(event.value))
        if new_status == self._current_status:
            return  # programmatic / initial set — not a user action
        self._current_status = new_status
        with session_scope() as session:
            book = get_book(session, self.book_id)
            if book and book.status != new_status:
                set_status(session, book, new_status)
        self.app.schedule_sync()  # type: ignore[attr-defined]
        self.reload()

    @on(Button.Pressed, "#edit")
    def action_edit(self) -> None:
        from echobooks.screens.book_form import BookFormScreen

        with session_scope() as session:
            book = get_book(session, self.book_id)
            if book is None:
                return
            draft = book_to_draft(book)
            status = book.status
        self.app.push_screen(
            BookFormScreen(draft, book_id=self.book_id, status=status),
            lambda _saved: self.reload(),
        )

    @on(Button.Pressed, "#add-session")
    def action_add_session(self) -> None:
        from echobooks.screens.session_edit import SessionEditScreen

        self.app.push_screen(SessionEditScreen(self.book_id), lambda _saved: self.reload())

    @on(DataTable.RowSelected, "#sessions")
    def _edit_session(self, event: DataTable.RowSelected) -> None:
        from echobooks.screens.session_edit import SessionEditScreen

        if event.row_key.value:
            self.app.push_screen(
                SessionEditScreen(self.book_id, session_id=event.row_key.value),
                lambda _saved: self.reload(),
            )

    def action_favorite(self) -> None:
        with session_scope() as session:
            book = get_book(session, self.book_id)
            if book:
                book.is_favorite = not book.is_favorite
                book.dirty = True
                fav = book.is_favorite
        self.app.notify("★ Favorited" if fav else "Unfavorited")
        self.app.schedule_sync()  # type: ignore[attr-defined]
        self.reload()

    def action_delete(self) -> None:
        from echobooks.screens.confirm import ConfirmScreen

        with session_scope() as session:
            book = get_book(session, self.book_id)
            title = book.title if book else None
        if title is None:
            return

        def _after(confirmed: bool | None) -> None:
            if not confirmed:
                return
            with session_scope() as session:
                book = get_book(session, self.book_id)
                if book:
                    soft_delete_book(session, book)
            self.app.notify(f"Deleted “{title}”")
            self.app.schedule_sync()  # type: ignore[attr-defined]
            self.dismiss()

        self.app.push_screen(ConfirmScreen(f"Delete “{title}”?"), _after)

    def action_back(self) -> None:
        self.dismiss()


from datetime import date as _date_cls  # noqa: E402

_MIN = _date_cls.min


def _render_meta(book) -> str:
    lines: list[str] = []
    star = "★ " if book.is_favorite else ""
    lines.append(f"[b]{star}{book.title}[/b]")
    if book.subtitle:
        lines.append(f"[i]{book.subtitle}[/i]")
    lines.append("")
    lines.append(f"by [b]{book.author_names}[/b]")
    if book.media_type == MediaType.AUDIOBOOK and book.narrators:
        lines.append(f"narrated by {book.narrator_names}")
    lines.append("")

    facts = [f"{book.media_type.label}", f"{book.status.label}"]
    if book.media_type == MediaType.AUDIOBOOK:
        facts.append(f"⏱ {format_runtime(book.runtime_min)}")
    elif book.page_count:
        facts.append(f"📄 {book.page_count} pages")
    if book.best_rating:
        facts.append(f"{stars(book.best_rating)}")
    lines.append("  ·  ".join(facts))

    if book.series_name:
        pos = f" #{book.series_position}" if book.series_position else ""
        lines.append(f"Series: {book.series_name}{pos}")
    extra = []
    if book.publisher:
        extra.append(f"Publisher: {book.publisher}")
    if book.published_date:
        extra.append(f"Published: {book.published_date}")
    if book.language:
        extra.append(f"Language: {book.language}")
    if extra:
        lines.append("  ·  ".join(extra))
    genres = [t.name for t in book.tags if t.kind == "genre"]
    if genres:
        lines.append(f"Genres: {', '.join(genres)}")
    if book.description:
        lines.append("")
        lines.append(book.description)
    return "\n".join(lines)
