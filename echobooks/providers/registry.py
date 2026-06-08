"""Routes searches/fetches to the right provider(s) and caches results.

- AUDIOBOOK queries -> Audible catalog search; the chosen hit is enriched by
  Audnexus (ASIN -> runtime / narrator / synopsis), falling back to Audible's own
  fields if Audnexus is down.
- PRINT / EBOOK queries -> Open Library.
- Expensive enrichments are cached in the ``provider_cache`` table so re-adds and
  offline use don't re-hit the network.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from echobooks.config import Settings
from echobooks.db.models import MediaType, ProviderCache
from echobooks.db.session import session_scope

from .audible import AudibleProvider
from .audnexus import AudnexusProvider
from .base import BookDraft, BookHit
from .openlibrary import OpenLibraryProvider

_USER_AGENT = "EchoBooks/0.1 (book catalog; +https://github.com/echobooks)"


class ProviderRegistry:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.load()
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(12.0),
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            follow_redirects=True,
        )
        region = self.settings.audible_region
        self.audible = AudibleProvider(self._client, region=region)
        self.audnexus = AudnexusProvider(self._client, region=region)

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- search ----------------------------------------------------------- #
    async def search(self, query: str, media_type: MediaType) -> list[BookHit]:
        query = query.strip()
        if not query:
            return []
        cache_key = f"search:{media_type.value}:{query.lower()}"
        if (cached := _cache_get(cache_key)) is not None:
            return [BookHit.model_validate(h) for h in cached]

        try:
            if media_type == MediaType.AUDIOBOOK:
                if not self.settings.use_audible:
                    return []
                hits = await self.audible.search(query)
            else:
                if not self.settings.use_openlibrary:
                    return []
                provider = OpenLibraryProvider(self._client, default_media=media_type)
                hits = await provider.search(query)
        except httpx.HTTPError:
            return []

        _cache_set(cache_key, [h.model_dump(mode="json") for h in hits])
        return hits

    # -- fetch full detail ------------------------------------------------ #
    async def fetch(self, hit: BookHit, media_type: MediaType) -> BookDraft:
        cache_key = f"detail:{hit.source}:{hit.external_id}"
        if (cached := _cache_get(cache_key)) is not None:
            return BookDraft.model_validate(cached)

        try:
            if hit.source in ("audible", "audnexus"):
                draft = await self.audnexus.fetch(hit)
            elif hit.source == "openlibrary":
                provider = OpenLibraryProvider(self._client, default_media=media_type)
                draft = await provider.fetch(hit)
            else:
                draft = BookDraft.from_hit(hit)
        except httpx.HTTPError:
            draft = BookDraft.from_hit(hit)

        _cache_set(cache_key, draft.model_dump(mode="json"))
        return draft

    # -- series ----------------------------------------------------------- #
    async def fetch_series(self, hit: BookHit, picked: BookDraft | None = None) -> list[BookDraft]:
        """All volumes of an audiobook series, enriched and ordered.

        Uses the *picked book's* Audnexus series ASIN (``picked.series_id``) when
        available, since an Audible search hit's first series is unreliable — a
        Mistborn book may list "The Cosmere" (the whole 29-book collection) first
        instead of "The Mistborn Saga". As a safety net the volumes are then
        filtered to those whose own series name matches the picked book's.
        """
        series_asin = (picked.series_id if picked and picked.series_id else hit.series_id)
        target_name = picked.series_name if picked else hit.series_name
        if hit.source != "audible" or not series_asin or not self.settings.use_audible:
            return []
        try:
            asins = await self.audible.series_children(series_asin)
        except httpx.HTTPError:
            return []
        if len(asins) <= 1:
            return []

        drafts = await asyncio.gather(*(self.audnexus.fetch_asin(a) for a in asins))
        result = [d for d in drafts if d is not None and d.title]

        # Keep only volumes whose own series matches the picked book's series.
        if target_name:
            target = target_name.strip().lower()
            matched = [d for d in result if (d.series_name or "").strip().lower() == target]
            if matched:
                result = matched

        # Dedupe by title (the same book appears under multiple region/edition
        # ASINs), and make sure the picked book is present.
        seen_titles: set[str] = set()
        unique = []
        for d in result:
            key = (d.title or "").strip().lower()
            if key in seen_titles:
                continue
            seen_titles.add(key)
            unique.append(d)
        if picked and (picked.title or "").strip().lower() not in seen_titles:
            unique.append(picked)
        unique.sort(key=lambda d: _position_key(d.series_position))
        return unique


def _position_key(pos: str | None) -> float:
    try:
        return float(pos)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 9999.0


# --------------------------------------------------------------------------- #
# Tiny JSON cache backed by the provider_cache table.
# --------------------------------------------------------------------------- #
def _cache_get(key: str) -> Any:
    try:
        with session_scope() as s:
            row = s.get(ProviderCache, key)
            return json.loads(row.value) if row else None
    except Exception:
        return None


def _cache_set(key: str, value: object) -> None:
    try:
        with session_scope() as s:
            row = s.get(ProviderCache, key)
            if row is None:
                s.add(ProviderCache(key=key, value=json.dumps(value)))
            else:
                row.value = json.dumps(value)
    except Exception:
        pass
