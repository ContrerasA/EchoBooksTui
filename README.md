# EchoBooks

A terminal (TUI) catalog for the books you've **read, are reading, or want to read** —
with first-class support for **audiobooks** (Audible / Graphic Audio) alongside print and
ebooks. Add a book and metadata auto-fills from public APIs; then slice your reading history
into stats: most-read author, hours listened, pages read, finishes per year, genres, and more.

Built with [Textual](https://textual.textualize.io/). Local-first — works fully offline today;
accounts + auto-sync to a self-hosted server are planned (see **Roadmap**).

## Quick start

```bash
uv sync          # install dependencies
uv run echobooks # launch the app
```

(Install `uv` first if needed: `curl -LsSf https://astral.sh/uv/install.sh | sh`.)

Your catalog lives in a local SQLite database under your platform data dir
(e.g. `~/.local/share/echobooks/echobooks.db`). Nothing leaves your machine except
metadata lookups to the book APIs.

## Using it

- **Library** (home): browse/search/sort your catalog. `a` add · `Enter` open · `s` stats ·
  `Ctrl+S` settings · `/` search · `q` quit.
- **Add a book**: pick a media type, search a provider, choose a result (or `Ctrl+M` for manual
  entry), then review/edit the prefilled form and save. Adding it as *Read* records a finished
  reading session automatically.
- **Book detail**: full metadata + your reading sessions. `e` edit · `n` add session (re-read /
  re-listen) · `f` favorite · change status inline.
- **Stats**: headline numbers plus charts (finishes by year, most-read authors) and top
  narrators / genres / ratings.

## Where metadata comes from

| Media | Provider | Notes |
|---|---|---|
| Print / Ebook | [Open Library](https://openlibrary.org/) | Free, no key. Title, author, cover, page count, description. |
| Audiobook | Audible catalog → [Audnexus](https://audnex.us/) | Title search finds the ASIN; Audnexus enriches with runtime, narrator, synopsis, genres. |
| Anything else | Manual entry | Editable form; runtime accepts `16:10`, `970`, or `16h 10m`. |

## Architecture

```
echobooks/
  app.py            EchoBooksApp shell + theme + CSS
  config.py         XDG paths + persisted settings
  db/
    models.py       SQLAlchemy models (UUID PKs, soft-delete + dirty flags for future sync)
    session.py      engine / Session factory / schema creation
    repository.py   all reads, writes, and stats queries (the data API)
  providers/
    base.py         BookHit / BookDraft DTOs + Provider protocol
    openlibrary.py  print/ebook search + enrich
    audible.py      audiobook catalog keyword search (ASIN + runtime)
    audnexus.py     audiobook enrich by ASIN
    registry.py     routes by media type; caches results in the DB
  screens/          library, add_book, book_form, book_detail, session_edit, stats, settings
  util.py           runtime/date/rating parsing helpers
tests/              providers (mocked httpx), repository + stats, headless app smoke test
```

**Reading sessions** are the source of truth for "read" stats: marking a book *Read* guarantees
one finished session, and re-reads/re-listens add more — so hours, pages, and finish counts all
include re-reads and stay internally consistent.

## Development

```bash
uv run pytest        # tests (offline; network is mocked)
uv run ruff check .  # lint
uv run mypy echobooks
```

## Roadmap

The data model already carries everything sync needs (client-generated UUID keys, `updated_at`,
soft `deleted_at`, and a `dirty` flag), so these phases bolt on without a rewrite:

- **Phase 2 — Server**: FastAPI + Postgres (reusing the SQLAlchemy models); register/login
  (argon2 + JWT).
- **Phase 3 — Sync**: `/sync/push` + `/sync/pull` with last-write-wins; Settings toggles
  offline ↔ account and syncs on launch.
- **Phase 4 — Deploy**: `flake.nix` + a NixOS service for the home server.
