"""Audible catalog provider — turns a title query into ASIN-bearing audiobook hits.

This is Audible's public catalog endpoint (the same one Audiobookshelf leans on).
It returns ASIN + contributors + runtime directly, which is enough to populate a
result list; richer fields are filled in afterwards by the Audnexus provider.
"""

from __future__ import annotations

import httpx

from echobooks.db.models import MediaType

from .base import BookDraft, BookHit

# Region -> Audible TLD for the catalog host.
REGION_TLD = {
    "us": "com",
    "ca": "ca",
    "uk": "co.uk",
    "au": "com.au",
    "fr": "fr",
    "de": "de",
    "jp": "co.jp",
    "it": "it",
    "in": "in",
    "es": "es",
}
_RESPONSE_GROUPS = "contributors,product_attrs,product_desc,media,series"


class AudibleProvider:
    name = "audible"

    def __init__(self, client: httpx.AsyncClient, region: str = "us") -> None:
        self._client = client
        self._region = region

    def _catalog_url(self) -> str:
        tld = REGION_TLD.get(self._region, "com")
        return f"https://api.audible.{tld}/1.0/catalog/products"

    async def search(self, query: str, limit: int = 10) -> list[BookHit]:
        resp = await self._client.get(
            self._catalog_url(),
            params={
                "keywords": query,
                "num_results": limit,
                "products_sort_by": "Relevance",
                "response_groups": _RESPONSE_GROUPS,
            },
        )
        resp.raise_for_status()
        products = resp.json().get("products", [])
        return [self._to_hit(p) for p in products if p.get("asin") and p.get("title")]

    def _to_hit(self, p: dict) -> BookHit:
        year = (p.get("release_date") or "")[:4] or None
        series = (p.get("series") or [{}])[0]
        return BookHit(
            source=self.name,
            external_id=p["asin"],
            title=p.get("title", ""),
            subtitle=p.get("subtitle"),
            authors=[a["name"] for a in p.get("authors", []) if a.get("name")],
            narrators=[n["name"] for n in p.get("narrators", []) if n.get("name")],
            year=year,
            media_type=MediaType.AUDIOBOOK,
            runtime_min=p.get("runtime_length_min"),
            cover_url=_cover(p),
            series_name=series.get("title"),
            series_position=series.get("sequence") or None,
            series_id=series.get("asin"),
        )

    async def fetch(self, hit: BookHit) -> BookDraft:
        """Fallback draft straight from Audible (used if Audnexus is unavailable)."""
        draft = BookDraft.from_hit(hit)
        draft.media_type = MediaType.AUDIOBOOK
        return draft

    async def series_children(self, series_asin: str, limit: int = 40) -> list[str]:
        """Return the ASINs of the volumes in a series, ordered, de-duplicated.

        Audible lists each volume once per region/edition, so we keep the first
        ASIN seen for each sort position.
        """
        tld = REGION_TLD.get(self._region, "com")
        url = f"https://api.audible.{tld}/1.0/catalog/products/{series_asin}"
        resp = await self._client.get(url, params={"response_groups": "relationships"})
        resp.raise_for_status()
        product = resp.json().get("product", {})
        children = [
            r
            for r in product.get("relationships", [])
            if r.get("relationship_to_product") == "child" and r.get("asin")
        ]
        children.sort(key=lambda r: _as_float(r.get("sort")))
        seen_pos: set[str] = set()
        asins: list[str] = []
        for r in children:
            pos = str(r.get("sort") or r.get("sequence") or "")
            if pos in seen_pos:
                continue
            seen_pos.add(pos)
            asins.append(r["asin"])
            if len(asins) >= limit:
                break
        return asins


def _as_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _cover(p: dict) -> str | None:
    images = p.get("product_images") or {}
    if not images:
        return None
    # Keys are pixel sizes as strings; take the largest.
    best = max(images, key=lambda k: int(k) if str(k).isdigit() else 0)
    return images[best]
