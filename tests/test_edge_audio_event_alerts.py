from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.database import AsyncSessionLocal
from app.models.alert import Alert


def _alert_payload(**overrides) -> dict:
    payload = {
        "severity": "medium",
        "confidence": 0.91,
        "duration": 1.25,
        "location": "Room 101",
        "transcribed_text": "synchronized utterance",
        "language": "en",
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_yamnet_ran_event_is_accepted_persisted_and_returned(client):
    event_id = uuid4()
    response = await client.post(
        "/alerts/",
        json=_alert_payload(
            event_id=str(event_id),
            yamnet_ran=True,
            yamnet_class="Speech",
            yamnet_score=0.83,
        ),
    )

    assert response.status_code == 200
    assert response.json()["event_id"] == str(event_id)
    assert response.json()["yamnet_ran"] is True
    assert response.json()["yamnet_class"] == "Speech"
    assert response.json()["yamnet_score"] == 0.83

    detail = await client.get(f"/alerts/{response.json()['id']}")
    listed = await client.get("/alerts/")

    assert detail.status_code == 200
    assert detail.json()["event_id"] == str(event_id)
    assert detail.json()["yamnet_ran"] is True
    assert listed.status_code == 200
    assert listed.json()[0]["event_id"] == str(event_id)
    assert listed.json()[0]["yamnet_ran"] is True

    async with AsyncSessionLocal() as session:
        alert = (
            await session.execute(select(Alert).where(Alert.event_id == event_id))
        ).scalar_one()

    assert alert.event_id == event_id
    assert alert.yamnet_ran is True


@pytest.mark.asyncio
async def test_explicit_yamnet_not_run_sentinel_is_accepted(client):
    response = await client.post(
        "/alerts/",
        json=_alert_payload(
            event_id=str(uuid4()),
            yamnet_ran=False,
            yamnet_class="NotRun",
            yamnet_score=0.0,
        ),
    )

    assert response.status_code == 200
    assert response.json()["yamnet_ran"] is False
    assert response.json()["yamnet_class"] == "NotRun"
    assert response.json()["yamnet_score"] == 0.0


@pytest.mark.asyncio
async def test_explicit_yamnet_not_run_fabricated_evidence_is_normalized(client):
    response = await client.post(
        "/alerts/",
        json=_alert_payload(
            event_id=str(uuid4()),
            yamnet_ran=False,
            yamnet_class="Speech",
            yamnet_score=0.60,
        ),
    )

    assert response.status_code == 200
    assert response.json()["yamnet_ran"] is False
    assert response.json()["yamnet_class"] == "NotRun"
    assert response.json()["yamnet_score"] == 0.0


@pytest.mark.asyncio
async def test_yamnet_ran_rejects_not_run_label(client):
    response = await client.post(
        "/alerts/",
        json=_alert_payload(
            event_id=str(uuid4()),
            yamnet_ran=True,
            yamnet_class="NotRun",
            yamnet_score=0.0,
        ),
    )

    assert response.status_code == 422
    assert "actual non-empty label" in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("score", [-0.01, 1.01])
async def test_yamnet_ran_rejects_score_outside_probability_bounds(client, score):
    response = await client.post(
        "/alerts/",
        json=_alert_payload(
            event_id=str(uuid4()),
            yamnet_ran=True,
            yamnet_class="Speech",
            yamnet_score=score,
        ),
    )

    assert response.status_code == 422
    assert "between 0 and 1" in response.text


@pytest.mark.asyncio
async def test_legacy_yamnet_payload_without_new_fields_remains_accepted(client):
    response = await client.post(
        "/alerts/",
        json=_alert_payload(
            yamnet_class="Speech",
            yamnet_score=0.60,
        ),
    )

    assert response.status_code == 200
    assert response.json()["event_id"] is None
    assert response.json()["yamnet_ran"] is None
    assert response.json()["yamnet_class"] == "Speech"
    assert response.json()["yamnet_score"] == 0.60


@pytest.mark.asyncio
async def test_duplicate_event_is_idempotent_and_not_notified_twice(
    client,
    prevent_external_notifications,
):
    event_id = uuid4()
    payload = _alert_payload(
        event_id=str(event_id),
        yamnet_ran=True,
        yamnet_class="Speech",
        yamnet_score=0.83,
    )

    first = await client.post("/alerts/", json=payload)
    second = await client.post("/alerts/", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["event_id"] == str(event_id)

    async with AsyncSessionLocal() as session:
        count = await session.scalar(select(func.count(Alert.id)).where(Alert.event_id == event_id))

    assert count == 1
    assert prevent_external_notifications.call_count == 1


@pytest.mark.asyncio
async def test_requests_without_event_id_are_never_deduplicated(
    client,
    prevent_external_notifications,
):
    payload = _alert_payload(yamnet_class="Speech", yamnet_score=0.60)

    first = await client.post("/alerts/", json=payload)
    second = await client.post("/alerts/", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] != second.json()["id"]
    assert prevent_external_notifications.call_count == 2


@pytest.mark.asyncio
async def test_old_alert_with_null_edge_evidence_serializes_in_list_and_detail(client):
    async with AsyncSessionLocal() as session:
        alert = Alert(
            severity="low",
            confidence=0.55,
            duration=0.4,
            language="unknown",
            event_id=None,
            yamnet_ran=None,
        )
        session.add(alert)
        await session.commit()
        await session.refresh(alert)
        alert_id = alert.id

    listed = await client.get("/alerts/")
    detail = await client.get(f"/alerts/{alert_id}")

    assert listed.status_code == 200
    assert detail.status_code == 200
    assert listed.json()[0]["event_id"] is None
    assert listed.json()[0]["yamnet_ran"] is None
    assert detail.json()["event_id"] is None
    assert detail.json()["yamnet_ran"] is None


def test_openapi_exposes_optional_edge_audio_event_fields():
    from app.main import app

    openapi = app.openapi()
    request_schema = openapi["components"]["schemas"]["AlertCreate"]
    response_schema = openapi["components"]["schemas"]["AlertResponse"]
    post_operation = openapi["paths"]["/alerts/"]["post"]

    assert post_operation["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/AlertCreate"
    }
    for field in (
        "event_id",
        "yamnet_ran",
        "yamnet_class",
        "yamnet_score",
        "language",
        "language_confidence",
        "matched_terms",
        "transcribed_text",
    ):
        assert field in request_schema["properties"]
    assert "event_id" not in request_schema.get("required", [])
    assert "yamnet_ran" not in request_schema.get("required", [])
    assert {"event_id", "yamnet_ran"} <= response_schema["properties"].keys()

    event_id_options = request_schema["properties"]["event_id"]["anyOf"]
    assert {"type": "string", "format": "uuid"} in event_id_options
    yamnet_ran_options = request_schema["properties"]["yamnet_ran"]["anyOf"]
    assert {"type": "boolean"} in yamnet_ran_options
