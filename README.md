# EchoBooks

A terminal (TUI) catalog for the books you've **read, are reading, or want to read** —
with first-class support for **audiobooks** (Audible / Graphic Audio) alongside print and
ebooks. Add a book and metadata auto-fills from public APIs; then slice your reading history
into stats: most-read author, hours listened, pages read, finishes per year, genres, and more.

Built with [Textual](https://textual.textualize.io/). Local-first — works fully offline, with
**optional** Google sign-in that syncs your catalog to your own self-hosted server (see
**Accounts & sync**). The offline client installs with no server dependencies.

## Quick start

```bash
uv sync          # install dependencies
uv run echobooks # launch the app
```

(Install `uv` first if needed: `curl -LsSf https://astral.sh/uv/install.sh | sh`.)

Your catalog lives in a local SQLite database under your platform data dir
(e.g. `~/.local/share/echobooks/echobooks.db`). Nothing leaves your machine except
metadata lookups to the book APIs — unless you opt into **Accounts & sync** (below).

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
  app.py            EchoBooksApp shell + theme + CSS; owns SyncClient, sync-on-launch
  config.py         XDG paths + persisted settings (incl. account tokens)
  db/
    models.py       SQLAlchemy models (UUID PKs, soft-delete + dirty + user_id for sync)
    session.py      engine / Session factory / schema creation + user_id migration
    repository.py   all reads, writes, and stats queries (the data API)
  providers/        openlibrary / audible / audnexus + registry (metadata lookup)
  sync/             client-side sync (no server deps)
    serialize.py    wire DTOs + last-write-wins merge (shared with the server)
    engine.py       push dirty rows → pull → merge; import_local for first login
    client.py       SyncClient: httpx wrapper for /auth/* + /sync/*
  server/           OPTIONAL self-hosted API (pip install echobooks[server])
    app.py          FastAPI factory          auth.py   Google device flow + JWT
    sync.py         /sync/push + /sync/pull   db.py     Postgres engine / session
    models.py       User table                config.py env-driven settings
  screens/          library, add_book, book_form, book_detail, session_edit, stats,
                    settings, login (device flow), import_picker
  util.py           runtime/date/rating parsing helpers
tests/              providers, repository + stats, sync engine + LWW, server auth + sync,
                    headless app + login-flow smoke tests, no-server-deps guard
```

**Reading sessions** are the source of truth for "read" stats: marking a book *Read* guarantees
one finished session, and re-reads/re-listens add more — so hours, pages, and finish counts all
include re-reads and stay internally consistent.

## Accounts & sync

Sign-in is entirely optional — the app works fully offline without it. If you run your own
EchoBooks server, you can sign in with Google to sync your catalog across machines.

- **Sign in**: Settings (`Ctrl+S`) → enter your server URL → **Sign in with Google**. Because a
  terminal can't host a browser redirect, login uses the OAuth **device flow**: the app shows a
  short code and a URL; open it on any device, approve, and the app picks up from there. Your
  server holds the Google secret — the app only ever sees a token your server issues.
- **Importing your existing books**: right after your first sign-in, EchoBooks asks which of your
  local titles to upload to the account — pick exactly the ones you want (Space toggles).
- **A second machine with its own books**: when you sign in on another computer that already has
  offline data, you get the same picker for *that* device's titles, so you choose what to push up.
  Everything then merges by **last-write-wins** (the most recently edited copy of each book wins),
  and the merged catalog syncs back down.
- **Sync on launch / on demand**: a signed-in app syncs in the background at startup; **Sync now**
  in Settings runs it manually. **Sign out** drops your tokens but keeps the local catalog intact.
- **Token storage**: your account tokens are kept in the OS keyring (Keychain / Secret Service /
  Credential Manager), not in `settings.json`. On machines with no keyring (headless servers,
  some containers) they fall back to `settings.json`; set `ECHOBOOKS_NO_KEYRING=1` to force that.
  Existing plaintext tokens migrate into the keyring automatically on the next launch.

## Running your own server

The server is optional and ships behind an extra, so the plain client stays dependency-light:

```bash
pip install echobooks            # offline client only
pip install "echobooks[server]"  # adds the FastAPI sync server (or: uv sync --all-extras)
```

**1. Create a Google OAuth client.** In the [Google Cloud Console](https://console.cloud.google.com/)
→ APIs & Services → Credentials → *Create credentials* → *OAuth client ID*, choose application
type **TV and Limited Input devices** (this is what enables the device flow). Note the client id
and secret.

**2. Configure the server** via env (a `.env` file works; it's gitignored):

```bash
DATABASE_URL=postgresql+psycopg://echobooks@localhost/echobooks
GOOGLE_CLIENT_ID=...apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=...
JWT_SECRET=$(openssl rand -hex 32)   # any long random string
# optional: HOST, PORT, JWT_ACCESS_TTL, JWT_REFRESH_TTL
```

**3. Run it:**

```bash
echobooks-server     # serves the API (creates tables on first run)
```

Put it behind your Cloudflare tunnel (or any HTTPS reverse proxy) and point the app's **Server
URL** at that public address. Endpoints: `POST /auth/device/start`, `POST /auth/device/poll`,
`POST /auth/refresh`, `POST /sync/push`, `GET /sync/pull`, `GET /health`.

### Deploying declaratively on NixOS (flake)

The repo ships a flake that builds the app from `uv.lock` with [uv2nix](https://pyproject-nix.github.io/uv2nix/)
(no runtime `uv sync`, no `nix-ld` — C-extension deps come from wheels) and a NixOS module
that runs the server as a hardened systemd service and provisions Postgres.

```nix
# flake.nix (your host)
{
  inputs.echobooks.url = "github:ContrerasA/EchoBooksTui";   # or git+file:// for a local checkout
  outputs = { self, nixpkgs, echobooks }: {
    nixosConfigurations.myhost = nixpkgs.lib.nixosSystem {
      modules = [
        echobooks.nixosModules.default
        {
          services.echobooks = {
            enable = true;
            # host/port default to 127.0.0.1:8000 (sit behind your TLS proxy)
            # secrets live OUTSIDE the Nix store:
            environmentFile = "/var/lib/echobooks/.env";  # GOOGLE_CLIENT_ID/SECRET, JWT_SECRET
          };
        }
      ];
    };
  };
}
```

`nixos-rebuild switch` then builds the pinned server, creates the `echobooks` Postgres role +
database (unix-socket peer auth), and starts `echobooks.service`. Redeploys are a rebuild —
no `git pull` + `uv sync` on the box. Options: `services.echobooks.{package,user,stateDir,host,
port,database,databaseUrl,environmentFile,provisionDatabase}` (see `nix/module.nix`).

You can also just build/run the package directly: `nix build github:ContrerasA/EchoBooksTui`
(produces an env with `echobooks` + `echobooks-server`), or `nix run …#server`.

## Development

```bash
uv sync --all-extras   # client + server + dev tools
uv run pytest          # tests (offline; network + Google are mocked)
uv run ruff check .    # lint
uv run mypy echobooks  # types

# Nix users: a devShell with the full env is available
nix develop            # or `nix flake check` to validate the flake
```

## Roadmap

- **Phase 2 — Accounts + sync** *(done)*: optional Google device-flow login, server-issued JWTs,
  FastAPI + Postgres server (reusing the SQLAlchemy models), `/sync/push` + `/sync/pull` with
  last-write-wins, and an import picker for first-login / multi-device.
- **Phase 3 — Hardening / deploy** *(done)*: client tokens moved to the OS keyring; a `flake.nix`
  (uv2nix build) + NixOS module make the home-server deploy fully declarative.
