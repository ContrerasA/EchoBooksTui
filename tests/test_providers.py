"""Provider parsing tests against recorded JSON payloads (httpx mocked via respx)."""

from __future__ import annotations

import httpx
import pytest
import respx

from echobooks.db.models import MediaType
from echobooks.providers.audible import AudibleProvider
from echobooks.providers.audnexus import AudnexusProvider, _strip_html
from echobooks.providers.base import BookHit
from echobooks.providers.openlibrary import OpenLibraryProvider


@pytest.fixture
async def client():
    async with httpx.AsyncClient() as c:
        yield c


@respx.mock
async def test_openlibrary_search(client):
    respx.get("https://openlibrary.org/search.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "docs": [
                    {
                        "key": "/works/OL893415W",
                        "title": "Dune",
                        "author_name": ["Frank Herbert"],
                        "first_publish_year": 1965,
                        "cover_i": 12345,
                        "number_of_pages_median": 592,
                        "language": ["eng"],
                    }
                ]
            },
        )
    )
    prov = OpenLibraryProvider(client, default_media=MediaType.EBOOK)
    hits = await prov.search("dune")
    assert len(hits) == 1
    hit = hits[0]
    assert hit.title == "Dune"
    assert hit.authors == ["Frank Herbert"]
    assert hit.year == "1965"
    assert hit.media_type == MediaType.EBOOK
    assert hit.cover_url and "12345" in hit.cover_url


@respx.mock
async def test_audible_search(client):
    respx.get(host="api.audible.com").mock(
        return_value=httpx.Response(
            200,
            json={
                "products": [
                    {
                        "asin": "B08G9PRS1K",
                        "title": "Project Hail Mary",
                        "authors": [{"name": "Andy Weir"}],
                        "narrators": [{"name": "Ray Porter"}],
                        "runtime_length_min": 970,
                        "release_date": "2021-05-04",
                        "product_images": {"500": "https://img/500.jpg"},
                    }
                ]
            },
        )
    )
    prov = AudibleProvider(client, region="us")
    hits = await prov.search("project hail mary")
    assert len(hits) == 1
    hit = hits[0]
    assert hit.external_id == "B08G9PRS1K"
    assert hit.narrators == ["Ray Porter"]
    assert hit.runtime_min == 970
    assert hit.media_type == MediaType.AUDIOBOOK
    assert hit.year == "2021"


@respx.mock
async def test_audible_search_parses_series(client):
    respx.get(host="api.audible.com").mock(
        return_value=httpx.Response(
            200,
            json={
                "products": [
                    {
                        "asin": "B002V0QCYU",
                        "title": "The Final Empire",
                        "authors": [{"name": "Brandon Sanderson"}],
                        "series": [{"asin": "B006K1P698", "title": "Mistborn", "sequence": "1"}],
                    }
                ]
            },
        )
    )
    prov = AudibleProvider(client)
    hit = (await prov.search("mistborn"))[0]
    assert hit.has_series
    assert hit.series_id == "B006K1P698"
    assert hit.series_name == "Mistborn"
    assert hit.series_position == "1"


@respx.mock
async def test_audible_series_children_dedupes_and_orders(client):
    respx.get("https://api.audible.com/1.0/catalog/products/B006K1P698").mock(
        return_value=httpx.Response(
            200,
            json={
                "product": {
                    "relationships": [
                        {"relationship_to_product": "child", "asin": "B2", "sort": "2"},
                        {"relationship_to_product": "child", "asin": "B1", "sort": "1"},
                        # Duplicate of position 1 (other region) — should be dropped.
                        {"relationship_to_product": "child", "asin": "B1B", "sort": "1"},
                        {"relationship_to_product": "parent", "asin": "PARENT"},
                    ]
                }
            },
        )
    )
    prov = AudibleProvider(client)
    asins = await prov.series_children("B006K1P698")
    assert asins == ["B1", "B2"]


@respx.mock
async def test_audnexus_fetch(client):
    respx.get("https://api.audnex.us/books/B08G9PRS1K").mock(
        return_value=httpx.Response(
            200,
            json={
                "asin": "B08G9PRS1K",
                "title": "Project Hail Mary",
                "authors": [{"name": "Andy Weir"}],
                "narrators": [{"name": "Ray Porter"}],
                "genres": [{"name": "Science Fiction"}],
                "runtimeLengthMin": 970,
                "summary": "<p>A lone astronaut.</p>",
                "publisherName": "Audible Studios",
                "releaseDate": "2021-05-04T00:00:00.000Z",
                "language": "english",
                "image": "https://img/cover.jpg",
                "seriesPrimary": {"name": "Hail Mary", "position": "1"},
            },
        )
    )
    prov = AudnexusProvider(client, region="us")
    draft = await prov.fetch(BookHit(source="audible", external_id="B08G9PRS1K", title="x"))
    assert draft.runtime_min == 970
    assert draft.narrators == ["Ray Porter"]
    assert draft.genres == ["Science Fiction"]
    assert draft.description == "A lone astronaut."
    assert draft.publisher == "Audible Studios"
    assert draft.language == "English"
    assert draft.series_name == "Hail Mary"


@respx.mock
async def test_audnexus_fetch_falls_back_on_error(client):
    respx.get("https://api.audnex.us/books/BAD").mock(return_value=httpx.Response(404))
    prov = AudnexusProvider(client)
    hit = BookHit(
        source="audible",
        external_id="BAD",
        title="Fallback",
        runtime_min=120,
        media_type=MediaType.AUDIOBOOK,
    )
    draft = await prov.fetch(hit)
    assert draft.title == "Fallback"
    assert draft.runtime_min == 120


async def test_fetch_series_filters_to_picked_series():
    from echobooks.providers.base import BookDraft
    from echobooks.providers.registry import ProviderRegistry

    reg = ProviderRegistry()

    async def fake_children(series_asin, limit=40):
        return ["A1", "A2", "B1"]  # B1 belongs to a different series

    def vol(title, series, pos):
        return BookDraft(
            title=title, series_name=series, series_position=pos, media_type=MediaType.AUDIOBOOK
        )

    data = {
        "A1": vol("Book One", "Right Saga", "1"),
        "A2": vol("Book Two", "Right Saga", "2"),
        "B1": vol("Unrelated", "Wrong Collection", "1"),
    }

    async def fake_fetch_asin(asin):
        return data.get(asin)

    reg.audible.series_children = fake_children  # type: ignore[method-assign]
    reg.audnexus.fetch_asin = fake_fetch_asin  # type: ignore[method-assign]

    hit = BookHit(
        source="audible", external_id="A1", title="Book One",
        series_id="WRONG", series_name="Right Saga", media_type=MediaType.AUDIOBOOK,
    )
    picked = BookDraft(title="Book One", series_name="Right Saga", series_id="RIGHT")
    try:
        vols = await reg.fetch_series(hit, picked)
    finally:
        await reg.aclose()
    # Unrelated volume (wrong series) is dropped; ordered by position.
    assert [v.title for v in vols] == ["Book One", "Book Two"]


def test_strip_html():
    assert _strip_html("<p>hi <b>there</b></p>") == "hi there"
    assert _strip_html(None) is None
    assert _strip_html("") is None
