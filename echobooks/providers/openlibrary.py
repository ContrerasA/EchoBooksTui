"""Open Library provider — free, no API key, good for print & ebook metadata."""

from __future__ import annotations

import httpx

from echobooks.db.models import MediaType

from .base import BookDraft, BookHit

SEARCH_URL = "https://openlibrary.org/search.json"
COVER_URL = "https://covers.openlibrary.org/b/id/{cover_id}-L.jpg"
_FIELDS = ",".join(
    [
        "key",
        "title",
        "subtitle",
        "author_name",
        "first_publish_year",
        "cover_i",
        "number_of_pages_median",
        "language",
        "publisher",
    ]
)
_LANGS = {"eng": "English", "spa": "Spanish", "fre": "French", "ger": "German", "ita": "Italian"}


class OpenLibraryProvider:
    name = "openlibrary"

    def __init__(
        self, client: httpx.AsyncClient, default_media: MediaType = MediaType.PRINT
    ) -> None:
        self._client = client
        self._default_media = default_media

    async def search(self, query: str, limit: int = 10) -> list[BookHit]:
        resp = await self._client.get(
            SEARCH_URL,
            params={"q": query, "limit": limit, "fields": _FIELDS},
        )
        resp.raise_for_status()
        docs = resp.json().get("docs", [])
        return [self._to_hit(d) for d in docs if d.get("title")]

    def _to_hit(self, doc: dict) -> BookHit:
        cover_id = doc.get("cover_i")
        year = doc.get("first_publish_year")
        return BookHit(
            source=self.name,
            external_id=doc.get("key", ""),
            title=doc.get("title", ""),
            subtitle=doc.get("subtitle"),
            authors=doc.get("author_name", []) or [],
            year=str(year) if year else None,
            media_type=self._default_media,
            cover_url=COVER_URL.format(cover_id=cover_id) if cover_id else None,
        )

    async def fetch(self, hit: BookHit) -> BookDraft:
        draft = BookDraft.from_hit(hit)
        draft.media_type = self._default_media
        # The work record carries description + subjects (genres).
        if hit.external_id.startswith("/works/"):
            try:
                resp = await self._client.get(f"https://openlibrary.org{hit.external_id}.json")
                resp.raise_for_status()
                work = resp.json()
            except (httpx.HTTPError, ValueError):
                work = {}
            draft.description = _description(work.get("description"))
            subjects = work.get("subjects") or []
            draft.genres = [s for s in subjects[:6] if isinstance(s, str)]
        # Page count / language come from the search doc; re-query lightly if missing.
        if draft.page_count is None or draft.language is None:
            doc = await self._search_doc(hit)
            if doc:
                if draft.page_count is None:
                    draft.page_count = doc.get("number_of_pages_median")
                langs = doc.get("language") or []
                if draft.language is None and langs:
                    draft.language = _LANGS.get(langs[0], langs[0])
                pubs = doc.get("publisher") or []
                if not draft.publisher and pubs:
                    draft.publisher = pubs[0]
        return draft

    async def _search_doc(self, hit: BookHit) -> dict | None:
        try:
            resp = await self._client.get(
                SEARCH_URL,
                params={"q": hit.title, "limit": 5, "fields": _FIELDS},
            )
            resp.raise_for_status()
        except httpx.HTTPError:
            return None
        for doc in resp.json().get("docs", []):
            if doc.get("key") == hit.external_id:
                return doc
        return None


def _description(value: object) -> str | None:
    """Open Library descriptions are either a string or {"value": "..."}."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        v = value.get("value")
        return v if isinstance(v, str) else None
    return None
