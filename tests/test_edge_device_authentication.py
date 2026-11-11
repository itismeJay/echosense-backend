import logging
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.database import AsyncSessionLocal
from app.models.alert import Alert
from app.models.edge_device import EdgeDevice
from app.services.device_auth import verify_device_key
from tests.conftest import auth_headers, finalized_alert_fields


def _alert_payload(event_id=None, severity="MEDIUM", **overrides) -> dict:
    payload = finalized_alert_fields(event_id)
    payload.update(
        {
            "severity": severity,
            "confidence": 0.82,
            "duration": 1.4,
            "location": "Untrusted Body Classroom",
            "transcribed_text": "Synthetic finalized transcript.",
            "language": "en",
            "yamnet_ran": False,
            "yamnet_class": "NotRun",
            "yamnet_score": 0.0,
        }
    )
    payload.update(overrides)
    return payload


def _device_payload(code="classroom-101-pi") -> dict:
    return {
        "device_code": code,
        "display_name": "Room 101 EchoSense",
        "classroom_name": "Synthetic Test Classroom",
        "school_name": "Synthetic Test School",
    }


@pytest.mark.asyncio
async def test_missing_device_headers_are_rejected(unauthenticated_edge_client):
    response = await unauthenticated_edge_client.post("/alerts/", json=_alert_payload())

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid device credentials"}


@pytest.mark.asyncio
async def test_each_required_device_header_is_rejected_when_missing(
    unauthenticated_edge_client,
    edge_device_identity,
):
    missing_id = await unauthenticated_edge_client.post(
        "/alerts/",
        json=_alert_payload(),
        headers={"X-EchoSense-Device-Key": edge_device_identity["device_key"]},
    )
    missing_key = await unauthenticated_edge_client.post(
        "/alerts/",
        json=_alert_payload(),
        headers={"X-EchoSense-Device-Id": edge_device_identity["device_code"]},
    )

    assert missing_id.status_code == missing_key.status_code == 401
    assert missing_id.json() == missing_key.json() == {"detail": "Invalid device credentials"}
    assert edge_device_identity["device_key"] not in missing_id.text
    assert edge_device_identity["device_key"] not in missing_key.text


@pytest.mark.asyncio
async def test_unknown_device_and_wrong_key_are_rejected(
    unauthenticated_edge_client,
    edge_device_identity,
):
    unknown = await unauthenticated_edge_client.post(
        "/alerts/",
        json=_alert_payload(),
        headers={
            "X-EchoSense-Device-Id": "unknown-classroom-pi",
            "X-EchoSense-Device-Key": "synthetic-wrong-key",
        },
    )
    wrong_key = await unauthenticated_edge_client.post(
        "/alerts/",
        json=_alert_payload(),
        headers={
            "X-EchoSense-Device-Id": edge_device_identity["device_code"],
            "X-EchoSense-Device-Key": "synthetic-wrong-key",
        },
    )

    assert unknown.status_code == wrong_key.status_code == 401
    assert unknown.json() == wrong_key.json() == {"detail": "Invalid device credentials"}


@pytest.mark.asyncio
async def test_disabled_device_is_rejected(client, edge_device_identity):
    async with AsyncSessionLocal() as session:
        device = await session.get(EdgeDevice, edge_device_identity["id"])
        device.is_active = False
        await session.commit()

    response = await client.post("/alerts/", json=_alert_payload())

    assert response.status_code == 403
    assert response.json() == {"detail": "Invalid device credentials"}


@pytest.mark.asyncio
async def test_correct_credentials_assign_trusted_device_and_classroom(
    client,
    edge_device_identity,
):
    response = await client.post("/alerts/", json=_alert_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["device_id"] == str(edge_device_identity["id"])
    assert body["device_code"] == edge_device_identity["device_code"]
    assert body["device_display_name"] == "Synthetic Test Device"
    assert body["classroom_name"] == "Synthetic Test Classroom"
    assert body["school_name"] == "Synthetic Test School"
    assert body["location"] == "Synthetic Test Classroom"
    assert body["review_notice"] == ("Unverified possible-aggression alert. Human review required.")

    async with AsyncSessionLocal() as session:
        stored = await session.get(Alert, body["id"])
        assert stored.edge_device_id == edge_device_identity["id"]
        assert stored.classroom_name_snapshot == "Synthetic Test Classroom"
        assert stored.school_name_snapshot == "Synthetic Test School"


@pytest.mark.asyncio
async def test_request_body_device_identity_is_rejected_and_location_is_not_trusted(
    client,
):
    spoofed_identity = await client.post(
        "/alerts/",
        json=_alert_payload(device_id=str(uuid4())),
    )
    spoofed_location = await client.post(
        "/alerts/",
        json=_alert_payload(location="Spoofed Room"),
    )
    conflicting_identifier = await client.post(
        "/alerts/",
        json=_alert_payload(device_identifier="different-device"),
    )
    conflicting_source = await client.post(
        "/alerts/",
        json=_alert_payload(device_source={"device_code": "different-device"}),
    )

    assert spoofed_identity.status_code == 422
    assert conflicting_identifier.status_code == 422
    assert conflicting_source.status_code == 422
    assert spoofed_location.status_code == 200
    assert spoofed_location.json()["location"] == "Synthetic Test Classroom"
    assert spoofed_location.json()["classroom_name"] == "Synthetic Test Classroom"


@pytest.mark.asyncio
async def test_duplicate_event_is_associated_once_and_not_renotified(
    client,
    edge_device_identity,
    prevent_external_notifications,
):
    event_id = uuid4()
    payload = _alert_payload(event_id)

    first = await client.post("/alerts/", json=payload)
    duplicate = await client.post("/alerts/", json=payload)

    assert first.status_code == duplicate.status_code == 200
    assert first.json()["id"] == duplicate.json()["id"]
    assert duplicate.json()["device_id"] == str(edge_device_identity["id"])
    async with AsyncSessionLocal() as session:
        count = await session.scalar(select(func.count(Alert.id)).where(Alert.event_id == event_id))
    assert count == 1
    prevent_external_notifications.assert_awaited_once()


@pytest.mark.asyncio
async def test_historical_alert_without_device_serializes_with_null_context(
    client,
    identities,
):
    async with AsyncSessionLocal() as session:
        alert = Alert(
            severity="LOW",
            confidence=0.5,
            duration=0.5,
            location="Historical Classroom",
            language="unknown",
        )
        session.add(alert)
        await session.commit()
        await session.refresh(alert)
        alert_id = alert.id

    response = await client.get(
        f"/alerts/{alert_id}",
        headers=auth_headers(identities["admin"]),
    )

    assert response.status_code == 200
    assert response.json()["device_id"] is None
    assert response.json()["device_code"] is None
    assert response.json()["device_display_name"] is None
    assert response.json()["classroom_name"] is None
    assert response.json()["school_name"] is None


@pytest.mark.asyncio
async def test_device_management_is_admin_only(client, identities):
    unauthenticated = await client.post("/devices", json=_device_payload())
    staff_create = await client.post(
        "/devices",
        json=_device_payload(),
        headers=auth_headers(identities["staff"]),
    )
    staff_list = await client.get(
        "/devices",
        headers=auth_headers(identities["staff"]),
    )

    assert unauthenticated.status_code == 401
    assert staff_create.status_code == staff_list.status_code == 403


@pytest.mark.asyncio
async def test_creation_returns_key_once_and_never_exposes_hash(
    client,
    identities,
):
    created = await client.post(
        "/devices",
        json=_device_payload(),
        headers=auth_headers(identities["admin"]),
    )

    assert created.status_code == 201
    body = created.json()
    device_key = body["device_key"]
    device_id = body["device"]["id"]
    assert device_key.startswith("edk_")
    assert body["warning"] == "Store this key securely. It will not be shown again."
    assert "api_key_hash" not in created.text

    listed = await client.get(
        "/devices",
        headers=auth_headers(identities["admin"]),
    )
    detail = await client.get(
        f"/devices/{device_id}",
        headers=auth_headers(identities["admin"]),
    )
    assert listed.status_code == detail.status_code == 200
    assert "device_key" not in listed.text
    assert "api_key_hash" not in listed.text
    assert "device_key" not in detail.text
    assert "api_key_hash" not in detail.text

    async with AsyncSessionLocal() as session:
        device = await session.get(EdgeDevice, UUID(device_id))
        assert device.api_key_hash != device_key
        assert verify_device_key(device_key, device.api_key_hash)


@pytest.mark.asyncio
async def test_admin_can_update_assignment_and_disable_or_enable_device(
    client,
    identities,
):
    admin_headers = auth_headers(identities["admin"])
    created = await client.post(
        "/devices",
        json=_device_payload("classroom-toggle-pi"),
        headers=admin_headers,
    )
    device_id = created.json()["device"]["id"]

    disabled = await client.post(
        f"/devices/{device_id}/disable",
        headers=admin_headers,
    )
    enabled = await client.post(
        f"/devices/{device_id}/enable",
        headers=admin_headers,
    )

    assert disabled.status_code == enabled.status_code == 200
    assert disabled.json()["classroom_name"] == "Synthetic Test Classroom"
    assert disabled.json()["school_name"] == "Synthetic Test School"
    assert disabled.json()["is_active"] is False
    assert enabled.json()["is_active"] is True


@pytest.mark.asyncio
async def test_key_rotation_invalidates_old_key_and_new_key_authenticates(
    client,
    unauthenticated_edge_client,
    identities,
):
    admin_headers = auth_headers(identities["admin"])
    created = await client.post(
        "/devices",
        json=_device_payload("classroom-rotation-pi"),
        headers=admin_headers,
    )
    old_key = created.json()["device_key"]
    device = created.json()["device"]

    rotated = await client.post(
        f"/devices/{device['id']}/rotate-key",
        headers=admin_headers,
    )
    new_key = rotated.json()["device_key"]

    old_response = await unauthenticated_edge_client.post(
        "/alerts/",
        json=_alert_payload(device_identifier=device["device_code"]),
        headers={
            "X-EchoSense-Device-Id": device["device_code"],
            "X-EchoSense-Device-Key": old_key,
        },
    )
    new_response = await unauthenticated_edge_client.post(
        "/alerts/",
        json=_alert_payload(device_identifier=device["device_code"]),
        headers={
            "X-EchoSense-Device-Id": device["device_code"],
            "X-EchoSense-Device-Key": new_key,
        },
    )

    assert new_key != old_key
    assert old_response.status_code == 401
    assert new_response.status_code == 200
    assert new_response.json()["device_id"] == device["id"]


@pytest.mark.asyncio
async def test_last_seen_updates_only_after_successful_authentication(
    unauthenticated_edge_client,
    edge_device_identity,
):
    wrong_headers = {
        "X-EchoSense-Device-Id": edge_device_identity["device_code"],
        "X-EchoSense-Device-Key": "synthetic-wrong-key",
    }
    wrong = await unauthenticated_edge_client.post(
        "/alerts/",
        json=_alert_payload(),
        headers=wrong_headers,
    )
    async with AsyncSessionLocal() as session:
        after_wrong = await session.get(EdgeDevice, edge_device_identity["id"])
        assert after_wrong.last_seen_at is None

    correct = await unauthenticated_edge_client.post(
        "/alerts/",
        json=_alert_payload(),
        headers=edge_device_identity["headers"],
    )
    async with AsyncSessionLocal() as session:
        after_success = await session.get(EdgeDevice, edge_device_identity["id"])
        assert after_success.last_seen_at is not None

    assert wrong.status_code == 401
    assert correct.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("severity", ["LOW", "MEDIUM", "HIGH"])
async def test_canonical_severity_behavior_is_unchanged(client, severity):
    response = await client.post(
        "/alerts/",
        json=_alert_payload(severity=severity),
    )

    assert response.status_code == 200
    assert response.json()["severity"] == severity.lower()
    assert response.json()["severity_level"] == severity


@pytest.mark.asyncio
async def test_device_credentials_are_not_logged(
    unauthenticated_edge_client,
    edge_device_identity,
    caplog,
):
    with caplog.at_level(logging.DEBUG):
        response = await unauthenticated_edge_client.post(
            "/alerts/",
            json=_alert_payload(),
            headers=edge_device_identity["headers"],
        )

    assert response.status_code == 200
    assert edge_device_identity["device_key"] not in caplog.text


@pytest.mark.asyncio
async def test_invalid_device_key_is_absent_from_logs_and_response(
    unauthenticated_edge_client,
    edge_device_identity,
    caplog,
):
    submitted_key = "synthetic-invalid-key-that-must-remain-private"
    with caplog.at_level(logging.DEBUG):
        response = await unauthenticated_edge_client.post(
            "/alerts/",
            json=_alert_payload(),
            headers={
                "X-EchoSense-Device-Id": edge_device_identity["device_code"],
                "X-EchoSense-Device-Key": submitted_key,
            },
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid device credentials"}
    assert submitted_key not in response.text
    assert submitted_key not in caplog.text
