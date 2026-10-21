from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.slur import SlurEntry
from tests.conftest import auth_headers, finalized_alert_fields


async def _ingest_keyword_alert(client) -> None:
    async with AsyncSessionLocal() as session:
        session.add(SlurEntry(slur_text="example phrase", language="en", severity_weight=0.6))
        await session.commit()
    payload = finalized_alert_fields()
    payload.update(
        {
            "severity": "medium",
            "confidence": 0.91,
            "duration": 1.25,
            "location": "Room 101",
            "transcribed_text": "Synthetic finalized transcript.",
            "matched_terms": [{"term": "example phrase", "language": "en"}],
        }
    )
    response = await client.post("/alerts/", json=payload)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_logs_list_loads_matched_terms_without_lazy_loading(client, identities):
    await _ingest_keyword_alert(client)

    response = await client.get("/logs/", headers=auth_headers(identities["staff"]))

    assert response.status_code == 200
    assert response.json()[0]["matched_terms"][0]["term"] == "example phrase"


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["skip=-1", "limit=-1", "limit=0", "limit=100000"])
async def test_logs_list_rejects_out_of_range_pagination(client, identities, query):
    response = await client.get(f"/logs/?{query}", headers=auth_headers(identities["staff"]))

    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claims",
    [{}, {"sub": "abc"}, {"sub": ""}, {"sub": "-1"}, {"sub": "\u0661"}, {"sub": None}],
    ids=["missing", "non-numeric", "empty", "negative", "non-ascii-digit", "null"],
)
async def test_signed_token_with_invalid_subject_is_unauthorized(client, claims):
    token = jwt.encode(
        {**claims, "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        settings.SECRET_KEY,
        algorithm="HS256",
    )

    response = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid token"}


COLLECTION_GET_ROUTES = [
    "/alerts",
    "/logs",
    "/dictionary",
    "/reports",
    "/users",
    "/system-settings",
    "/audit-logs",
    "/classrooms",
    "/devices",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", COLLECTION_GET_ROUTES)
@pytest.mark.parametrize("suffix", ["", "/"], ids=["no-slash", "slash"])
async def test_collection_get_routes_answer_without_redirect(client, identities, path, suffix):
    response = await client.get(path + suffix, headers=auth_headers(identities["admin"]))

    assert response.status_code == 200
    assert "location" not in response.headers


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["", "/"], ids=["no-slash", "slash"])
async def test_collection_post_routes_answer_without_redirect(client, identities, suffix):
    headers = auth_headers(identities["admin"])

    report = await client.post(
        "/reports" + suffix,
        json={"date_from": "2026-01-01", "date_to": "2026-01-31"},
        headers=headers,
    )
    user = await client.post(
        "/users" + suffix,
        json={"email": f"slash{len(suffix)}@school.test", "password": "long-enough-password"},
        headers=headers,
    )

    assert report.status_code == 201
    assert user.status_code == 201


@pytest.mark.asyncio
async def test_slash_aliases_are_hidden_from_openapi(client):
    paths = (await client.get("/openapi.json")).json()["paths"]

    assert "/users/" in paths and "/users" not in paths
    assert "/devices" in paths and "/devices/" not in paths
