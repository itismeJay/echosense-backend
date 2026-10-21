import pytest

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
