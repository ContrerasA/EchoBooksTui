"""Settings: data location, active providers, and a placeholder for sync."""

from __future__ import annotations

from textual import on
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

            yield Static(
                "\n[b]Account & sync[/b]\n[dim]Coming soon — accounts with auto-sync to your "
                "self-hosted server. The local catalog already records everything needed to "
                "sync later.[/dim]",
                classes="hint",
            )
            with Horizontal(classes="actions"):
                yield Button("Save", variant="success", id="save")
                yield Button("Back", id="back")
        yield Footer()

    @on(Button.Pressed, "#save")
    def _save(self) -> None:
        s = self.app.settings  # type: ignore[attr-defined]
        s.use_openlibrary = self.query_one("#use-ol", Switch).value
        s.use_audible = self.query_one("#use-audible", Switch).value
        s.audible_region = self.query_one("#region", Input).value.strip() or "us"
        s.save()
        # Rebuild providers so region/toggles take effect immediately.
        from echobooks.providers.registry import ProviderRegistry

        old = self.app.registry  # type: ignore[attr-defined]
        self.app.registry = ProviderRegistry(s)  # type: ignore[attr-defined]
        self.app.call_later(old.aclose)
        self.app.notify("Settings saved")
        self.dismiss()

    @on(Button.Pressed, "#back")
    def action_back(self) -> None:
        self.dismiss()
