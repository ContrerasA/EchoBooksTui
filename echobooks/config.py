"""Runtime configuration and on-disk locations for EchoBooks.

Everything local-first: a SQLite database and a small JSON settings file live
under the platform's XDG data/config directories. The sync-related fields are
present now but unused until the account/sync phase.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

APP_NAME = "echobooks"


def data_dir() -> Path:
    """Directory holding the SQLite database (override with ECHOBOOKS_DATA_DIR)."""
    override = os.environ.get("ECHOBOOKS_DATA_DIR")
    path = Path(override) if override else Path(user_data_dir(APP_NAME))
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_dir() -> Path:
    """Directory holding settings.json (override with ECHOBOOKS_CONFIG_DIR)."""
    override = os.environ.get("ECHOBOOKS_CONFIG_DIR")
    path = Path(override) if override else Path(user_config_dir(APP_NAME))
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "echobooks.db"


def database_url() -> str:
    return f"sqlite:///{db_path()}"


@dataclass
class Settings:
    """User-tunable settings, persisted to settings.json."""

    # Which providers to consult. Manual entry is always available.
    use_openlibrary: bool = True
    use_audible: bool = True
    audible_region: str = "us"

    # Remembered choices from the last "Add book" so they carry over.
    last_media: str = "AUDIOBOOK"
    last_status: str = "WANT"

    # Account/sync.
    mode: str = "offline"  # "offline" | "account"
    server_url: str = ""
    username: str = ""

    # Auth tokens issued by the EchoBooks server after Google sign-in. Persisted
    # to settings.json — fine for a local CLI; a later hardening pass can move
    # these to the OS keyring.
    access_token: str = ""
    refresh_token: str = ""
    user_email: str = ""
    last_sync: str = ""  # ISO timestamp of the last successful pull

    # Non-persisted convenience fields.
    extras: dict = field(default_factory=dict)

    def is_logged_in(self) -> bool:
        return bool(self.access_token and self.server_url)

    def clear_account(self) -> None:
        """Sign out: drop tokens but keep the local catalog (offline resumes)."""
        self.access_token = ""
        self.refresh_token = ""
        self.user_email = ""
        self.last_sync = ""
        self.mode = "offline"
        self.save()

    @classmethod
    def path(cls) -> Path:
        return config_dir() / "settings.json"

    @classmethod
    def load(cls) -> Settings:
        p = cls.path()
        if not p.exists():
            return cls()
        try:
            raw = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return cls()
        known = {k: raw[k] for k in raw if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self) -> None:
        self.path().write_text(json.dumps(asdict(self), indent=2))
