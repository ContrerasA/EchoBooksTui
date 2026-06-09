"""Server configuration, loaded from environment / a local ``.env`` file.

Nothing here ships in the offline client. The Google OAuth *client secret* lives
only on the server — the TUI never sees it (that's the point of the device flow).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class ServerSettings:
    database_url: str
    google_client_id: str
    google_client_secret: str
    jwt_secret: str
    jwt_access_ttl: int = 3600  # seconds (1 hour)
    jwt_refresh_ttl: int = 60 * 60 * 24 * 30  # 30 days
    host: str = "0.0.0.0"
    port: int = 8000

    @property
    def configured(self) -> bool:
        """Whether Google OAuth + a real JWT secret are present."""
        return bool(self.google_client_id and self.google_client_secret and self.jwt_secret)


def _load_dotenv() -> None:
    """Best-effort .env loader (python-dotenv ships with the server extra)."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ModuleNotFoundError:  # pragma: no cover - dev convenience only
        pass


@lru_cache
def get_settings() -> ServerSettings:
    _load_dotenv()
    return ServerSettings(
        database_url=os.environ.get(
            "DATABASE_URL", "postgresql+psycopg://echobooks@localhost/echobooks"
        ),
        google_client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
        google_client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        jwt_secret=os.environ.get("JWT_SECRET", ""),
        jwt_access_ttl=int(os.environ.get("JWT_ACCESS_TTL", "3600")),
        jwt_refresh_ttl=int(os.environ.get("JWT_REFRESH_TTL", str(60 * 60 * 24 * 30))),
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
    )
