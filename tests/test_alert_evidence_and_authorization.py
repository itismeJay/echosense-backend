from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.database import AsyncSessionLocal
from app.models.alert import Alert
from app.models.user import User
from app.notifications.push import NOTIFICATION_TEMPLATES, send_expo_pushes
from app.routers.auth import create_token
from app.schemas.alert import MAX_ALERT_TRANSCRIPT_LENGTH
from tests.conftest import auth_headers


def _alert_payload(**overrides) -> dict:
    payload = {
        "event_id": str(uuid4()),
        "severity": "medium",
        "confidence": 0.84,
        "duration": 1.4,
        "location": "Synthetic Room",
        "transcribed_text": "Exact synthetic transcript.",
        "language": "en",
        "yamnet_ran": False,
        "yamnet_class": "NotRun",
        "yamnet_score": 0.0,
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("language", "transcript"),
    [
        ("fil", "Hoy, Kumusta Ka? Huwag NIYO akong sigawan!"),
        ("ceb", "Ayaw Ko'g Ingna Ana—Palihog!"),
        ("mixed", "Please, huwag mo akong SIGAWAN; palihog."),
    ],
)
async def test_exact_multilingual_transcript_is_preserved(
    client,
    identities,
    language,
    transcript,
):
    created = await client.post(
        "/alerts/",
        json=_alert_payload(language=language, transcribed_text=transcript),
    )

    assert created.status_code == 200
    assert created.json()["transcribed_text"] == transcript

    alert_id = created.json()["id"]
    detail = await client.get(
        f"/alerts/{alert_id}",
        headers=auth_headers(identities["counselor"]),
    )
    assert detail.status_code == 200
    assert detail.json()["transcribed_text"] == transcript

    async with AsyncSessionLocal() as session:
        stored = await session.get(Alert, alert_id)
    assert stored.transcribed_text == transcript


@pytest.mark.asyncio
@pytest.mark.parametrize("transcript", [None, "", "   \t"])
async def test_new_event_requires_nonblank_finalized_transcript(client, transcript):
    response = await client.post(
        "/alerts/",
        json=_alert_payload(transcribed_text=transcript),
    )

    assert response.status_code == 422
    assert "finalized transcript" in response.text


@pytest.mark.asyncio
async def test_legacy_payload_without_event_id_keeps_optional_transcript_compatibility(client):
    payload = _alert_payload()
    payload.pop("event_id")
    payload["transcribed_text"] = None

    response = await client.post("/alerts/", json=payload)

    assert response.status_code == 200
    assert response.json()["event_id"] is None
    assert response.json()["transcribed_text"] is None


@pytest.mark.asyncio
async def test_oversized_transcript_is_rejected(client):
    response = await client.post(
        "/alerts/",
        json=_alert_payload(
            transcribed_text="x" * (MAX_ALERT_TRANSCRIPT_LENGTH + 1),
        ),
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_malformed_event_id_is_rejected_without_creating_alert(client):
    response = await client.post(
        "/alerts/",
        json=_alert_payload(event_id="not-a-uuid"),
    )

    assert response.status_code == 422
    async with AsyncSessionLocal() as session:
        count = await session.scalar(select(func.count(Alert.id)))
    assert count == 0


@pytest.mark.asyncio
async def test_different_event_ids_create_distinct_alerts(client):
    first = await client.post("/alerts/", json=_alert_payload())
    second = await client.post("/alerts/", json=_alert_payload())

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["event_id"] != second.json()["event_id"]
    assert first.json()["id"] != second.json()["id"]


@pytest.mark.asyncio
async def test_alert_evidence_reads_require_authentication(client, identities):
    created = await client.post("/alerts/", json=_alert_payload())
    alert_id = created.json()["id"]

    protected_paths = [
        "/alerts/",
        f"/alerts/{alert_id}",
        "/alerts/analytics/categories",
        "/alerts/analytics/summary",
        "/logs/",
        "/logs/stats",
    ]
    for path in protected_paths:
        unauthenticated = await client.get(path)
        assert unauthenticated.status_code == 401

        for role in ("admin", "staff", "counselor"):
            authorized = await client.get(path, headers=auth_headers(identities[role]))
            assert authorized.status_code == 200


@pytest.mark.asyncio
async def test_unknown_role_cannot_read_alert_evidence(client):
    async with AsyncSessionLocal() as session:
        user = User(
            email="synthetic-outsider@example.test",
            hashed_password="synthetic-hash",
            role="outsider",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        token = create_token(user)

    response = await client.get(
        "/alerts/",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


def test_notification_and_api_wording_requires_human_review():
    from app.main import app

    assert "Unverified possible-aggression alert. Human review required." in app.description
    assert set(NOTIFICATION_TEMPLATES) == {"LOW", "MEDIUM", "HIGH"}
    for template in NOTIFICATION_TEMPLATES.values():
        assert "review" in template.body.casefold()
        assert "confirmed bullying" not in template.body.casefold()
        assert "aggressive intent" not in template.body.casefold()


def test_alert_contract_does_not_accept_raw_audio_fields():
    from app.main import app

    properties = app.openapi()["components"]["schemas"]["AlertCreate"]["properties"]
    assert "raw_audio" not in properties
    assert "audio" not in properties
    assert "audio_bytes" not in properties


@pytest.mark.asyncio
async def test_push_payload_uses_safe_wording_and_keeps_transcript_off_lock_screen(
    monkeypatch,
):
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
            captured.update(url=url, messages=json, timeout=timeout)
            return SuccessfulResponse()

    monkeypatch.setattr("app.notifications.push.httpx.AsyncClient", FakeClient)

    await send_expo_pushes(
        ["synthetic-token"],
        alert_id=42,
        severity="high",
        location="Synthetic Room",
    )

    assert captured["messages"] == [
        {
            "to": "synthetic-token",
            "title": NOTIFICATION_TEMPLATES["HIGH"].title,
            "body": NOTIFICATION_TEMPLATES["HIGH"].body,
            "sound": "default",
            "priority": "high",
            "channelId": "echosense-high-alerts",
            "data": {
                "alertId": 42,
                "severity": "high",
                "severityLevel": "HIGH",
            },
        }
    ]
    assert "transcript" not in str(captured["messages"]).casefold()
    assert "Synthetic Room" not in str(captured["messages"])


@pytest.mark.asyncio
async def test_push_provider_failure_is_contained_without_logging_token(
    monkeypatch,
    caplog,
):
    token = "synthetic-private-token"

    class FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json, timeout):
            raise RuntimeError(f"provider rejected {token}")

    monkeypatch.setattr("app.notifications.push.httpx.AsyncClient", FailingClient)

    await send_expo_pushes(
        [token],
        alert_id=42,
        severity="high",
        location="Synthetic Room",
    )

    assert "reason=RuntimeError" in caplog.text
    assert token not in caplog.text
