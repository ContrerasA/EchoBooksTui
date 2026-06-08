"""Headless smoke test driving the real Textual app via the pilot."""

from __future__ import annotations

from datetime import date

import pytest

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


async def test_app_flow(app_env):
    from echobooks.db import repository as repo
    from echobooks.db.models import MediaType, Status
    from echobooks.db.session import session_scope
    from echobooks.providers.base import BookDraft

    with session_scope() as s:
        repo.create_book(
            s,
            BookDraft(
                title="Project Hail Mary",
                authors=["Andy Weir"],
                media_type=MediaType.AUDIOBOOK,
                runtime_min=970,
            ),
            status=Status.READ,
            finished_on=date(2026, 1, 1),
            rating=5.0,
        )

    from echobooks.app import EchoBooksApp

    app = EchoBooksApp()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        assert app.screen.query_one("#books").row_count == 1

        # Open detail, add a re-listen session.
        await pilot.press("enter")
        await pilot.pause()
        assert type(app.screen).__name__ == "BookDetailScreen"
        await pilot.press("n")
        await pilot.pause()
        app.screen.query_one("#s-finished").value = "2026-06-01"
        app.screen.query_one("#s-rating").value = "4.5"
        await pilot.press("ctrl+s")
        await pilot.pause()
        await pilot.pause()
        assert app.screen.query_one("#sessions").row_count == 2

        # Back to library, add a manual book.
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        await pilot.press("ctrl+m")
        await pilot.pause()
        app.screen.query_one("#f-title").value = "Manual Book"
        await pilot.press("ctrl+s")
        await pilot.pause()
        await pilot.pause()
        assert app.screen.query_one("#books").row_count == 2

        # Stats dashboard builds without error.
        await pilot.press("s")
        await pilot.pause()
        assert type(app.screen).__name__ == "StatsScreen"
