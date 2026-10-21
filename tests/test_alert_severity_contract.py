import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.database import AsyncSessionLocal
from app.models.alert import Alert
from app.models.edge_device import EdgeDevice
from app.notifications.push import (
    CLASSROOM_ALERT_DATA_KEYS,
    NOTIFICATION_TEMPLATES,
    send_expo_pushes,
)
from app.schemas.alert import (
    MAX_EVIDENCE_ITEMS,
    MAX_EVIDENCE_STRING_LENGTH,
    REVIEW_NOTICE,
)
from tests.conftest import auth_headers, finalized_alert_fields


def _payload(severity="medium", **overrides):
    payload = finalized_alert_fields(overrides.pop("event_id", None))
    payload.update(
        {
            "severity": severity,
            "confidence": 0.91,
            "duration": 1.75,
            "location": "Synthetic Classroom",
            "transcribed_text": "Exact Transcript — Huwag BAGUHIN.",
            "language": "mixed",
            "yamnet_ran": False,
            "yamnet_class": "NotRun",
            "yamnet_score": 0.0,
        }
    )
    payload.update(overrides)
    return payload


def _evidence(level):
    return {
        "level": level,
        "reasons": ["term_category:self_harm_directive"],
        "term_categories": {
            "self_harm_directive": ["synthetic matched phrase"],
        },
        "supporting_evidence": ["laughter_or_excitement_marker_present"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("submitted", "canonical", "compatible"),
    [
        ("low", "LOW", "low"),
        ("MEDIUM", "MEDIUM", "medium"),
        ("HiGh", "HIGH", "high"),
    ],
)
async def test_severity_is_normalized_persisted_and_exposed(
    client,
    submitted,
    canonical,
    compatible,
):
    response = await client.post(
        "/alerts/",
        json=_payload(submitted, severity_evidence=_evidence(canonical)),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["severity"] == compatible
    assert body["severity_level"] == canonical
    assert body["severity_evidence"]["level"] == canonical
    assert body["review_notice"] == REVIEW_NOTICE

    async with AsyncSessionLocal() as session:
        stored = await session.get(Alert, body["id"])
    assert stored.severity == canonical
    assert stored.severity_evidence == _evidence(canonical)


@pytest.mark.asyncio
@pytest.mark.parametrize("severity", ["critical", "urgent", "severe", "unknown", "", 3])
async def test_unsupported_severity_is_rejected_without_creating_a_row(client, severity):
    response = await client.post("/alerts/", json=_payload(severity))

    assert response.status_code == 422
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count(Alert.id))) == 0


@pytest.mark.asyncio
async def test_missing_severity_is_rejected_instead_of_fabricated(client):
    payload = _payload()
    payload.pop("severity")

    response = await client.post("/alerts/", json=payload)

    assert response.status_code == 422
    assert "severity" in response.text


@pytest.mark.asyncio
async def test_legacy_payload_without_severity_evidence_remains_accepted(client):
    response = await client.post("/alerts/", json=_payload("low"))

    assert response.status_code == 200
    assert response.json()["severity_level"] == "LOW"
    assert response.json()["severity_evidence"] is None


@pytest.mark.asyncio
async def test_severity_evidence_round_trips_through_detail(client, identities):
    created = await client.post(
        "/alerts/",
        json=_payload("high", severity_evidence=_evidence("high")),
    )
    detail = await client.get(
        f"/alerts/{created.json()['id']}",
        headers=auth_headers(identities["staff"]),
    )

    assert detail.status_code == 200
    assert detail.json()["severity_evidence"] == _evidence("HIGH")
    assert detail.json()["transcribed_text"] == "Exact Transcript — Huwag BAGUHIN."


@pytest.mark.asyncio
async def test_list_exposes_compatible_and_canonical_severity(client, identities):
    await client.post("/alerts/", json=_payload("HIGH"))

    response = await client.get(
        "/alerts/?severity=high",
        headers=auth_headers(identities["counselor"]),
    )

    assert response.status_code == 200
    assert response.json()[0]["severity"] == "high"
    assert response.json()[0]["severity_level"] == "HIGH"


@pytest.mark.asyncio
async def test_old_row_with_null_evidence_returns_truthful_null(client, identities):
    async with AsyncSessionLocal() as session:
        alert = Alert(
            severity="LOW",
            severity_evidence=None,
            confidence=0.5,
            duration=0.2,
            language="unknown",
        )
        session.add(alert)
        await session.commit()
        await session.refresh(alert)
        alert_id = alert.id

    detail = await client.get(
        f"/alerts/{alert_id}",
        headers=auth_headers(identities["admin"]),
    )

    assert detail.status_code == 200
    assert detail.json()["severity_evidence"] is None


@pytest.mark.asyncio
async def test_severity_evidence_level_mismatch_is_rejected(client):
    response = await client.post(
        "/alerts/",
        json=_payload("LOW", severity_evidence=_evidence("HIGH")),
    )

    assert response.status_code == 422
    assert "must match severity" in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "evidence",
    [
        {"level": "LOW", "reasons": "not-a-list"},
        {"level": "LOW", "reasons": ["reason"], "term_categories": ["bad"]},
        {"level": "LOW", "reasons": ["reason"], "supporting_evidence": {"bad": True}},
        {"level": "LOW", "reasons": [], "unexpected": "nested growth"},
    ],
)
async def test_malformed_severity_evidence_is_rejected(client, evidence):
    response = await client.post(
        "/alerts/",
        json=_payload("LOW", severity_evidence=evidence),
    )

    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "evidence",
    [
        {
            "level": "LOW",
            "reasons": ["reason"] * (MAX_EVIDENCE_ITEMS + 1),
        },
        {
            "level": "LOW",
            "reasons": ["x" * (MAX_EVIDENCE_STRING_LENGTH + 1)],
        },
    ],
)
async def test_oversized_severity_evidence_is_rejected(client, evidence):
    response = await client.post(
        "/alerts/",
        json=_payload("LOW", severity_evidence=evidence),
    )

    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["raw_audio", "audio", "audio_bytes", "audio_samples"])
async def test_raw_audio_and_unknown_fields_are_rejected(client, field):
    response = await client.post(
        "/alerts/",
        json=_payload("LOW", **{field: [1, 2, 3]}),
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_duplicate_event_with_evidence_is_idempotent_and_not_renotified(
    client,
    prevent_external_notifications,
):
    event_id = str(uuid4())
    payload = _payload(
        "medium",
        event_id=event_id,
        severity_evidence=_evidence("MEDIUM"),
    )

    first = await client.post("/alerts/", json=payload)
    duplicate = await client.post("/alerts/", json=payload)

    assert first.status_code == duplicate.status_code == 200
    assert first.json()["id"] == duplicate.json()["id"]
    prevent_external_notifications.assert_awaited_once()


@pytest.mark.parametrize("level", ["LOW", "MEDIUM", "HIGH"])
def test_each_severity_has_a_distinct_privacy_safe_template(level):
    template = NOTIFICATION_TEMPLATES[level]

    assert "review" in template.body.casefold()
    assert "transcript" not in template.body.casefold()
    assert "confirmed" not in template.body.casefold()
    assert "bully" not in template.body.casefold()
    assert "guilt" not in template.body.casefold()


def test_notification_templates_are_distinct_and_high_uses_provider_priority():
    assert len({template.title for template in NOTIFICATION_TEMPLATES.values()}) == 3
    assert len({template.body for template in NOTIFICATION_TEMPLATES.values()}) == 3
    assert NOTIFICATION_TEMPLATES["LOW"].priority == "normal"
    assert NOTIFICATION_TEMPLATES["MEDIUM"].priority == "normal"
    assert NOTIFICATION_TEMPLATES["HIGH"].priority == "high"


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["LOW", "MEDIUM", "HIGH"])
async def test_push_payload_selects_template_and_omits_sensitive_evidence(monkeypatch, level):
    captured = {}

    class SuccessfulResponse:
        def raise_for_status(self):
            return None

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json, timeout):
            captured["messages"] = json
            return SuccessfulResponse()

    monkeypatch.setattr("app.notifications.push.httpx.AsyncClient", FakeClient)

    await send_expo_pushes(
        ["ExpoPushToken[synthetic-token]"],
        alert_id=7,
        severity=level,
        location="Private Location",
        event_id="00000000-0000-4000-8000-000000000007",
        trigger_type="KEYWORD",
    )

    message = captured["messages"][0]
    assert message["title"] == NOTIFICATION_TEMPLATES[level].title
    assert message["body"] == NOTIFICATION_TEMPLATES[level].body
    assert message["sound"] == "default"
    assert message["priority"] == NOTIFICATION_TEMPLATES[level].priority
    assert message["channelId"] == (
        "echosense-high-alerts" if level == "HIGH" else "echosense-phase3-alerts"
    )
    assert message["data"] == {
        "type": "classroom_alert",
        "alertId": 7,
        "event_id": "00000000-0000-4000-8000-000000000007",
        "severity": level.lower(),
        "severityLevel": level,
        "trigger_type": "KEYWORD",
        "route": "/alert/7",
        "is_test": False,
    }
    assert frozenset(message["data"]) == CLASSROOM_ALERT_DATA_KEYS
    assert message["data"]["route"] == f"/alert/{message['data']['alertId']}"
    prohibited = {
        "transcript",
        "transcribed_text",
        "monitored_terms",
        "severity_evidence",
        "acoustic_trigger_evidence",
        "classroom_name",
        "school_name",
        "device_id",
        "device_code",
        "device_identifier",
        "authorization",
        "device_key",
        "push_token",
    }
    assert prohibited.isdisjoint(message["data"])
    assert "Private Location" not in str(message)
    assert "transcript" not in str(message).casefold()


@pytest.mark.asyncio
async def test_push_omits_blank_and_malformed_tokens(monkeypatch):
    captured = {}

    class SuccessfulResponse:
        def raise_for_status(self):
            return None

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json, timeout):
            captured["messages"] = json
            return SuccessfulResponse()

    monkeypatch.setattr("app.notifications.push.httpx.AsyncClient", FakeClient)

    await send_expo_pushes(
        [
            None,
            "",
            "   ",
            "malformed-token",
            " ExpoPushToken[synthetic-token] ",
            "ExpoPushToken[synthetic-token]",
        ],
        alert_id=7,
        severity="LOW",
        location="Private Location",
        event_id="00000000-0000-4000-8000-000000000007",
        trigger_type="KEYWORD",
    )

    assert [message["to"] for message in captured["messages"]] == ["ExpoPushToken[synthetic-token]"]


@pytest.mark.asyncio
async def test_push_detects_per_message_expo_rejection(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {"status": "ok", "id": "synthetic-ticket-one"},
                    {
                        "status": "error",
                        "details": {"error": "DeviceNotRegistered"},
                    },
                ]
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            return Response()

    monkeypatch.setattr("app.notifications.push.httpx.AsyncClient", FakeClient)

    result = await send_expo_pushes(
        ["ExpoPushToken[first]", "ExponentPushToken[second]"],
        alert_id=7,
        severity="MEDIUM",
        location="Private Location",
        event_id="00000000-0000-4000-8000-000000000007",
        trigger_type="KEYWORD",
    )

    assert result.status == "partial"
    assert result.accepted_count == 1
    assert result.rejected_count == 1
    assert result.last_error == "DeviceNotRegistered"


@pytest.mark.asyncio
async def test_phase3_test_push_uses_explicit_test_copy(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"status": "ok", "id": "synthetic-test-ticket"}]}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            captured.update(kwargs)
            return Response()

    monkeypatch.setattr("app.notifications.push.httpx.AsyncClient", FakeClient)

    await send_expo_pushes(
        ["ExpoPushToken[test-device]"],
        alert_id=9,
        severity="LOW",
        location="Private Location",
        event_id="00000000-0000-4000-8000-000000000009",
        trigger_type="TEST",
        is_test=True,
    )

    message = captured["json"][0]
    assert message["title"] == "EchoSense Alert — TEST"
    assert message["body"] == "TEST possible verbal-aggression event. Human review required."
    assert message["sound"] == "default"
    assert message["channelId"] == "echosense-phase3-alerts"
    assert message["data"] == {
        "type": "classroom_alert",
        "alertId": 9,
        "event_id": "00000000-0000-4000-8000-000000000009",
        "severity": "low",
        "severityLevel": "LOW",
        "trigger_type": "TEST",
        "route": "/alert/9",
        "is_test": True,
    }


@pytest.mark.asyncio
async def test_classroom_push_rejects_inconsistent_test_mapping():
    with pytest.raises(ValueError, match="TEST state is inconsistent"):
        await send_expo_pushes(
            ["ExpoPushToken[test-device]"],
            alert_id=9,
            severity="LOW",
            location="Private Location",
            event_id="00000000-0000-4000-8000-000000000009",
            trigger_type="TEST",
            is_test=False,
        )


@pytest.mark.asyncio
async def test_push_provider_failure_leaves_committed_alert_stored(
    client,
    monkeypatch,
):
    class FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json, timeout):
            raise RuntimeError("synthetic provider failure")

    async with AsyncSessionLocal() as session:
        from app.models.user import User

        school_id = await session.scalar(select(EdgeDevice.school_id).limit(1))
        session.add(
            User(
                email="push-failure@example.test",
                hashed_password="synthetic",
                role="staff",
                push_token="ExpoPushToken[synthetic-token]",
                school_id=school_id,
            )
        )
        await session.commit()

    monkeypatch.setattr("app.notifications.push.httpx.AsyncClient", FailingClient)
    monkeypatch.setattr("app.routers.alerts.send_expo_pushes", send_expo_pushes)

    response = await client.post("/alerts/", json=_payload("HIGH"))
    alert_id = response.json()["id"]
    for _ in range(20):
        await asyncio.sleep(0.01)
        async with AsyncSessionLocal() as session:
            stored = await session.get(Alert, alert_id)
            if stored.push_status == "failed":
                break

    assert response.status_code == 200
    async with AsyncSessionLocal() as session:
        stored = await session.get(Alert, alert_id)
        assert stored is not None
        assert stored.delivery_status == "stored"
        assert stored.push_status == "failed"
        assert stored.push_attempt_count == 1
        assert stored.push_last_error == "RuntimeError"
        assert stored.push_submitted_at is not None


def test_openapi_documents_evidence_canonical_level_and_review_notice():
    from app.main import app

    openapi = app.openapi()
    create = openapi["components"]["schemas"]["AlertCreate"]
    response = openapi["components"]["schemas"]["AlertResponse"]
    evidence = openapi["components"]["schemas"]["SeverityEvidence"]
    severity = openapi["components"]["schemas"]["SeverityLevel"]

    assert severity["enum"] == ["LOW", "MEDIUM", "HIGH"]
    assert "severity_evidence" in create["properties"]
    assert "severity_evidence" not in create["required"]
    assert {
        "severity",
        "severity_level",
        "severity_evidence",
        "review_notice",
        "transcript",
        "transcribed_text",
        "delivery_status",
        "push_status",
    } <= response["properties"].keys()
    assert {"type": "null"} in response["properties"]["push_status"]["anyOf"]
    assert evidence["additionalProperties"] is False
    assert create["additionalProperties"] is False
