"""Stats dashboard: headline numbers plus lightweight text bar charts.

We render bars as text (block glyphs in a Static) rather than using a plotting
widget — that avoids a resize feedback loop and is instant regardless of size.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from echobooks.db import repository as repo
from echobooks.db.session import session_scope
from echobooks.util import stars

_ACCENT = "#F54257"
_BAR_WIDTH = 32


class StatsScreen(Screen[None]):
    BINDINGS = [Binding("escape", "back", "Back")]

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            with Container(classes="stat-grid", id="cards"):
                yield Static(id="card-books", classes="stat-card")
                yield Static(id="card-finishes", classes="stat-card")
                yield Static(id="card-hours", classes="stat-card")
                yield Static(id="card-pages", classes="stat-card")
            yield Static(id="chart-years", classes="chart")
            yield Static(id="chart-authors", classes="chart")
            yield Static(id="lists", classes="panel")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = "statistics"
        with session_scope() as session:
            t = repo.totals(session)
            years = repo.finishes_by_year(session)
            authors = repo.top_authors(session, limit=10)
            narrators = repo.top_narrators(session, limit=5)
            genres = repo.genre_breakdown(session, limit=8)
            ratings = repo.rating_distribution(session)
            media = repo.media_breakdown(session)

        self._card("card-books", str(t.books), "in library")
        self._card("card-finishes", str(t.finishes), "finishes")
        self._card("card-hours", f"{t.hours_listened:g}", "hours listened")
        self._card("card-pages", f"{t.pages_read:,}", "pages read")

        year_rows = [(str(y), c) for y, c in years]
        self.query_one("#chart-years", Static).update(
            "[b]Finishes by year[/b]\n" + _bar_chart(year_rows)
        )
        self.query_one("#chart-authors", Static).update(
            "[b]Most-read authors[/b]\n" + _bar_chart(authors)
        )
        self.query_one("#lists", Static).update(
            _render_lists(narrators, genres, ratings, media)
        )

    def _card(self, wid: str, number: str, label: str) -> None:
        self.query_one(f"#{wid}", Static).update(f"[b]{number}[/b]\n[dim]{label}[/dim]")

    def action_back(self) -> None:
        self.dismiss()


def _bar_chart(rows: list[tuple[str, int]], width: int = _BAR_WIDTH) -> str:
    if not rows:
        return "[dim]No finishes yet — mark some books read.[/dim]"
    top = max(v for _, v in rows) or 1
    label_w = min(22, max(len(str(label)) for label, _ in rows))
    lines = []
    for label, value in rows:
        filled = max(1, round(value / top * width)) if value else 0
        bar = "█" * filled
        text = str(label)
        if len(text) > label_w:
            text = text[: label_w - 1] + "…"
        lines.append(f"{text:<{label_w}}  [{_ACCENT}]{bar}[/] {value}")
    return "\n".join(lines)


def _render_lists(narrators, genres, ratings, media) -> str:
    out: list[str] = []
    if narrators:
        out.append("[b]Top narrators[/b]")
        out += [f"  {n} — {c}" for n, c in narrators]
        out.append("")
    if genres:
        out.append("[b]Top genres[/b]")
        out += [f"  {g} — {c}" for g, c in genres]
        out.append("")
    if ratings:
        out.append("[b]Ratings[/b]")
        out += [f"  {stars(r)} — {c}" for r, c in ratings]
        out.append("")
    if media:
        out.append("[b]Library by media[/b]")
        out += [f"  {m.label} — {c}" for m, c in media]
    if not out:
        return "No data yet — add some books and mark them read."
    return "\n".join(out)
