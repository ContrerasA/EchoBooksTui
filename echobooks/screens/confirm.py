"""A small yes/no confirmation modal."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConfirmScreen(ModalScreen[bool]):
    """Returns True if confirmed, False otherwise."""

    DEFAULT_CSS = """
    ConfirmScreen { align: center middle; background: $background 60%; }
    #dialog {
        width: 56;
        height: auto;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    #dialog .buttons { height: auto; align: center middle; padding-top: 1; }
    #dialog Button { margin: 0 1; }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, message: str, confirm_label: str = "Delete") -> None:
        super().__init__()
        self.message = message
        self.confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self.message)
            with Horizontal(classes="buttons"):
                yield Button(self.confirm_label, variant="error", id="yes")
                yield Button("Cancel", id="no")

    def on_mount(self) -> None:
        self.query_one("#no", Button).focus()

    @on(Button.Pressed, "#yes")
    def _yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#no")
    def action_cancel(self) -> None:
        self.dismiss(False)
