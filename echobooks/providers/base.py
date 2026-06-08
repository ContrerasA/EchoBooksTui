"""Shared provider contracts and DTOs.

Providers turn a free-text query into lightweight :class:`BookHit` rows, then a
chosen hit into a fully-populated :class:`BookDraft` that the repository can
persist. Keeping these as plain pydantic models (no SQLAlchemy here) avoids any
import cycle with the db layer.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

from echobooks.db.models import MediaType


class BookHit(BaseModel):
    """A single search result — just enough to show in a results list."""

    source: str  # "openlibrary" | "audible" | ...
    external_id: str  # OLID / ISBN / ASIN
    title: str
    subtitle: str | None = None
    authors: list[str] = Field(default_factory=list)
    narrators: list[str] = Field(default_factory=list)
    year: str | None = None
    media_type: MediaType = MediaType.PRINT
    runtime_min: int | None = None
    cover_url: str | None = None
    # Series membership (audiobooks): series_id is the parent series ASIN.
    series_name: str | None = None
    series_position: str | None = None
    series_id: str | None = None

    @property
    def author_label(self) -> str:
        return ", ".join(self.authors) or "—"

    @property
    def has_series(self) -> bool:
        return bool(self.series_id)


class BookDraft(BaseModel):
    """Full metadata ready to become a Book row (all fields editable in the UI)."""

    title: str = ""
    subtitle: str | None = None
    authors: list[str] = Field(default_factory=list)
    narrators: list[str] = Field(default_factory=list)
    genres: list[str] = Field(default_factory=list)
    media_type: MediaType = MediaType.PRINT
    cover_url: str | None = None
    description: str | None = None
    page_count: int | None = None
    runtime_min: int | None = None
    language: str | None = None
    publisher: str | None = None
    published_date: str | None = None
    series_name: str | None = None
    series_position: str | None = None
    series_id: str | None = None  # parent series ASIN (Audnexus canonical)
    external_source: str | None = None
    external_id: str | None = None

    @classmethod
    def from_hit(cls, hit: BookHit) -> BookDraft:
        return cls(
            title=hit.title,
            subtitle=hit.subtitle,
            authors=list(hit.authors),
            narrators=list(hit.narrators),
            media_type=hit.media_type,
            cover_url=hit.cover_url,
            runtime_min=hit.runtime_min,
            published_date=hit.year,
            series_name=hit.series_name,
            series_position=hit.series_position,
            series_id=hit.series_id,
            external_source=hit.source,
            external_id=hit.external_id,
        )


class Provider(Protocol):
    """A metadata source. Implementations are async and network-bound."""

    name: str

    async def search(self, query: str, limit: int = 10) -> list[BookHit]: ...

    async def fetch(self, hit: BookHit) -> BookDraft: ...
