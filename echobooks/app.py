"""The EchoBooks Textual application shell."""

from __future__ import annotations

import asyncio

from textual import work
from textual.app import App
from textual.binding import Binding
from textual.theme import Theme

from echobooks.config import Settings
from echobooks.db.session import init_db
from echobooks.providers.registry import ProviderRegistry
from echobooks.screens.library import LibraryScreen
from echobooks.sync.client import SyncClient

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

# Hard cap on the final flush-on-quit so an unreachable server can't hang exit.
# A change that doesn't make it up stays dirty and syncs on next launch anyway.
_SYNC_QUIT_TIMEOUT = 5.0


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
        # Lazily built (only an account user needs it); see sync_client.
        self._sync_client: SyncClient | None = None
        # True once a local change has been queued but not yet pushed; lets the
        # on-quit flush know there's something to send (see on_unmount).
        self._sync_pending = False

    @property
    def sync_client(self) -> SyncClient:
        """The server connection, created on first use and owned by the app."""
        if self._sync_client is None:
            self._sync_client = SyncClient(self.settings)
        return self._sync_client

    def on_mount(self) -> None:
        self.register_theme(ECHOBOOKS_THEME)
        self.theme = "echobooks"
        self.push_screen(LibraryScreen())
        if self.settings.is_logged_in():
            self._launch_sync()

    def schedule_sync(self) -> None:
        """Push a local change to the server in the background.

        Screens call this right after committing a mutation (add / edit / delete
        / status / favorite / session). No-ops when logged out — there's nothing
        to push to. The work runs in the exclusive ``sync`` worker, so rapid
        changes coalesce: a new run cancels any in-flight one, and because the
        sync re-reads *all* dirty rows, the final run still pushes everything.
        """
        if not self.settings.is_logged_in():
            return
        self._sync_pending = True
        self._launch_sync()

    async def _do_sync(self) -> int | None:
        """Run one sync cycle. Returns the count of pulled updates, or None on
        failure. Shared by the debounced worker and the on-quit flush.

        Failures stay silent → the app keeps working offline. The pending flag
        only clears on success, so a change that failed to push is retried on
        quit and on the next launch.
        """
        from echobooks.db.session import get_sessionmaker
        from echobooks.sync.engine import sync

        try:
            result = await sync(
                get_sessionmaker(), self.sync_client, since=self.settings.last_sync or None
            )
        except Exception:
            return None  # offline / server down / token expired — just stay local
        self.settings.last_sync = result.at
        self.settings.save()
        self._sync_pending = False
        return result.applied

    @work(exclusive=True, group="sync")
    async def _launch_sync(self) -> None:
        """Best-effort sync on startup and after local changes."""
        applied = await self._do_sync()
        if applied:
            self.notify(f"Synced — {applied} update(s) from your account")

    async def on_unmount(self) -> None:
        # Flush a pending change-triggered sync before we tear down, so adding a
        # book and immediately quitting still pushes it: the background push may
        # still be in flight when shutdown cancels its worker. Running the sync
        # directly here, bounded by a timeout, covers that. Anything that doesn't
        # make it up stays dirty and syncs on next launch.
        if self._sync_pending and self.settings.is_logged_in():
            try:
                await asyncio.wait_for(self._do_sync(), timeout=_SYNC_QUIT_TIMEOUT)
            except TimeoutError:
                pass  # server too slow — change is safe locally, syncs next time
        await self.registry.aclose()
        if self._sync_client is not None:
            await self._sync_client.aclose()


def run() -> None:
    init_db()
    EchoBooksApp().run()
