"""The EchoBooks Textual application shell."""

from __future__ import annotations

from textual.app import App
from textual.binding import Binding
from textual.theme import Theme

from echobooks.config import Settings
from echobooks.db.session import init_db
from echobooks.providers.registry import ProviderRegistry
from echobooks.screens.library import LibraryScreen

ECHOBOOKS_THEME = Theme(
    name="echobooks",
    primary="#F54257",
    accent="#F54257",
    secondary="#7AA2F7",
    success="#9ECE6A",
    warning="#E0AF68",
    error="#F7768E",
    foreground="#C8CCD4",
    background="#0E0E12",
    surface="#16161D",
    panel="#1C1C26",
    boost="#2A2A36",
    dark=True,
)


class EchoBooksApp(App[None]):
    TITLE = "EchoBooks"
    SUB_TITLE = "your reading & listening catalog"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
    ]

    CSS = """
    /* Only the home screen is transparent (shows the terminal through). Pushed
       screens stay opaque so they fully cover what's behind — otherwise a
       transparent dialog reveals the screen beneath it. */
    LibraryScreen { background: transparent; }
    Header { background: transparent; }
    Footer { background: transparent; }
    FooterKey { background: transparent; }

    /* Breathing room between the header, the search/filter bar, and the list. */
    .toolbar {
        height: auto;
        padding: 0 1;
        margin: 1 1 0 1;
        background: transparent;
    }
    .toolbar Input { width: 1fr; }
    .toolbar Select { width: 28; }

    #books {
        height: 1fr;
        margin: 1;
        background: transparent;
    }
    /* Header: bold, no background fill. */
    #books > .datatable--header {
        background: transparent;
        color: $accent;
        text-style: bold;
    }
    /* Selected row: accent underline instead of a background highlight. */
    #books > .datatable--cursor {
        background: transparent;
        color: $accent;
        text-style: underline;
    }

    .form-field { height: auto; margin: 0 0 1 0; }
    .form-field Label { color: $text-muted; }

    .panel {
        border: round $primary;
        padding: 1 2;
        margin: 1;
        height: auto;
        background: transparent;
    }

    /* Add/edit form: rows of .field (label + control). Compact height-1
       borderless controls; the field's LABEL underlines when it has focus. */
    .form-row { height: auto; }
    .field { width: 1fr; height: auto; padding: 0 1; }
    .field Label { color: $text-muted; height: 1; }
    /* Toggled from Python on focus (see BookFormScreen) — a pure CSS
       :focus-within descendant rule didn't apply reliably in this app. */
    .field Label.active-label { color: $accent; text-style: underline; }
    .field Input {
        height: 1;
        border: none;
        padding: 0 1;
        margin: 0 0 1 0;
        background: $boost;
    }
    .field Select { margin: 0 0 1 0; }
    .field Select SelectCurrent { border: none; height: 1; background: $boost; }
    #f-description { height: 4; background: $boost; }

    .stat-grid { layout: grid; grid-size: 4; grid-gutter: 1; height: auto; }
    .stat-card {
        border: round $accent;
        padding: 1;
        height: auto;
        content-align: center middle;
        text-align: center;
        background: transparent;
    }
    .stat-number { text-style: bold; color: $accent; }

    /* Text bar-chart panels (Static content, so height:auto is safe — no
       draw-to-fill feedback loop like a plotting widget would have). */
    .chart {
        height: auto;
        border: round $primary;
        padding: 0 1;
        margin: 1;
        background: transparent;
    }

    .actions { height: auto; padding: 1; }
    .actions Button { margin: 0 1 0 0; }

    SelectionList { height: auto; max-height: 20; background: transparent; }

    #detail-meta { height: 1fr; }
    .hint { color: $text-muted; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.ansi_color = True
        self.settings = Settings.load()
        self.registry = ProviderRegistry(self.settings)

    def on_mount(self) -> None:
        self.register_theme(ECHOBOOKS_THEME)
        self.theme = "echobooks"
        self.push_screen(LibraryScreen())

    async def on_unmount(self) -> None:
        await self.registry.aclose()


def run() -> None:
    init_db()
    EchoBooksApp().run()
