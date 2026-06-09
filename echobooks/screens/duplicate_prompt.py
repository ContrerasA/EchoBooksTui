"""Shown on add when the book already exists: open it, add anyway, or cancel."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class DuplicateBookScreen(ModalScreen[str]):
    """Returns one of ``"open"``, ``"add"``, or ``"cancel"``."""

    DEFAULT_CSS = """
    DuplicateBookScreen { align: center middle; background: $background 60%; }
    #dialog {
        width: 60;
        height: auto;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    #dialog .buttons { height: auto; align: center middle; padding-top: 1; }
    #dialog Button { margin: 0 1; }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, status_label: str) -> None:
        super().__init__()
        self.title_text = title
        self.status_label = status_label

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(
                f"[b]“{self.title_text}”[/b] is already in your library "
                f"([i]{self.status_label}[/i])."
            )
            with Horizontal(classes="buttons"):
                yield Button("Open it", variant="primary", id="open")
                yield Button("Add anyway", variant="warning", id="add")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#open", Button).focus()

    @on(Button.Pressed, "#open")
    def _open(self) -> None:
        self.dismiss("open")

    @on(Button.Pressed, "#add")
    def _add(self) -> None:
        self.dismiss("add")

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss("cancel")
