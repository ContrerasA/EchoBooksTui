"""The home screen: a filterable, sortable table of the whole catalog."""

from __future__ import annotations

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Input, Select

from echobooks.db.models import MediaType, Status
from echobooks.db.repository import book_to_draft, get_book, list_books, soft_delete_book
from echobooks.db.session import session_scope
from echobooks.screens.fields import LabelledFields, field
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

_HEADERS = ["Title", "Author", "Series", "Type", "Status", "Rating", "Length"]
# The free-text columns that flex with the terminal width; the rest (Type,
# Status, Rating, Length) have bounded content and keep their natural width.
# Each flex column shrinks no smaller than its minimum before the table starts
# to scroll horizontally.
_FLEX_COLS = (0, 1, 2)
_FLEX_MIN = {0: 12, 1: 10, 2: 10}


class LibraryScreen(LabelledFields, Screen[None]):
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
            yield field("Search", Input(placeholder="Title or author…", id="search"))
            yield field(
                "Status",
                Select(_STATUS_OPTIONS, value="ALL", allow_blank=False, id="status"),
                classes="narrow",
            )
            yield field(
                "Sort",
                Select(_SORT_OPTIONS, value="author", allow_blank=False, id="sort"),
                classes="narrow",
            )
        yield DataTable(id="books", cursor_type="row", zebra_stripes=False, cell_padding=1)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#books", DataTable)
        self._col_keys = table.add_columns(*_HEADERS)
        # Full, untruncated cell text per row, kept so we can re-truncate the
        # flex columns on resize without re-querying the database.
        self._rows: list[tuple[str, list[str]]] = []
        self._flex_widths: list[int] | None = None
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
        self._rows = []
        with session_scope() as session:
            books = list_books(session, status=status, search=search, sort=str(sort_val))
            count = len(books)
            for book in books:
                length = (
                    format_runtime(book.runtime_min)
                    if book.media_type == MediaType.AUDIOBOOK
                    else (f"{book.page_count}p" if book.page_count else "—")
                )
                cells = [
                    book.title,
                    book.author_names,
                    _series_label(book),
                    book.media_type.label,
                    book.status.label,
                    stars(book.best_rating),
                    length,
                ]
                self._rows.append((book.id, cells))
                table.add_row(*cells, key=book.id)
        self.sub_title = f"{count} book{'s' if count != 1 else ''}"
        self._focus_pending_book(table)
        self._flex_widths = None  # row set changed — force a re-fit
        self._fit_columns()

    def on_resize(self, event: events.Resize) -> None:
        # Re-truncate the flex columns once the table has taken its new size.
        self.call_after_refresh(self._fit_columns)

    def _fit_columns(self) -> None:
        """Size Title/Author/Series to the available width, truncating to fit.

        Bounded columns (Type/Status/Rating/Length) keep their natural width; the
        remaining space is split among the flex columns, each clamped to its
        minimum. Below that the table scrolls horizontally rather than hide data.
        """
        if not self.is_mounted or not getattr(self, "_rows", None):
            return
        table = self.query_one("#books", DataTable)
        avail = table.size.width - 2  # leave room for the scrollbar gutter
        if avail <= 0:
            return  # not laid out yet; on_resize will call us again

        naturals = [
            max(len(_HEADERS[c]), *(len(cells[c]) for _, cells in self._rows))
            for c in range(len(_HEADERS))
        ]
        padding = 2 * table.cell_padding * len(_HEADERS)
        fixed = sum(w for c, w in enumerate(naturals) if c not in _FLEX_COLS)
        budget = avail - padding - fixed

        flex_nat = [naturals[c] for c in _FLEX_COLS]
        flex_min = [min(_FLEX_MIN[c], naturals[c]) for c in _FLEX_COLS]
        widths = _fit_flex_widths(flex_nat, flex_min, budget)
        if widths == self._flex_widths:
            return
        self._flex_widths = widths

        for pos, c in enumerate(_FLEX_COLS):
            width = widths[pos]
            col_key = self._col_keys[c]
            last = len(self._rows) - 1
            for i, (row_id, cells) in enumerate(self._rows):
                # Refresh the column's auto-width only once, on the final cell.
                table.update_cell(
                    row_id, col_key, _truncate(cells[c], width), update_width=(i == last)
                )

    def _focus_pending_book(self, table: DataTable) -> None:
        """If an add flow just created a book, select and scroll to its row."""
        book_id = self.app.pending_focus_book_id  # type: ignore[attr-defined]
        if book_id is None:
            return
        self.app.pending_focus_book_id = None  # type: ignore[attr-defined]
        try:
            row_index = table.get_row_index(book_id)
        except Exception:
            return  # filtered out by the active search/status — nothing to focus
        table.move_cursor(row=row_index, scroll=True)

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
            self.app.schedule_sync()  # type: ignore[attr-defined]

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


def _truncate(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width == 1:
        return "…"
    return text[: width - 1] + "…"


def _fit_flex_widths(naturals: list[int], mins: list[int], budget: int) -> list[int]:
    """Distribute ``budget`` cells across the flex columns.

    Columns get their natural width when everything fits, fall back to their
    minimums when space is tight, and otherwise shrink proportionally to how
    much slack (natural − min) each one has.
    """
    if budget >= sum(naturals):
        return list(naturals)
    if budget <= sum(mins):
        return list(mins)
    excess = sum(naturals) - budget
    slack = [n - m for n, m in zip(naturals, mins)]
    total = sum(slack) or 1
    return [
        max(mins[i], naturals[i] - round(excess * slack[i] / total))
        for i in range(len(naturals))
    ]
