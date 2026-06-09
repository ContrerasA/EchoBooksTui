"""Choose which local titles to upload to the account.

Reused for two moments:

* **First login** — "Import your existing books to your account?"
* **Second machine** — "This device has offline books — choose which to sync up."

Returns the list of selected book ids (empty if the user imports nothing). The
caller pushes those via :func:`echobooks.sync.engine.import_local`, then runs a
full sync to pull the merged account back down.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, SelectionList, Static
from textual.widgets.selection_list import Selection

from echobooks.db.repository import list_books
from echobooks.db.session import session_scope


class ImportPickerScreen(Screen[list[str]]):
    """Pick local books to upload. Dismisses the selected book ids."""

    BINDINGS = [
        Binding("ctrl+s", "import_selected", "Import selected"),
        Binding("escape", "skip", "Skip"),
    ]

    def __init__(self, prompt: str = "Import these titles to your account?") -> None:
        super().__init__()
        self.prompt = prompt
        # (book_id, label) pairs, loaded off the local catalog.
        self._items: list[tuple[str, str]] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(classes="panel"):
            yield Static(f"[b]{self.prompt}[/b]", classes="hint")
            yield Static(
                "Space toggles · Ctrl+S imports the checked titles · Esc skips.",
                classes="hint",
            )
            yield SelectionList[str](id="to-import")
            with Horizontal(classes="actions"):
                yield Button("Import selected", variant="success", id="import")
                yield Button("Select all", id="all")
                yield Button("Skip", id="skip")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = "import"
        with session_scope() as session:
            books = list_books(session, sort="title")
            self._items = [(b.id, f"{b.title}  ·  {b.author_names}") for b in books]
        sel = self.query_one("#to-import", SelectionList)
        for book_id, label in self._items:
            sel.add_option(Selection(label, book_id, initial_state=True))
        sel.focus()

    @on(Button.Pressed, "#import")
    def action_import_selected(self) -> None:
        chosen = list(self.query_one("#to-import", SelectionList).selected)
        self.dismiss(chosen)

    @on(Button.Pressed, "#all")
    def _select_all(self) -> None:
        self.query_one("#to-import", SelectionList).select_all()

    @on(Button.Pressed, "#skip")
    def action_skip(self) -> None:
        self.dismiss([])
