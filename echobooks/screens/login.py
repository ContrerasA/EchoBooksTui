"""Google device-flow login.

Shows the verification URL + user code, then polls the server in a background
worker until the user approves (or it times out / is cancelled). On success the
issued tokens are stored in :class:`Settings` and the screen dismisses ``True``.
"""

from __future__ import annotations

import asyncio

import httpx
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from echobooks.sync.client import AuthError, AuthPending, SyncClient


class LoginScreen(ModalScreen[bool]):
    """Device-flow sign-in. Dismisses True on success, False on cancel/failure."""

    DEFAULT_CSS = """
    LoginScreen { align: center middle; background: $background 60%; }
    #login-dialog {
        width: 64;
        height: auto;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    #login-code { text-style: bold; color: $accent; text-align: center; padding: 1 0; }
    #login-dialog .buttons { height: auto; align: center middle; padding-top: 1; }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, client: SyncClient) -> None:
        super().__init__()
        self.client = client
        self._device_code: str | None = None
        self._cancelled = False

    def compose(self) -> ComposeResult:
        with Vertical(id="login-dialog"):
            yield Static("[b]Sign in with Google[/b]", classes="hint")
            yield Static("Starting…", id="login-status")
            yield Static("", id="login-code")
            with Vertical(classes="buttons"):
                yield Button("Cancel", id="login-cancel")

    def on_mount(self) -> None:
        self._begin()

    @work(exclusive=True)
    async def _begin(self) -> None:
        status = self.query_one("#login-status", Static)
        try:
            start = await self.client.start_device_login()
        except httpx.HTTPError:
            status.update("[red]Could not reach the server. Check the URL in Settings.[/red]")
            return
        self._device_code = start.device_code
        status.update(
            f"Open [u]{start.verification_uri}[/u] and enter this code:"
        )
        self.query_one("#login-code", Static).update(start.user_code)
        await self._poll(start.interval, start.expires_in)

    async def _poll(self, interval: int, expires_in: int) -> None:
        status = self.query_one("#login-status", Static)
        assert self._device_code is not None
        waited = 0
        while waited < expires_in and not self._cancelled:
            await asyncio.sleep(max(interval, 1))
            waited += max(interval, 1)
            if self._cancelled:
                break
            try:
                tokens = await self.client.poll_device_login(self._device_code)
            except AuthPending:
                continue
            except (AuthError, httpx.HTTPError):
                status.update("[red]Sign-in failed. Please try again.[/red]")
                return
            # Success — persist tokens and finish.
            s = self.app.settings  # type: ignore[attr-defined]
            s.access_token = tokens.access_token
            s.refresh_token = tokens.refresh_token
            s.user_email = tokens.email
            s.mode = "account"
            s.save()
            self.dismiss(True)
            return
        if not self._cancelled:
            status.update("[yellow]The code expired. Please try again.[/yellow]")

    @on(Button.Pressed, "#login-cancel")
    def action_cancel(self) -> None:
        self._cancelled = True
        self.dismiss(False)
