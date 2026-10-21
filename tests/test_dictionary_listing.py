import pytest

from app.database import AsyncSessionLocal
from app.models.slur import SlurEntry

TERMS = [
    ("alpha phrase", "en", 0.9),
    ("beta salita", "fil", 0.8),
    ("gamma pulong", "ceb", 0.7),
    ("100% sure", "en", 0.6),
    ("under_score", "en", 0.5),
    ("alpha again", "ceb", 0.4),
]


@pytest.fixture
async def seeded_terms():
    async with AsyncSessionLocal() as session:
        session.add_all(
            SlurEntry(slur_text=text, language=language, severity_weight=weight)
            for text, language, weight in TERMS
        )
        await session.commit()


def _texts(response) -> list[str]:
    return [entry["slur_text"] for entry in response.json()]


@pytest.mark.asyncio
async def test_unpaginated_listing_still_returns_every_term_for_edge_sync(client, seeded_terms):
    response = await client.get("/dictionary/")

    assert response.status_code == 200
    assert isinstance(response.json(), list)
    assert _texts(response) == [text for text, _, _ in TERMS]
    assert set(response.json()[0]) == {
        "term_id",
        "slur_text",
        "language",
        "severity_weight",
        "added_at",
    }
    assert response.headers["X-Total-Count"] == str(len(TERMS))


@pytest.mark.asyncio
async def test_listing_pages_with_stable_order_and_total(client, seeded_terms):
    first = await client.get("/dictionary/?limit=4")
    second = await client.get("/dictionary/?limit=4&skip=4")

    assert _texts(first) + _texts(second) == [text for text, _, _ in TERMS]
    assert first.headers["X-Total-Count"] == second.headers["X-Total-Count"] == "6"


@pytest.mark.asyncio
async def test_listing_filters_by_search_and_language(client, seeded_terms):
    search = await client.get("/dictionary/?search=  ALPHA ")
    language = await client.get("/dictionary/?language=ceb")
    combined = await client.get("/dictionary/?search=alpha&language=ceb&limit=1")

    assert _texts(search) == ["alpha phrase", "alpha again"]
    assert search.headers["X-Total-Count"] == "2"
    assert _texts(language) == ["gamma pulong", "alpha again"]
    assert _texts(combined) == ["alpha again"]
    assert combined.headers["X-Total-Count"] == "1"


@pytest.mark.asyncio
@pytest.mark.parametrize(("search", "expected"), [("%", ["100% sure"]), ("_", ["under_score"])])
async def test_search_treats_wildcards_literally(client, seeded_terms, search, expected):
    response = await client.get("/dictionary/", params={"search": search})

    assert _texts(response) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    ["limit=0", "limit=501", "skip=-1", "language=mixed", "language=Bisaya", "search=" + "x" * 101],
)
async def test_listing_rejects_invalid_parameters(client, query):
    response = await client.get(f"/dictionary/?{query}")

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_total_count_header_is_exposed_to_browsers(client):
    response = await client.get(
        "/dictionary/", headers={"Origin": "https://echosense-frontend.vercel.app"}
    )

    assert "x-total-count" in response.headers["access-control-expose-headers"].lower()
