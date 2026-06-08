"""The home screen: a filterable, sortable table of the whole catalog."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Input, Select

from echobooks.db.models import MediaType, Status
from echobooks.db.repository import book_to_draft, get_book, list_books, soft_delete_book
from echobooks.db.session import session_scope
from echobooks.util import format_runtime, stars

_STATUS_OPTIONS = [("All", "ALL"), *[(s.label, s.value) for s in Status]]
_SORT_OPTIONS = [
    ("Author", "author"),
    ("Title", "title"),
    ("Recently added", "added"),
    ("Recently updated", "updated"),
    ("Longest runtime", "runtime"),
    ("Most pages", "pages"),
]


class LibraryScreen(Screen[None]):
    BINDINGS = [
        Binding("a", "add", "Add book"),
        Binding("e", "edit", "Edit"),
        Binding("d", "delete", "Delete"),
        Binding("s", "stats", "Stats"),
        Binding("ctrl+s", "settings", "Settings"),
        Binding("slash", "focus_search", "Search", show=False),
        Binding("r", "refresh", "Refresh", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(classes="toolbar"):
            yield Input(placeholder="Search title or author…", id="search")
            yield Select(_STATUS_OPTIONS, value="ALL", allow_blank=False, id="status")
            yield Select(_SORT_OPTIONS, value="author", allow_blank=False, id="sort")
        yield DataTable(id="books", cursor_type="row", zebra_stripes=False, cell_padding=3)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#books", DataTable)
        table.add_columns("Title", "Author", "Series", "Type", "Status", "Rating", "Length")
        self.reload()
        table.focus()

    def on_screen_resume(self) -> None:
        self.reload()
        # Refocus the table so single-key actions (q/a/s) work without first
        # tabbing off the search box or a Select.
        self.query_one("#books", DataTable).focus()

    # -- data ------------------------------------------------------------- #
    def reload(self) -> None:
        if not self.is_mounted:
            return
        search = self.query_one("#search", Input).value
        status_val = self.query_one("#status", Select).value
        sort_val = self.query_one("#sort", Select).value
        status = None if status_val == "ALL" else Status(str(status_val))

        table = self.query_one("#books", DataTable)
        table.clear()
        with session_scope() as session:
            books = list_books(session, status=status, search=search, sort=str(sort_val))
            count = len(books)
            for book in books:
                length = (
                    format_runtime(book.runtime_min)
                    if book.media_type == MediaType.AUDIOBOOK
                    else (f"{book.page_count}p" if book.page_count else "—")
                )
                table.add_row(
                    book.title,
                    book.author_names,
                    _series_label(book),
                    book.media_type.label,
                    book.status.label,
                    stars(book.best_rating),
                    length,
                    key=book.id,
                )
        self.sub_title = f"{count} book{'s' if count != 1 else ''}"

    # -- events ----------------------------------------------------------- #
    @on(Input.Changed, "#search")
    @on(Select.Changed, "#status")
    @on(Select.Changed, "#sort")
    def _filters_changed(self) -> None:
        self.reload()

    @on(Input.Submitted, "#search")
    def _search_submit(self) -> None:
        self.query_one("#books", DataTable).focus()

    @on(DataTable.RowSelected, "#books")
    def _open_book(self, event: DataTable.RowSelected) -> None:
        from echobooks.screens.book_detail import BookDetailScreen

        if event.row_key.value:
            self.app.push_screen(BookDetailScreen(event.row_key.value))

    def _current_book_id(self) -> str | None:
        table = self.query_one("#books", DataTable)
        if table.row_count == 0:
            return None
        try:
            cell_key = table.coordinate_to_cell_key(table.cursor_coordinate)
        except Exception:
            return None
        value = cell_key.row_key.value
        return str(value) if value is not None else None

    # -- actions ---------------------------------------------------------- #
    def action_add(self) -> None:
        from echobooks.screens.add_book import AddBookScreen

        self.app.push_screen(AddBookScreen())

    def action_edit(self) -> None:
        from echobooks.screens.book_form import BookFormScreen

        book_id = self._current_book_id()
        if book_id is None:
            return
        with session_scope() as session:
            book = get_book(session, book_id)
            if book is None:
                return
            draft = book_to_draft(book)
            status = book.status
        self.app.push_screen(
            BookFormScreen(draft, book_id=book_id, status=status),
            lambda _saved: self.reload(),
        )

    def action_delete(self) -> None:
        from echobooks.screens.confirm import ConfirmScreen

        book_id = self._current_book_id()
        if book_id is None:
            return
        with session_scope() as session:
            book = get_book(session, book_id)
            if book is None:
                return
            title = book.title

        def _after(confirmed: bool | None) -> None:
            if not confirmed:
                return
            with session_scope() as session:
                book = get_book(session, book_id)
                if book:
                    soft_delete_book(session, book)
            self.reload()
            self.app.notify(f"Deleted “{title}”")

        self.app.push_screen(ConfirmScreen(f"Delete “{title}”?"), _after)

    def action_stats(self) -> None:
        from echobooks.screens.stats import StatsScreen

        self.app.push_screen(StatsScreen())

    def action_settings(self) -> None:
        from echobooks.screens.settings import SettingsScreen

        self.app.push_screen(SettingsScreen())

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_refresh(self) -> None:
        self.reload()


def _series_label(book) -> str:
    if not book.series_name:
        return "—"
    if book.series_position:
        return f"{book.series_name} #{book.series_position}"
    return book.series_name
