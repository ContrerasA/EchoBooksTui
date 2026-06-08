"""Add a book: choose media type, search a provider, pick a result (or go manual)."""

from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, OptionList, Select, Static
from textual.widgets.option_list import Option

from echobooks.db.models import MediaType, Status
from echobooks.providers.base import BookDraft, BookHit
from echobooks.util import format_runtime

_MEDIA_OPTIONS = [(m.label, m.value) for m in MediaType]
_STATUS_OPTIONS = [(s.label, s.value) for s in Status]


class AddBookScreen(Screen[None]):
    BINDINGS = [
        Binding("escape", "cancel", "Back"),
        Binding("ctrl+m", "manual", "Manual entry"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._hits: list[BookHit] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(classes="panel"):
            yield Static(
                "[b]Add a book[/b] — search a provider or enter it manually", classes="hint"
            )
            settings = self.app.settings  # type: ignore[attr-defined]
            with Horizontal(classes="toolbar"):
                yield Select(
                    _MEDIA_OPTIONS, value=settings.last_media, allow_blank=False, id="media"
                )
                yield Select(
                    _STATUS_OPTIONS, value=settings.last_status, allow_blank=False, id="status"
                )
            with Horizontal(classes="toolbar"):
                yield Input(placeholder="Title / author…", id="query")
                yield Button("Search", variant="primary", id="search-btn")
                yield Button("Manual", id="manual-btn")
            yield Static("", id="search-status", classes="hint")
            yield OptionList(id="results")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#query", Input).focus()

    @on(Select.Changed, "#media")
    @on(Select.Changed, "#status")
    def _remember_choices(self) -> None:
        settings = self.app.settings  # type: ignore[attr-defined]
        settings.last_media = str(self.query_one("#media", Select).value)
        settings.last_status = str(self.query_one("#status", Select).value)
        settings.save()

    # -- search ----------------------------------------------------------- #
    @on(Button.Pressed, "#search-btn")
    @on(Input.Submitted, "#query")
    def _do_search(self) -> None:
        query = self.query_one("#query", Input).value.strip()
        if not query:
            return
        media = MediaType(str(self.query_one("#media", Select).value))
        self.query_one("#search-status", Static).update("Searching…")
        self.query_one("#results", OptionList).clear_options()
        self._search_worker(query, media)

    @work(exclusive=True)
    async def _search_worker(self, query: str, media: MediaType) -> None:
        hits = await self.app.registry.search(query, media)  # type: ignore[attr-defined]
        self._hits = hits
        results = self.query_one("#results", OptionList)
        results.clear_options()
        if not hits:
            self.query_one("#search-status", Static).update(
                "No results — try 'Manual' (Ctrl+M) to enter it by hand."
            )
            return
        self.query_one("#search-status", Static).update(f"{len(hits)} results — Enter to pick")
        for hit in hits:
            results.add_option(Option(_format_hit(hit)))
        results.focus()

    @on(OptionList.OptionSelected, "#results")
    def _pick(self, event: OptionList.OptionSelected) -> None:
        hit = self._hits[event.option_index]
        media = MediaType(str(self.query_one("#media", Select).value))
        self.query_one("#search-status", Static).update(f"Fetching “{hit.title}”…")
        self._fetch_worker(hit, media)

    @work(exclusive=True)
    async def _fetch_worker(self, hit: BookHit, media: MediaType) -> None:
        registry = self.app.registry  # type: ignore[attr-defined]
        draft = await registry.fetch(hit, media)
        if media == MediaType.AUDIOBOOK and (draft.series_id or hit.has_series):
            self.query_one("#search-status", Static).update(
                f"Found series “{draft.series_name or hit.series_name}” — loading volumes…"
            )
            volumes = await registry.fetch_series(hit, draft)
            if len(volumes) > 1:
                self._open_series(volumes, draft)
                return
        self._open_form(draft, media)

    def _open_series(self, volumes: list[BookDraft], picked: BookDraft) -> None:
        from echobooks.screens.series_select import SeriesSelectScreen

        status = Status(str(self.query_one("#status", Select).value))

        def _after(saved: bool | None) -> None:
            if saved:
                self.dismiss()

        self.app.push_screen(SeriesSelectScreen(volumes, picked, status), _after)

    # -- manual ----------------------------------------------------------- #
    @on(Button.Pressed, "#manual-btn")
    def action_manual(self) -> None:
        media = MediaType(str(self.query_one("#media", Select).value))
        self._open_form(BookDraft(media_type=media, external_source="manual"), media)

    def _open_form(self, draft: BookDraft, media: MediaType) -> None:
        from echobooks.screens.book_form import BookFormScreen

        draft.media_type = media
        status = Status(str(self.query_one("#status", Select).value))

        def _after(saved: bool | None) -> None:
            if saved:
                self.dismiss()

        self.app.push_screen(BookFormScreen(draft, status=status), _after)

    def action_cancel(self) -> None:
        self.dismiss()


def _format_hit(hit: BookHit) -> str:
    bits = [f"[b]{hit.title}[/b]"]
    bits.append(hit.author_label)
    if hit.narrators:
        bits.append(f"narr. {', '.join(hit.narrators)}")
    if hit.runtime_min:
        bits.append(format_runtime(hit.runtime_min))
    if hit.year:
        bits.append(hit.year)
    return "  ·  ".join(bits)
