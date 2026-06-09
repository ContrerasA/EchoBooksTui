"""Review and merge books that ended up duplicated (usually across devices)."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from echobooks.db.repository import find_duplicate_groups, merge_books
from echobooks.db.session import session_scope


class ResolveDuplicatesScreen(ModalScreen[None]):
    """Lists groups of same-book entries; merge each or keep both."""

    DEFAULT_CSS = """
    ResolveDuplicatesScreen { align: center middle; background: $background 60%; }
    #dialog {
        width: 72;
        max-height: 80%;
        height: auto;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    #groups { height: auto; max-height: 20; }
    .dup-row { height: auto; align: left middle; padding: 1 0; border-bottom: dashed $panel; }
    .dup-title { width: 1fr; content-align: left middle; }
    .dup-row Button { margin: 0 0 0 1; }
    #footer-row { height: auto; align: right middle; padding-top: 1; }
    """

    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self) -> None:
        super().__init__()
        # Groups the user chose to keep, so they don't reappear this session.
        self._kept: set[tuple[str, ...]] = set()
        self._groups: list[tuple[str, list[str]]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("[b]Resolve duplicates[/b]", classes="hint")
            yield VerticalScroll(id="groups")
            with Horizontal(id="footer-row"):
                yield Button("Done", variant="primary", id="done")

    def on_mount(self) -> None:
        self.sub_title = "duplicates"
        self._reload()

    def _reload(self) -> None:
        container = self.query_one("#groups", VerticalScroll)
        container.remove_children()
        self._groups = []
        with session_scope() as session:
            for group in find_duplicate_groups(session):
                ids = sorted(b.id for b in group)  # ids[0] is the deterministic survivor
                if tuple(ids) in self._kept:
                    continue
                self._groups.append((group[0].title, ids))

        if not self._groups:
            container.mount(Static("No duplicates to resolve.", classes="hint"))
            return
        for idx, (title, ids) in enumerate(self._groups):
            container.mount(
                Horizontal(
                    Static(f"{title}  [dim]×{len(ids)}[/dim]", classes="dup-title"),
                    Button("Merge", variant="success", id=f"merge-{idx}"),
                    Button("Keep both", id=f"keep-{idx}"),
                    classes="dup-row",
                )
            )

    @on(Button.Pressed, "#done")
    def action_close(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed)
    def _row_button(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid.startswith("merge-"):
            title, ids = self._groups[int(bid.split("-", 1)[1])]
            with session_scope() as session:
                merge_books(session, ids[0], ids[1:])
            self.app.notify(f"Merged “{title}”")
            self.app.schedule_sync()  # type: ignore[attr-defined]
            self._reload()
        elif bid.startswith("keep-"):
            _title, ids = self._groups[int(bid.split("-", 1)[1])]
            self._kept.add(tuple(ids))
            self._reload()
