"""Add or edit a single reading session (a read or re-read / re-listen)."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Input, Label, Select, Static, TextArea

from echobooks.db.models import MediaType, ReadingSession
from echobooks.db.repository import add_session, get_book
from echobooks.db.session import session_scope
from echobooks.util import parse_date, parse_rating, stars

_MEDIA_OPTIONS = [("Same as book", "")] + [(m.label, m.value) for m in MediaType]


class SessionEditScreen(Screen[bool]):
    BINDINGS = [
        Binding("ctrl+s", "save", "Save"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, book_id: str, session_id: str | None = None) -> None:
        super().__init__()
        self.book_id = book_id
        self.session_id = session_id

    def compose(self) -> ComposeResult:
        started = finished = rating = review = ""
        media = ""
        if self.session_id:
            with session_scope() as s:
                rs = s.get(ReadingSession, self.session_id)
                if rs:
                    started = rs.started_on.isoformat() if rs.started_on else ""
                    finished = rs.finished_on.isoformat() if rs.finished_on else ""
                    rating = str(rs.rating) if rs.rating is not None else ""
                    review = rs.review or ""
                    media = rs.media_type.value if rs.media_type else ""

        title = "Edit session" if self.session_id else "New reading session"
        with VerticalScroll(classes="panel"):
            yield Static(f"[b]{title}[/b]", classes="hint")
            yield Label("Started on (YYYY-MM-DD)")
            yield Input(started, id="s-started")
            yield Label("Finished on (YYYY-MM-DD)")
            yield Input(finished, id="s-finished")
            yield Label("Rating (0.5–5)")
            yield Input(rating, id="s-rating")
            yield Label("Media (override)")
            yield Select(_MEDIA_OPTIONS, value=media, allow_blank=False, id="s-media")
            yield Label("Review / notes")
            yield TextArea(review, id="s-review")
            with Horizontal(classes="actions"):
                yield Button("Save", variant="success", id="save")
                if self.session_id:
                    yield Button("Delete", variant="error", id="delete")
                yield Button("Cancel", id="cancel")

    @on(Button.Pressed, "#save")
    def action_save(self) -> None:
        started = parse_date(self.query_one("#s-started", Input).value)
        finished = parse_date(self.query_one("#s-finished", Input).value)
        rating = parse_rating(self.query_one("#s-rating", Input).value)
        media_val = str(self.query_one("#s-media", Select).value)
        media = MediaType(media_val) if media_val else None
        review = self.query_one("#s-review", TextArea).text.strip() or None

        with session_scope() as session:
            if self.session_id:
                rs = session.get(ReadingSession, self.session_id)
                if rs:
                    rs.started_on = started
                    rs.finished_on = finished
                    rs.rating = rating
                    rs.media_type = media
                    rs.review = review
                    rs.dirty = True
            else:
                book = get_book(session, self.book_id)
                if book:
                    add_session(
                        session,
                        book,
                        started_on=started,
                        finished_on=finished,
                        rating=rating,
                        review=review,
                        media_type=media,
                    )
        self.app.notify(f"Session saved {stars(rating)}".strip())
        self.dismiss(True)

    @on(Button.Pressed, "#delete")
    def _delete(self) -> None:
        with session_scope() as session:
            rs = session.get(ReadingSession, self.session_id)
            if rs:
                session.delete(rs)
        self.app.notify("Session deleted")
        self.dismiss(True)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(False)
