import pytest

from app.routers.system_logs import REDACTED_STT_MESSAGE
from tests.conftest import auth_headers


@pytest.mark.asyncio
async def test_stt_log_ingestion_redacts_harmless_transcript(client, identities):
    synthetic_transcript = "Ordinary synthetic classroom conversation."

    ingested = await client.post(
        "/system/logs",
        json={"lines": [f"[STT] {synthetic_transcript}"]},
    )
    queried = await client.get(
        "/system/logs",
        headers=auth_headers(identities["admin"]),
    )

    assert ingested.status_code == 200
    assert queried.status_code == 200
    body = queried.json()
    assert body["lines"][-1]["type"] == "stt"
    assert body["lines"][-1]["message"] == REDACTED_STT_MESSAGE
    assert synthetic_transcript not in queried.text


@pytest.mark.asyncio
async def test_stt_hit_log_preserves_classification_but_not_text(client, identities):
    synthetic_transcript = "Synthetic monitored phrase."

    await client.post(
        "/system/logs",
        json={"lines": [f"[STT] HIT {synthetic_transcript}"]},
    )
    queried = await client.get(
        "/system/logs",
        headers=auth_headers(identities["admin"]),
    )

    body = queried.json()
    assert body["lines"][-1]["type"] == "hit"
    assert body["lines"][-1]["message"] == REDACTED_STT_MESSAGE
    assert synthetic_transcript not in queried.text
