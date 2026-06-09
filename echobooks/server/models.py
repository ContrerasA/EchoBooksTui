"""Server-only models: the ``User`` table.

The catalog tables (book, author, …) are reused verbatim from
:mod:`echobooks.db.models` — importing them here registers them on the shared
``Base.metadata`` so ``create_all`` builds the whole schema on the server's
Postgres. The per-user scoping uses the nullable ``user_id`` column already on
``SyncMixin`` (NULL on the client, set to ``User.id`` here).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

# Importing the catalog models attaches them to Base.metadata (needed for
# create_all). They are otherwise used directly from echobooks.db.models.
from echobooks.db.models import Base  # noqa: F401  (re-exported for create_all)


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "user"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    google_sub: Mapped[str] = mapped_column(String, unique=True, index=True)
    email: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
