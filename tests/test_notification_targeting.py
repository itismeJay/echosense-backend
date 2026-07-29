import logging
from uuid import uuid4

import pytest

from app.config import Settings, settings
from app.database import AsyncSessionLocal
from app.models.user import User
from app.services.notification_recipients import (
    RecipientSelection,
    evaluate_controlled_recipient,
)
from tests.conftest import auth_headers

SYNTHETIC_TOKEN_A = "ExpoPushToken[synthetic-notification-token-a]"
SYNTHETIC_TOKEN_B = "ExpoPushToken[synthetic-notification-token-b]"
SYNTHETIC_TOKEN_C = "ExponentPushToken[synthetic-notification-token-c]"


def _alert_payload(event_id=None) -> dict:
    payload = {
        "severity": "medium",
        "confidence": 0.82,
        "duration": 1.1,
        "location": "Controlled Test Room",
        "transcribed_text": "synthetic controlled test",
        "language": "en",
        "yamnet_ran": False,
        "yamnet_class": "NotRun",
        "yamnet_score": 0.0,
    }
    if event_id is not None:
        payload["event_id"] = str(event_id)
    return payload


@pytest.fixture(autouse=True)
def controlled_test_settings(monkeypatch):
    monkeypatch.setattr(settings, "ECHOSENSE_CONTROLLED_TEST_MODE", False)
    monkeypatch.setattr(settings, "ECHOSENSE_CONTROLLED_TEST_USER_ID", None)


async def _add_users(*tokens: str | None) -> list[User]:
    async with AsyncSessionLocal() as session:
        users = [
            User(
                email=f"notification-user-{index}@example.test",
                hashed_password="synthetic-hash",
                role="admin" if index == 1 else "staff",
                push_token=token,
            )
            for index, token in enumerate(tokens, start=1)
        ]
        session.add_all(users)
        await session.commit()
        for user in users:
            await session.refresh(user)
        return users


def test_controlled_test_mode_defaults_to_disabled():
    isolated_settings = Settings(
        _env_file=None,
        DATABASE_URL="postgresql+asyncpg://local.invalid/test",
        SECRET_KEY="synthetic-test-secret",
    )

    assert isolated_settings.ECHOSENSE_CONTROLLED_TEST_MODE is False
    assert isolated_settings.ECHOSENSE_CONTROLLED_TEST_USER_ID is None


@pytest.mark.asyncio
async def test_disabled_mode_preserves_broadcast_selection(
    client,
    prevent_external_notifications,
    caplog,
):
    await _add_users(SYNTHETIC_TOKEN_A, None, SYNTHETIC_TOKEN_B)

    with caplog.at_level(logging.INFO, logger="app.services.notification_recipients"):
        response = await client.post("/alerts/", json=_alert_payload())

    assert response.status_code == 200
    prevent_external_notifications.assert_awaited_once()
    selected_tokens = prevent_external_notifications.await_args.args[0]
    assert set(selected_tokens) == {SYNTHETIC_TOKEN_A, SYNTHETIC_TOKEN_B}
    assert "[NOTIFICATION] mode=normal recipients=2" in caplog.text


@pytest.mark.asyncio
async def test_enabled_mode_requires_configured_user_and_never_broadcasts(
    client,
    prevent_external_notifications,
    monkeypatch,
    caplog,
):
    await _add_users(SYNTHETIC_TOKEN_A, SYNTHETIC_TOKEN_B)
    monkeypatch.setattr(settings, "ECHOSENSE_CONTROLLED_TEST_MODE", True)

    with caplog.at_level(logging.ERROR, logger="app.services.notification_recipients"):
        response = await client.post("/alerts/", json=_alert_payload())

    assert response.status_code == 200
    prevent_external_notifications.assert_not_awaited()
    assert "reason=invalid_user_id" in caplog.text
    assert SYNTHETIC_TOKEN_A not in caplog.text
    assert SYNTHETIC_TOKEN_B not in caplog.text


@pytest.mark.asyncio
async def test_enabled_mode_selects_only_configured_user(
    client,
    prevent_external_notifications,
    monkeypatch,
    caplog,
):
    users = await _add_users(SYNTHETIC_TOKEN_A, SYNTHETIC_TOKEN_B, SYNTHETIC_TOKEN_C)
    monkeypatch.setattr(settings, "ECHOSENSE_CONTROLLED_TEST_MODE", True)
    monkeypatch.setattr(settings, "ECHOSENSE_CONTROLLED_TEST_USER_ID", users[1].id)

    with caplog.at_level(logging.INFO, logger="app.services.notification_recipients"):
        response = await client.post("/alerts/", json=_alert_payload())

    assert response.status_code == 200
    prevent_external_notifications.assert_awaited_once()
    assert prevent_external_notifications.await_args.args[0] == [SYNTHETIC_TOKEN_B]
    assert "[NOTIFICATION] mode=controlled_test recipients=1" in caplog.text
    assert SYNTHETIC_TOKEN_B not in caplog.text


@pytest.mark.asyncio
async def test_enabled_mode_fails_closed_for_missing_user(
    client,
    prevent_external_notifications,
    monkeypatch,
):
    await _add_users(SYNTHETIC_TOKEN_A)
    monkeypatch.setattr(settings, "ECHOSENSE_CONTROLLED_TEST_MODE", True)
    monkeypatch.setattr(settings, "ECHOSENSE_CONTROLLED_TEST_USER_ID", 999_999)

    response = await client.post("/alerts/", json=_alert_payload())

    assert response.status_code == 200
    prevent_external_notifications.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_token", [None, "", "   "])
async def test_enabled_mode_fails_closed_for_user_without_push_token(
    client,
    prevent_external_notifications,
    monkeypatch,
    caplog,
    missing_token,
):
    users = await _add_users(missing_token, SYNTHETIC_TOKEN_A)
    monkeypatch.setattr(settings, "ECHOSENSE_CONTROLLED_TEST_MODE", True)
    monkeypatch.setattr(settings, "ECHOSENSE_CONTROLLED_TEST_USER_ID", users[0].id)

    with caplog.at_level(logging.ERROR, logger="app.services.notification_recipients"):
        response = await client.post("/alerts/", json=_alert_payload())

    assert response.status_code == 200
    prevent_external_notifications.assert_not_awaited()
    assert "reason=push_token_unavailable" in caplog.text
    assert SYNTHETIC_TOKEN_A not in caplog.text


@pytest.mark.asyncio
async def test_enabled_mode_fails_closed_for_malformed_user_identifier(
    client,
    prevent_external_notifications,
    monkeypatch,
):
    await _add_users(SYNTHETIC_TOKEN_A)
    monkeypatch.setattr(settings, "ECHOSENSE_CONTROLLED_TEST_MODE", True)
    monkeypatch.setattr(
        settings,
        "ECHOSENSE_CONTROLLED_TEST_USER_ID",
        "not-an-integer",
    )

    response = await client.post("/alerts/", json=_alert_payload())

    assert response.status_code == 200
    prevent_external_notifications.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_audit_reports_one_recipient_without_exposing_token(
    client,
    identities,
    monkeypatch,
):
    async with AsyncSessionLocal() as session:
        admin = await session.get(User, identities["admin"]["id"])
        admin.push_token = SYNTHETIC_TOKEN_A
        await session.commit()

    monkeypatch.setattr(settings, "ECHOSENSE_CONTROLLED_TEST_MODE", True)
    monkeypatch.setattr(
        settings,
        "ECHOSENSE_CONTROLLED_TEST_USER_ID",
        identities["admin"]["id"],
    )

    response = await client.get(
        "/users/notification-recipient-audit",
        headers=auth_headers(identities["admin"]),
    )

    assert response.status_code == 200
    assert response.json() == {
        "controlled_test_mode": True,
        "configured_user_reference_present": True,
        "configured_recipient_resolved": True,
        "selected_recipient_count": 1,
        "eligible_recipient_count": 1,
        "recipient_identifier_masked": "user_id:configured",
        "recipient_internal_id": f"user_id:{identities['admin']['id']}",
        "masked_email": "a***n@school.test",
        "role": "admin",
        "account_active_status": "not_recorded",
        "has_push_token": True,
        "token_structurally_valid": True,
        "token_provider": "expo",
        "token_duplicate_count": 0,
        "token_last_updated": "not_recorded",
        "token_stale_status": "not_recorded",
        "selected_recipient_source": "controlled_user",
        "broadcast_risk": False,
        "failure_reason": None,
    }
    assert SYNTHETIC_TOKEN_A not in response.text


@pytest.mark.asyncio
async def test_recipient_audit_requires_admin(client, identities):
    unauthenticated = await client.get("/users/notification-recipient-audit")
    staff = await client.get(
        "/users/notification-recipient-audit",
        headers=auth_headers(identities["staff"]),
    )

    assert unauthenticated.status_code == 401
    assert staff.status_code == 403


@pytest.mark.asyncio
async def test_recipient_audit_fails_when_count_is_zero(
    client,
    identities,
    monkeypatch,
):
    monkeypatch.setattr(settings, "ECHOSENSE_CONTROLLED_TEST_MODE", True)
    monkeypatch.setattr(settings, "ECHOSENSE_CONTROLLED_TEST_USER_ID", 999_999)

    response = await client.get(
        "/users/notification-recipient-audit",
        headers=auth_headers(identities["admin"]),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["eligible_recipient_count"] == 0
    assert response.json()["detail"]["failure_reason"] == "user_not_found"
    assert SYNTHETIC_TOKEN_A not in response.text


@pytest.mark.asyncio
async def test_recipient_audit_fails_when_resolution_is_ambiguous(
    client,
    identities,
    monkeypatch,
):
    selection = RecipientSelection(
        controlled_test_mode=True,
        tokens=(),
        configured_recipient_resolved=False,
        recipient_identifier_masked="user_id:configured",
        has_push_token=False,
        failure_reason="multiple_users",
    )

    async def ambiguous_selection(*args, **kwargs):
        return selection

    monkeypatch.setattr(settings, "ECHOSENSE_CONTROLLED_TEST_MODE", True)
    monkeypatch.setattr(
        "app.routers.users.resolve_notification_recipients",
        ambiguous_selection,
    )

    response = await client.get(
        "/users/notification-recipient-audit",
        headers=auth_headers(identities["admin"]),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["failure_reason"] == "multiple_users"


def test_controlled_recipient_evaluation_rejects_multiple_matches():
    users = [
        User(id=42, push_token=SYNTHETIC_TOKEN_A),
        User(id=42, push_token=SYNTHETIC_TOKEN_B),
    ]

    selection = evaluate_controlled_recipient(users, 42)

    assert selection.eligible_recipient_count == 0
    assert selection.failure_reason == "multiple_users"


@pytest.mark.asyncio
async def test_duplicate_event_does_not_schedule_second_controlled_notification(
    client,
    prevent_external_notifications,
    monkeypatch,
):
    users = await _add_users(SYNTHETIC_TOKEN_A, SYNTHETIC_TOKEN_B)
    monkeypatch.setattr(settings, "ECHOSENSE_CONTROLLED_TEST_MODE", True)
    monkeypatch.setattr(settings, "ECHOSENSE_CONTROLLED_TEST_USER_ID", users[0].id)
    event_id = uuid4()

    first = await client.post("/alerts/", json=_alert_payload(event_id))
    duplicate = await client.post("/alerts/", json=_alert_payload(event_id))

    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert duplicate.json()["id"] == first.json()["id"]
    prevent_external_notifications.assert_awaited_once()
