"""Headless TUI test of the account flow: settings → import picker → sync.

The server is mocked with respx (the SyncClient's HTTP calls are intercepted), so
this drives the real screens and the real sync engine without a live server.
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest
import respx

from echobooks.db import session as dbsession


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHOBOOKS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ECHOBOOKS_CONFIG_DIR", str(tmp_path))
    dbsession._engine = None
    dbsession._Session = None
    dbsession.init_db()
    yield
    dbsession._engine = None
    dbsession._Session = None


def _seed_book() -> str:
    from echobooks.db import repository as repo
    from echobooks.db.models import MediaType, Status
    from echobooks.db.session import session_scope
    from echobooks.providers.base import BookDraft

    with session_scope() as s:
        book = repo.create_book(
            s,
            BookDraft(title="Project Hail Mary", authors=["Andy Weir"],
                      media_type=MediaType.PRINT, page_count=400),
            status=Status.READ,
            finished_on=date(2026, 1, 1),
            rating=5.0,
        )
        return book.id


@respx.mock
async def test_import_picker_uploads_selected(app_env):
    book_id = _seed_book()

    # Capture what the client pushes; pull returns nothing new.
    pushed_payloads: list[dict] = []

    def _push(request: httpx.Request) -> httpx.Response:
        import json

        pushed_payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"rows": []})

    respx.post("https://srv.test/sync/push").mock(side_effect=_push)
    respx.get("https://srv.test/sync/pull").mock(
        return_value=httpx.Response(200, json={"rows": []})
    )

    from echobooks.app import EchoBooksApp

    app = EchoBooksApp()
    # Put the app in a logged-in state so we exercise import + sync directly.
    app.settings.server_url = "https://srv.test"
    app.settings.access_token = "test-access"
    app.settings.refresh_token = "test-refresh"
    app.settings.user_email = "reader@example.com"
    app.settings.mode = "account"

    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        from echobooks.screens.import_picker import ImportPickerScreen
        from echobooks.screens.settings import SettingsScreen

        settings_screen = SettingsScreen()
        await app.push_screen(settings_screen)
        await pilot.pause()

        # Run the import for our seeded book, then let the workers finish.
        settings_screen._run_import_and_sync([book_id])
        for _ in range(6):
            await pilot.pause()

    # The chosen book was pushed to the server.
    assert pushed_payloads, "expected a push to the server"
    all_rows = [r for payload in pushed_payloads for r in payload["rows"]]
    book_titles = {r["fields"]["title"] for r in all_rows if r["table"] == "book"}
    assert "Project Hail Mary" in book_titles

    # ImportPickerScreen builds and lists the local catalog.
    picker = ImportPickerScreen()
    assert picker.prompt


@respx.mock
async def test_sign_out_keeps_local_data(app_env):
    _seed_book()
    from echobooks.app import EchoBooksApp

    app = EchoBooksApp()
    app.settings.server_url = "https://srv.test"
    app.settings.access_token = "tok"
    app.settings.user_email = "x@example.com"

    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        from echobooks.screens.settings import SettingsScreen

        screen = SettingsScreen()
        await app.push_screen(screen)
        await pilot.pause()
        screen._sign_out()
        await pilot.pause()

    assert not app.settings.is_logged_in()
    # Local catalog survives sign-out.
    from echobooks.db.repository import list_books
    from echobooks.db.session import session_scope

    with session_scope() as s:
        assert len(list_books(s)) == 1
