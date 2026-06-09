"""Pick which volumes of a detected series to add in one go."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, Select, SelectionList, Static
from textual.widgets.selection_list import Selection

from echobooks.db.models import Status
from echobooks.db.repository import create_book
from echobooks.db.session import session_scope
from echobooks.providers.base import BookDraft
from echobooks.util import format_runtime

_STATUS_OPTIONS = [(s.label, s.value) for s in Status]


class SeriesSelectScreen(Screen[bool]):
    """Shown when the picked audiobook belongs to a series with siblings."""

    BINDINGS = [
        Binding("ctrl+s", "add_selected", "Add selected"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, drafts: list[BookDraft], picked: BookDraft, status: Status) -> None:
        super().__init__()
        self.drafts = drafts
        self.picked = picked
        self.status = status

    def compose(self) -> ComposeResult:
        series_name = self.picked.series_name or self.drafts[0].series_name or "this series"
        yield Header()
        with VerticalScroll(classes="panel"):
            yield Static(
                f"[b]{series_name}[/b] — {len(self.drafts)} volumes found. "
                "Pick which to add (Space toggles).",
                classes="hint",
            )
            selections = [
                Selection(_label(d), i, initial_state=True) for i, d in enumerate(self.drafts)
            ]
            yield SelectionList[int](*selections, id="vols")
            with Horizontal(classes="actions"):
                yield Button("Add selected", variant="success", id="add")
                yield Button("Just the one I picked", id="single")
                yield Button("Cancel", id="cancel")
            with Vertical():
                yield Label("Status for added books")
                yield Select(
                    _STATUS_OPTIONS, value=self.status.value, allow_blank=False, id="status"
                )
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = "series"
        self.query_one("#vols", SelectionList).focus()

    @on(Button.Pressed, "#add")
    def action_add_selected(self) -> None:
        indices = sorted(self.query_one("#vols", SelectionList).selected)
        if not indices:
            self.app.notify("Nothing selected", severity="warning")
            return
        status = Status(str(self.query_one("#status", Select).value))
        self._persist_status(status)
        created: dict[int, str] = {}
        with session_scope() as session:
            for i in indices:
                created[i] = create_book(session, self.drafts[i], status=status).id
        # Select the volume the user originally picked if it was added, else the
        # first of the batch, so the library lands on a sensible row.
        focus_id = next(
            (created[i] for i in indices if self.drafts[i] is self.picked),
            created[indices[0]],
        )
        self.app.pending_focus_book_id = focus_id  # type: ignore[attr-defined]
        self.app.notify(f"Added {len(indices)} books")
        self.app.schedule_sync()  # type: ignore[attr-defined]
        self.dismiss(True)

    @on(Button.Pressed, "#single")
    def _single(self) -> None:
        from echobooks.screens.book_form import BookFormScreen

        status = Status(str(self.query_one("#status", Select).value))
        self._persist_status(status)

        def _after(saved: bool | None) -> None:
            if saved:
                self.dismiss(True)

        self.app.push_screen(BookFormScreen(self.picked, status=status), _after)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(False)

    def _persist_status(self, status: Status) -> None:
        settings = self.app.settings  # type: ignore[attr-defined]
        settings.last_status = status.value
        settings.save()


def _label(d: BookDraft) -> str:
    pos = f"#{d.series_position}  " if d.series_position else ""
    rt = f"  ·  {format_runtime(d.runtime_min)}" if d.runtime_min else ""
    return f"{pos}{d.title}{rt}"
