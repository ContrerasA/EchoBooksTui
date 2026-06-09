"""Settings: data location, active providers, and account / sync."""

from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, Static, Switch

from echobooks.config import db_path


class SettingsScreen(Screen[None]):
    BINDINGS = [Binding("escape", "back", "Back")]

    def compose(self) -> ComposeResult:
        s = self.app.settings  # type: ignore[attr-defined]
        yield Header()
        with VerticalScroll(classes="panel"):
            yield Static("[b]Settings[/b]", classes="hint")
            yield Static(f"Database: [dim]{db_path()}[/dim]", classes="hint")

            with Horizontal(classes="form-field"):
                yield Switch(value=s.use_openlibrary, id="use-ol")
                yield Label("  Use Open Library (print / ebook)")
            with Horizontal(classes="form-field"):
                yield Switch(value=s.use_audible, id="use-audible")
                yield Label("  Use Audible + Audnexus (audiobooks)")

            yield Label("Audible region")
            yield Input(s.audible_region, id="region")

            # --- Account & sync --------------------------------------------- #
            yield Static("\n[b]Account & sync[/b]", classes="hint")
            yield from self._account_section(s)

            with Horizontal(classes="actions"):
                yield Button("Save", variant="success", id="save")
                yield Button("Back", id="back")
        yield Footer()

    def _account_section(self, s) -> ComposeResult:
        if s.is_logged_in():
            yield Static(
                f"Signed in as [b]{s.user_email or 'your account'}[/b]\n"
                f"[dim]Server: {s.server_url}[/dim]",
                classes="hint",
            )
            with Horizontal(classes="actions"):
                yield Button("Sync now", variant="primary", id="sync-now")
                yield Button("Sign out", variant="error", id="sign-out")
        else:
            yield Static(
                "[dim]Sign in to sync your catalog to your own server. "
                "The app stays fully usable offline either way.[/dim]",
                classes="hint",
            )
            yield Label("Server URL (e.g. https://echobooks.yourdomain.dev)")
            yield Input(s.server_url, id="server-url", placeholder="https://…")
            with Horizontal(classes="actions"):
                yield Button("Sign in with Google", variant="primary", id="sign-in")

    # -- providers save -------------------------------------------------- #
    @on(Button.Pressed, "#save")
    def _save(self) -> None:
        s = self.app.settings  # type: ignore[attr-defined]
        s.use_openlibrary = self.query_one("#use-ol", Switch).value
        s.use_audible = self.query_one("#use-audible", Switch).value
        s.audible_region = self.query_one("#region", Input).value.strip() or "us"
        s.save()
        from echobooks.providers.registry import ProviderRegistry

        old = self.app.registry  # type: ignore[attr-defined]
        self.app.registry = ProviderRegistry(s)  # type: ignore[attr-defined]
        self.app.call_later(old.aclose)
        self.app.notify("Settings saved")
        self.dismiss()

    # -- account actions ------------------------------------------------- #
    @on(Button.Pressed, "#sign-in")
    def _sign_in(self) -> None:
        from echobooks.screens.login import LoginScreen

        s = self.app.settings  # type: ignore[attr-defined]
        url = self.query_one("#server-url", Input).value.strip()
        if not url:
            self.app.notify("Enter your server URL first", severity="warning")
            return
        s.server_url = url.rstrip("/")
        s.save()

        def _after_login(ok: bool | None) -> None:
            if ok:
                self._offer_import()

        self.app.push_screen(LoginScreen(self.app.sync_client), _after_login)  # type: ignore[attr-defined]

    def _offer_import(self) -> None:
        """After login, ask which local titles to upload, then sync."""
        from echobooks.screens.import_picker import ImportPickerScreen

        def _after_pick(book_ids: list[str] | None) -> None:
            self._run_import_and_sync(book_ids or [])

        self.app.push_screen(
            ImportPickerScreen("Import your existing books to your account?"), _after_pick
        )

    @work(exclusive=True, group="sync")
    async def _run_import_and_sync(self, book_ids: list[str]) -> None:
        from echobooks.db.session import get_sessionmaker
        from echobooks.sync.engine import import_local, sync

        factory = get_sessionmaker()
        client = self.app.sync_client  # type: ignore[attr-defined]
        s = self.app.settings  # type: ignore[attr-defined]
        try:
            if book_ids:
                uploaded = await import_local(factory, client, book_ids)
                self.app.notify(f"Uploaded {uploaded} record(s)")
            result = await sync(factory, client, since=s.last_sync or None)
        except Exception as exc:  # noqa: BLE001 - surface any failure to the user
            self.app.notify(f"Sync failed: {exc}", severity="error")
            return
        s.last_sync = result.at
        s.save()
        self.app.notify("Signed in and synced")
        self.dismiss()

    @on(Button.Pressed, "#sync-now")
    @work(exclusive=True, group="sync")
    async def _sync_now(self) -> None:
        from echobooks.db.session import get_sessionmaker
        from echobooks.sync.engine import sync

        s = self.app.settings  # type: ignore[attr-defined]
        try:
            result = await sync(
                get_sessionmaker(), self.app.sync_client, since=s.last_sync or None  # type: ignore[attr-defined]
            )
        except Exception as exc:  # noqa: BLE001
            self.app.notify(f"Sync failed: {exc}", severity="error")
            return
        s.last_sync = result.at
        s.save()
        self.app.notify(f"Synced — {result.applied} update(s) pulled")

    @on(Button.Pressed, "#sign-out")
    def _sign_out(self) -> None:
        s = self.app.settings  # type: ignore[attr-defined]
        s.clear_account()
        self.app.notify("Signed out — your local catalog is untouched")
        self.dismiss()

    @on(Button.Pressed, "#back")
    def action_back(self) -> None:
        self.dismiss()
