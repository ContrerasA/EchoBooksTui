"""Audnexus enrichment — clean audiobook metadata by ASIN (runtime, narrator, synopsis)."""

from __future__ import annotations

import re

import httpx

from echobooks.db.models import MediaType

from .base import BookDraft, BookHit

BOOK_URL = "https://api.audnex.us/books/{asin}"
_TAG_RE = re.compile(r"<[^>]+>")


class AudnexusProvider:
    name = "audnexus"

    def __init__(self, client: httpx.AsyncClient, region: str = "us") -> None:
        self._client = client
        self._region = region

    async def fetch_asin(self, asin: str) -> BookDraft | None:
        try:
            resp = await self._client.get(
                BOOK_URL.format(asin=asin), params={"region": self._region}
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            return None
        return self._to_draft(data)

    async def fetch(self, hit: BookHit) -> BookDraft:
        """Enrich a hit by ASIN; fall back to the hit's own data on failure."""
        draft = await self.fetch_asin(hit.external_id)
        if draft is None:
            draft = BookDraft.from_hit(hit)
            draft.media_type = MediaType.AUDIOBOOK
        return draft

    def _to_draft(self, d: dict) -> BookDraft:
        series = d.get("seriesPrimary") or {}
        return BookDraft(
            title=d.get("title", ""),
            subtitle=d.get("subtitle"),
            authors=[a["name"] for a in d.get("authors", []) if a.get("name")],
            narrators=[n["name"] for n in d.get("narrators", []) if n.get("name")],
            genres=[g["name"] for g in d.get("genres", []) if g.get("name")],
            media_type=MediaType.AUDIOBOOK,
            cover_url=d.get("image"),
            description=_strip_html(d.get("summary")),
            runtime_min=d.get("runtimeLengthMin"),
            language=_titlecase(d.get("language")),
            publisher=d.get("publisherName"),
            published_date=(d.get("releaseDate") or "")[:10] or None,
            series_name=series.get("name"),
            series_position=series.get("position"),
            series_id=series.get("asin"),
            external_source="audible",
            external_id=d.get("asin"),
        )


def _strip_html(text: str | None) -> str | None:
    if not text:
        return None
    return _TAG_RE.sub("", text).strip() or None


def _titlecase(text: str | None) -> str | None:
    return text.title() if text else None
