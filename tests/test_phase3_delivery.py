import asyncio
from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.alert import Alert
from app.models.edge_device import EdgeDevice
from app.models.classroom import Classroom
from app.routers.auth import pwd_context
from app.schemas.alert import AlertCreate, AlertResponse
from tests.conftest import finalized_alert_fields


FINALIZED_RESPONSE_FIELDS = {
    "id",
    "event_id",
    "schema_version",
    "trigger_type",
    "severity",
    "severity_level",
    "severity_reasons",
    "review_message",
    "monitored_terms",
    "monitored_word_detected",
    "monitored_word_occurrences",
    "acoustic_trigger_evidence",
    "detailed_acoustic_evidence",
    "tone_evidence",
    "repetition_evidence",
    "direct_address_evidence",
    "laughter_context",
    "transcript",
    "transcribed_text",
    "transcription_status",
    "event_start_timestamp",
    "event_end_timestamp",
    "trigger_timestamp",
    "test_mode",
    "delivery_status",
    "push_status",
    "device_id",
    "device_code",
    "device_display_name",
    "classroom_name",
    "school_name",
    "created_at",
}


def _payload(event_id=None, **overrides):
    payload = finalized_alert_fields(event_id)
    payload.update(
        {
            "severity": "HIGH",
            "severity_reasons": ["term_category:synthetic"],
            "severity_evidence": {
                "level": "HIGH",
                "reasons": ["term_category:synthetic"],
                "term_categories": {"synthetic": ["synthetic phrase"]},
                "supporting_evidence": ["acoustic_support"],
            },
            "device_identifier": "classroom-test-pi",
            "device_source": {"collector": "synthetic"},
            "monitored_terms": ["synthetic phrase"],
            "monitored_word_detected": True,
            "monitored_word_occurrences": [
                {"term": "synthetic phrase", "confidence": 0.91, "offset_seconds": 0.2}
            ],
            "acoustic_trigger_evidence": {"class": "Speech", "score": 0.83},
            "detailed_acoustic_evidence": {"rms": 0.18, "windows": [{"score": 0.8}]},
            "tone_evidence": {"label": "upset", "confidence": 0.7},
            "repetition_evidence": {"count": 2},
            "direct_address_evidence": {"detected": False},
            "laughter_context": {"detected": True},
            "transcript": "Synthetic finalized transcript.",
            "transcription_status": "complete",
            "processing_latency": {"total_ms": 125.5},
            "dropped_data_metrics": {"audio_frames": 0},
            "collector_statuses": {"transcriber": "complete", "acoustic": "complete"},
            "event_delivery_summary": {"outbox_attempt": 1},
            "extension_count": 1,
            "extension_reasons": ["synthetic_extension"],
            "maximum_duration_reached": False,
            "pre_trigger_seconds": 1.0,
            "post_trigger_seconds": 2.0,
            "trigger_timestamp": "2026-08-04T00:00:00.250Z",
            "confidence": 0.91,
            "duration": 1.0,
            "yamnet_ran": True,
            "yamnet_class": "Speech",
            "yamnet_score": 0.83,
            "language": "en",
        }
    )
    payload.update(overrides)
    return payload


def test_request_orm_and_response_phase3_fields_align():
    phase3_fields = {
        "schema_version",
        "trigger_type",
        "severity_reasons",
        "review_message",
        "monitored_terms",
        "monitored_word_detected",
        "monitored_word_occurrences",
        "acoustic_trigger_evidence",
        "detailed_acoustic_evidence",
        "tone_evidence",
        "repetition_evidence",
        "direct_address_evidence",
        "laughter_context",
        "transcription_status",
        "event_start_timestamp",
        "event_end_timestamp",
        "processing_latency",
        "dropped_data_metrics",
        "collector_statuses",
        "event_delivery_summary",
        "extension_count",
        "extension_reasons",
        "maximum_duration_reached",
        "pre_trigger_seconds",
        "post_trigger_seconds",
        "trigger_timestamp",
        "test_mode",
    }

    assert phase3_fields <= AlertCreate.model_fields.keys()
    assert phase3_fields <= set(Alert.__table__.columns.keys())
    assert phase3_fields <= AlertResponse.model_fields.keys()
    assert {"transcript", "delivery_status", "push_status", "created_at"} <= (
        AlertResponse.model_fields.keys()
    )


def test_openapi_marks_all_edge_ingest_headers_required():
    from app.main import app

    operation = app.openapi()["paths"]["/alerts/"]["post"]
    headers = {
        parameter["name"]: parameter
        for parameter in operation["parameters"]
        if parameter["in"] == "header"
    }

    assert {
        "Idempotency-Key",
        "X-EchoSense-Device-Id",
        "X-EchoSense-Device-Key",
    } <= headers.keys()
    assert all(parameter["required"] is True for parameter in headers.values())


@pytest.mark.asyncio
async def test_full_finalized_phase2_payload_is_persisted_and_returned(client):
    payload = _payload()

    response = await client.post("/alerts/", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["event_id"] == payload["event_id"]
    assert body["schema_version"] == 2
    assert body["trigger_type"] == "KEYWORD"
    assert body["severity_reasons"] == payload["severity_reasons"]
    assert body["review_message"] == payload["review_message"]
    assert body["monitored_terms"] == payload["monitored_terms"]
    assert body["monitored_word_detected"] is True
    assert body["transcript"] == payload["transcript"]
    assert body["transcribed_text"] == payload["transcript"]
    assert body["event_start_timestamp"] is not None
    assert body["event_end_timestamp"] is not None
    assert body["test_mode"] is False
    assert body["delivery_status"] == "stored"
    assert body["push_status"] == "pending"
    assert body["classroom_name"] == "Synthetic Test Classroom"

    async with AsyncSessionLocal() as session:
        stored = await session.get(Alert, body["id"])
    assert stored.request_fingerprint is not None
    assert stored.tone_evidence == payload["tone_evidence"]
    assert stored.processing_latency == payload["processing_latency"]
    assert stored.delivery_status == "stored"


@pytest.mark.asyncio
async def test_idempotency_header_is_required_and_must_match(client):
    payload = _payload()

    missing = await client.post(
        "/alerts/",
        json=payload,
        headers={"Idempotency-Key": ""},
    )
    mismatch = await client.post(
        "/alerts/",
        json=payload,
        headers={"Idempotency-Key": str(uuid4())},
    )
    no_event = deepcopy(payload)
    no_event.pop("event_id")
    missing_event = await client.post("/alerts/", json=no_event)

    assert missing.status_code == 422
    assert mismatch.status_code == 422
    assert missing_event.status_code == 422


@pytest.mark.asyncio
async def test_identical_retry_returns_original_without_second_push(
    client,
    prevent_external_notifications,
):
    payload = _payload()

    first = await client.post("/alerts/", json=payload)
    duplicate = await client.post("/alerts/", json=deepcopy(payload))
    await asyncio.sleep(0)

    assert first.status_code == duplicate.status_code == 200
    assert first.json()["id"] == duplicate.json()["id"]
    prevent_external_notifications.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "value"),
    [
        ("severity", "LOW"),
        ("transcript", "Changed synthetic transcript."),
        ("trigger_type", "ACOUSTIC"),
        ("tone_evidence", {"label": "changed", "confidence": 0.2}),
    ],
)
async def test_conflicting_duplicate_payload_returns_409(client, change, value):
    payload = _payload()
    first = await client.post("/alerts/", json=payload)
    conflicting = deepcopy(payload)
    conflicting[change] = value
    if change == "severity":
        conflicting["severity_evidence"]["level"] = value

    response = await client.post("/alerts/", json=conflicting)

    assert first.status_code == 200
    assert response.status_code == 409
    assert response.json()["detail"] == "Event payload conflict"


@pytest.mark.asyncio
async def test_cross_device_event_reuse_returns_409(client):
    payload = _payload()
    first = await client.post("/alerts/", json=payload)
    second_key = "synthetic-second-device-key"
    async with AsyncSessionLocal() as session:
        classroom = await session.scalar(select(Classroom).limit(1))
        session.add(
            EdgeDevice(
                device_code="classroom-second-pi",
                display_name="Second synthetic device",
                school_id=classroom.school_id,
                classroom_id=classroom.id,
                legacy_classroom_name=classroom.name,
                legacy_school_name=classroom.school.name,
                api_key_hash=pwd_context.hash(second_key),
            )
        )
        await session.commit()

    reused = deepcopy(payload)
    reused["device_identifier"] = "classroom-second-pi"
    response = await client.post(
        "/alerts/",
        json=reused,
        headers={
            "X-EchoSense-Device-Id": "classroom-second-pi",
            "X-EchoSense-Device-Key": second_key,
            "Idempotency-Key": payload["event_id"],
        },
    )

    assert first.status_code == 200
    assert response.status_code == 409
    assert response.json()["detail"] == "Event identifier conflict"


@pytest.mark.asyncio
async def test_concurrent_identical_insert_creates_one_row_and_one_push(
    client,
    prevent_external_notifications,
):
    payload = _payload()

    first, second = await asyncio.gather(
        client.post("/alerts/", json=deepcopy(payload)),
        client.post("/alerts/", json=deepcopy(payload)),
    )
    await asyncio.sleep(0)

    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    async with AsyncSessionLocal() as session:
        count = await session.scalar(
            select(func.count(Alert.id)).where(Alert.event_id == UUID(payload["event_id"]))
        )
    assert count == 1
    prevent_external_notifications.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "nested_value",
    [
        {"raw_audio": [1, 2, 3]},
        {"nested": {"raw_pcm": [1, 2, 3]}},
        {"nested": [{"audio_base64": "synthetic"}]},
        {"raw_vosk_text": "debug-only speech"},
        {"items": [{"audio_debug": {"samples": [1]}}]},
    ],
)
async def test_recursive_privacy_rejects_prohibited_nested_fields(client, nested_value):
    payload = _payload(tone_evidence=nested_value)

    response = await client.post("/alerts/", json=payload)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_recursive_privacy_checks_all_structured_evidence_locations(client):
    fields = (
        "acoustic_trigger_evidence",
        "detailed_acoustic_evidence",
        "processing_latency",
        "collector_statuses",
    )
    for field in fields:
        response = await client.post(
            "/alerts/",
            json=_payload(**{field: {"nested": [{"recorded_audio": "synthetic"}]}}),
        )
        assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"trigger_type": "UNSUPPORTED"},
        {"trigger_type": "TEST", "test_mode": False},
        {"trigger_type": "KEYWORD", "test_mode": True},
        {"device_identifier": None, "device_source": None},
        {"review_message": "Confirmed event"},
        {
            "event_start_timestamp": "2026-08-04T00:00:02Z",
            "event_end_timestamp": "2026-08-04T00:00:01Z",
        },
        {"yamnet_score": "NaN"},
        {"monitored_word_occurrences": [{"term": "x", "confidence": 1.1}]},
    ],
)
async def test_invalid_finalized_contract_is_rejected(client, overrides):
    response = await client.post("/alerts/", json=_payload(**overrides))

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_unknown_and_oversized_evidence_are_rejected(client):
    unknown = await client.post("/alerts/", json=_payload(unrecognized_evidence=True))
    oversized_reasons = await client.post(
        "/alerts/",
        json=_payload(severity_reasons=[f"reason-{index}" for index in range(51)]),
    )
    oversized_nested_object = await client.post(
        "/alerts/",
        json=_payload(tone_evidence={f"key-{index}": index for index in range(257)}),
    )

    assert unknown.status_code == 422
    assert oversized_reasons.status_code == 422
    assert oversized_nested_object.status_code == 422


@pytest.mark.asyncio
async def test_test_alert_policy_fails_closed_and_marks_enabled_test(
    client,
    monkeypatch,
    prevent_external_notifications,
):
    payload = _payload(trigger_type="TEST", test_mode=True)
    monkeypatch.setattr(settings, "ECHOSENSE_ALLOW_TEST_ALERTS", False)
    disabled = await client.post("/alerts/", json=payload)

    monkeypatch.setattr(settings, "ECHOSENSE_ALLOW_TEST_ALERTS", True)
    enabled = await client.post("/alerts/", json=payload)
    await asyncio.sleep(0)

    assert disabled.status_code == 403
    assert enabled.status_code == 200
    assert enabled.json()["test_mode"] is True
    assert prevent_external_notifications.await_args.kwargs["is_test"] is True


@pytest.mark.asyncio
async def test_list_and_detail_expose_phase3_fields_and_pagination(client, identities):
    first = await client.post("/alerts/", json=_payload())
    second = await client.post("/alerts/", json=_payload())
    headers = {"Authorization": f"Bearer {identities['staff']['token']}"}

    listed = await client.get("/alerts/?skip=1&limit=1", headers=headers)
    detail = await client.get(f"/alerts/{first.json()['id']}", headers=headers)
    invalid_skip = await client.get("/alerts/?skip=-1", headers=headers)
    invalid_limit = await client.get("/alerts/?limit=201", headers=headers)

    assert second.status_code == 200
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    listed_body = listed.json()[0]
    detail_body = detail.json()
    assert FINALIZED_RESPONSE_FIELDS <= listed_body.keys()
    assert FINALIZED_RESPONSE_FIELDS <= detail_body.keys()
    assert listed_body["delivery_status"] == "stored"
    assert detail_body["delivery_status"] == "stored"
    assert listed_body["push_status"] == "pending"
    assert detail_body["push_status"] == "pending"
    assert listed_body["transcript"] == listed_body["transcribed_text"]
    assert detail_body["transcript"] == detail_body["transcribed_text"]
    assert detail_body["schema_version"] == 2
    assert detail_body["tone_evidence"] is not None
    assert invalid_skip.status_code == 422
    assert invalid_limit.status_code == 422
