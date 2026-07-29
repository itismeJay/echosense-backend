import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import func, select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.alert import Alert
from app.models.user import User
from app.notifications.push import (
    ANDROID_ALERT_CHANNEL_ID,
    ANDROID_HIGH_ALERT_CHANNEL_ID,
    PROVIDER_TEST_BODY,
    PROVIDER_TEST_DATA_KEYS,
    PROVIDER_TEST_ROUTE,
    PROVIDER_TEST_TITLE,
    ProviderSubmissionResult,
    build_provider_test_message,
    notification_channel_id,
    submit_expo_provider_test,
)
from app.notifications.tokens import (
    is_structurally_valid_push_token,
    normalize_push_token,
    push_token_provider,
)
from app.services.notification_recipients import (
    RecipientSelection,
    evaluate_controlled_recipient,
)
from app.services.provider_test import (
    ProviderTestGateError,
    _clear_provider_test_dry_runs_for_testing,
    create_provider_test_dry_run,
)
from tests.conftest import auth_headers

VALID_EXPO_TOKEN = "ExpoPushToken[provider-test-device]"
VALID_LEGACY_EXPO_TOKEN = "ExponentPushToken[provider-test-device]"


@pytest.fixture(autouse=True)
def provider_test_state(monkeypatch):
    _clear_provider_test_dry_runs_for_testing()
    monkeypatch.setattr(settings, "ECHOSENSE_CONTROLLED_TEST_MODE", False)
    monkeypatch.setattr(settings, "ECHOSENSE_CONTROLLED_TEST_USER_ID", None)
    monkeypatch.setattr(settings, "EXPO_ACCESS_TOKEN", None)
    yield
    _clear_provider_test_dry_runs_for_testing()


async def _configure_controlled_admin(identities, monkeypatch, token=VALID_EXPO_TOKEN) -> int:
    user_id = identities["admin"]["id"]
    async with AsyncSessionLocal() as session:
        admin = await session.get(User, user_id)
        admin.push_token = token
        await session.commit()
    monkeypatch.setattr(settings, "ECHOSENSE_CONTROLLED_TEST_MODE", True)
    monkeypatch.setattr(settings, "ECHOSENSE_CONTROLLED_TEST_USER_ID", user_id)
    return user_id


async def _dry_run(client, identities, user_id: int, *, physical=True):
    return await client.post(
        "/users/provider-test/dry-run",
        headers=auth_headers(identities["admin"]),
        json={
            "confirmed_recipient_user_id": user_id,
            "physical_device_confirmed": physical,
        },
    )


def _provider_result(test_id: str, classification="accepted") -> ProviderSubmissionResult:
    return ProviderSubmissionResult(
        submission_timestamp=datetime.now(timezone.utc),
        test_id=test_id,
        selected_recipient_count=1,
        provider_http_status=200,
        provider_classification=classification,
        provider_ticket_id_redacted="tick…cdef" if classification == "accepted" else None,
        message_count=1,
    )


@pytest.mark.parametrize(
    ("token", "normalized", "provider", "valid"),
    [
        (VALID_EXPO_TOKEN, VALID_EXPO_TOKEN, "expo", True),
        (VALID_LEGACY_EXPO_TOKEN, VALID_LEGACY_EXPO_TOKEN, "expo", True),
        (None, None, "not_recorded", False),
        ("", None, "not_recorded", False),
        ("   ", None, "not_recorded", False),
        ("PushToken[malformed]", "PushToken[malformed]", "unknown", False),
        ("ExpoPushToken[has whitespace]", "ExpoPushToken[has whitespace]", "unknown", False),
    ],
)
def test_push_token_structural_validation(token, normalized, provider, valid):
    assert normalize_push_token(token) == normalized
    assert push_token_provider(token) == provider
    assert is_structurally_valid_push_token(token) is valid


@pytest.mark.asyncio
async def test_recipient_audit_exposes_safe_comparable_identity(client, identities, monkeypatch):
    user_id = await _configure_controlled_admin(identities, monkeypatch)

    response = await client.get(
        "/users/notification-recipient-audit",
        headers=auth_headers(identities["admin"]),
    )

    assert response.status_code == 200
    audit = response.json()
    assert audit["controlled_test_mode"] is True
    assert audit["configured_user_reference_present"] is True
    assert audit["selected_recipient_count"] == 1
    assert audit["recipient_internal_id"] == f"user_id:{user_id}"
    assert audit["masked_email"] == "a***n@school.test"
    assert audit["role"] == "admin"
    assert audit["account_active_status"] == "not_recorded"
    assert audit["has_push_token"] is True
    assert audit["token_structurally_valid"] is True
    assert audit["token_provider"] == "expo"
    assert audit["token_duplicate_count"] == 0
    assert audit["token_last_updated"] == "not_recorded"
    assert audit["token_stale_status"] == "not_recorded"
    assert audit["selected_recipient_source"] == "controlled_user"
    assert audit["broadcast_risk"] is False
    assert VALID_EXPO_TOKEN not in response.text


@pytest.mark.asyncio
async def test_recipient_audit_reports_disabled_mode_broadcast_risk(
    client,
    identities,
):
    async with AsyncSessionLocal() as session:
        session.add(
            User(
                email="broadcast-risk@example.test",
                hashed_password="synthetic",
                role="staff",
                push_token=VALID_EXPO_TOKEN,
            )
        )
        await session.commit()

    response = await client.get(
        "/users/notification-recipient-audit",
        headers=auth_headers(identities["admin"]),
    )

    assert response.status_code == 200
    audit = response.json()
    assert audit["controlled_test_mode"] is False
    assert audit["selected_recipient_source"] == "broadcast"
    assert audit["broadcast_risk"] is True
    assert audit["selected_recipient_count"] == 1
    assert VALID_EXPO_TOKEN not in response.text


@pytest.mark.asyncio
async def test_recipient_audit_and_provider_test_paths_are_admin_only(client, identities):
    paths = [
        ("/users/notification-recipient-audit", "get", None),
        (
            "/users/provider-test/dry-run",
            "post",
            {
                "confirmed_recipient_user_id": identities["admin"]["id"],
                "physical_device_confirmed": True,
            },
        ),
        (
            "/users/provider-test/send",
            "post",
            {
                "confirmed_recipient_user_id": identities["admin"]["id"],
                "physical_device_confirmed": True,
                "approve_single_send": True,
                "test_id": "provider-test:unauthorized",
            },
        ),
    ]
    for path, method, body in paths:
        request = getattr(client, method)
        unauthenticated = await request(path, json=body) if body else await request(path)
        staff = (
            await request(path, json=body, headers=auth_headers(identities["staff"]))
            if body
            else await request(path, headers=auth_headers(identities["staff"]))
        )
        assert unauthenticated.status_code == 401
        assert staff.status_code == 403


@pytest.mark.asyncio
async def test_duplicate_token_is_reported_and_blocks_dry_run(
    client,
    identities,
    monkeypatch,
):
    user_id = await _configure_controlled_admin(identities, monkeypatch)
    async with AsyncSessionLocal() as session:
        session.add(
            User(
                email="duplicate-token@example.test",
                hashed_password="synthetic",
                role="staff",
                push_token=f"  {VALID_EXPO_TOKEN}  ",
            )
        )
        await session.commit()

    audit = await client.get(
        "/users/notification-recipient-audit",
        headers=auth_headers(identities["admin"]),
    )
    dry_run = await _dry_run(client, identities, user_id)

    assert audit.status_code == 409
    assert audit.json()["detail"]["token_duplicate_count"] == 1
    assert audit.json()["detail"]["failure_reason"] == "duplicate_push_token"
    assert dry_run.status_code == 409
    assert dry_run.json()["detail"]["reason"] == "duplicate_push_token"
    assert VALID_EXPO_TOKEN not in audit.text
    assert VALID_EXPO_TOKEN not in dry_run.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("token", "expected_reason"),
    [
        (None, "push_token_required"),
        ("", "push_token_required"),
        ("  ", "push_token_required"),
        ("PushToken[malformed]", "invalid_push_token"),
    ],
)
async def test_missing_or_invalid_token_blocks_dry_run(
    client,
    identities,
    monkeypatch,
    token,
    expected_reason,
):
    user_id = await _configure_controlled_admin(identities, monkeypatch, token)

    response = await _dry_run(client, identities, user_id)

    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == expected_reason


def test_inactive_controlled_recipient_fails_closed():
    user = User(
        id=42,
        email="inactive@example.test",
        role="staff",
        push_token=VALID_EXPO_TOKEN,
    )
    user.is_active = False

    selection = evaluate_controlled_recipient([user], 42)

    assert selection.selected_recipient_count == 1
    assert selection.eligible_recipient_count == 0
    assert selection.account_active_status == "inactive"
    assert selection.failure_reason == "inactive_user"
    with pytest.raises(ProviderTestGateError, match="inactive_recipient"):
        create_provider_test_dry_run(
            selection,
            confirmed_recipient_user_id=42,
            physical_device_confirmed=True,
        )


def test_multiple_selected_recipients_fail_provider_test_gate():
    selection = RecipientSelection(
        controlled_test_mode=True,
        tokens=(VALID_EXPO_TOKEN,),
        configured_recipient_resolved=False,
        recipient_identifier_masked="user_id:configured",
        has_push_token=True,
        configured_user_reference_present=True,
        selected_recipient_count=2,
        token_structurally_valid=True,
        token_provider="expo",
        selected_recipient_source="controlled_user",
    )

    with pytest.raises(ProviderTestGateError, match="exactly_one_recipient_required"):
        create_provider_test_dry_run(
            selection,
            confirmed_recipient_user_id=42,
            physical_device_confirmed=True,
        )


def test_provider_test_payload_matches_mobile_contract_exactly():
    test_id = "provider-test:1234"
    message = build_provider_test_message(VALID_EXPO_TOKEN, test_id)

    assert message == {
        "to": VALID_EXPO_TOKEN,
        "title": PROVIDER_TEST_TITLE,
        "body": PROVIDER_TEST_BODY,
        "sound": "default",
        "priority": "normal",
        "channelId": "echosense-alerts",
        "data": {
            "type": "provider_test",
            "test_id": test_id,
            "route": "/notifications/test",
            "severity": "LOW",
            "is_test": True,
        },
    }
    assert frozenset(message["data"]) == PROVIDER_TEST_DATA_KEYS
    assert message["data"]["route"] == PROVIDER_TEST_ROUTE
    prohibited = {
        "alertId",
        "event_id",
        "transcript",
        "transcribed_text",
        "matched_terms",
        "categories",
        "waveform_snapshot",
        "raw_audio",
        "audio",
        "student",
        "speaker",
        "room",
    }
    assert prohibited.isdisjoint(message["data"])


@pytest.mark.parametrize(
    ("severity", "expected_channel"),
    [
        ("LOW", ANDROID_ALERT_CHANNEL_ID),
        ("MEDIUM", ANDROID_ALERT_CHANNEL_ID),
        ("HIGH", ANDROID_HIGH_ALERT_CHANNEL_ID),
    ],
)
def test_classroom_notification_channel_selection(severity, expected_channel):
    assert notification_channel_id(severity) == expected_channel


@pytest.mark.asyncio
async def test_dry_run_is_redacted_and_performs_no_provider_request(
    client,
    identities,
    monkeypatch,
):
    user_id = await _configure_controlled_admin(identities, monkeypatch)
    provider = AsyncMock()
    monkeypatch.setattr("app.routers.users.submit_expo_provider_test", provider)

    response = await _dry_run(client, identities, user_id)

    assert response.status_code == 200
    dry_run = response.json()
    assert dry_run["controlled_test_mode"] is True
    assert dry_run["recipient_internal_id"] == f"user_id:{user_id}"
    assert dry_run["masked_email"] == "a***n@school.test"
    assert dry_run["role"] == "admin"
    assert dry_run["token_present"] is True
    assert dry_run["token_structurally_valid"] is True
    assert dry_run["token_duplicate_count"] == 0
    assert dry_run["recipient_count"] == 1
    assert dry_run["payload"]["title"] == PROVIDER_TEST_TITLE
    assert dry_run["payload"]["body"] == PROVIDER_TEST_BODY
    assert dry_run["payload"]["channelId"] == ANDROID_ALERT_CHANNEL_ID
    assert set(dry_run["payload_data_keys"]) == PROVIDER_TEST_DATA_KEYS
    assert dry_run["expected_provider_submissions"] == 1
    assert dry_run["expected_recipients"] == 1
    assert dry_run["expected_alert_rows"] == 0
    assert dry_run["expected_event_ids"] == 0
    assert dry_run["expected_classroom_analytics_writes"] == 0
    assert VALID_EXPO_TOKEN not in response.text
    provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_dry_run_requires_controlled_mode(client, identities):
    response = await _dry_run(client, identities, identities["admin"]["id"])

    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "controlled_mode_required"


@pytest.mark.asyncio
async def test_dry_run_requires_resolved_recipient(client, identities, monkeypatch):
    monkeypatch.setattr(settings, "ECHOSENSE_CONTROLLED_TEST_MODE", True)
    monkeypatch.setattr(settings, "ECHOSENSE_CONTROLLED_TEST_USER_ID", 999_999)

    response = await _dry_run(client, identities, identities["admin"]["id"])

    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "exactly_one_recipient_required"


@pytest.mark.asyncio
async def test_dry_run_requires_confirmed_identity_and_physical_device(
    client,
    identities,
    monkeypatch,
):
    user_id = await _configure_controlled_admin(identities, monkeypatch)

    identity_mismatch = await _dry_run(client, identities, user_id + 1)
    physical_missing = await _dry_run(client, identities, user_id, physical=False)

    assert identity_mismatch.status_code == 409
    assert identity_mismatch.json()["detail"]["reason"] == "recipient_identity_mismatch"
    assert physical_missing.status_code == 409
    assert physical_missing.json()["detail"]["reason"] == "physical_device_confirmation_required"


@pytest.mark.asyncio
async def test_send_requires_explicit_approval_and_valid_dry_run(
    client,
    identities,
    monkeypatch,
):
    user_id = await _configure_controlled_admin(identities, monkeypatch)
    dry_run = await _dry_run(client, identities, user_id)
    test_id = dry_run.json()["test_id"]
    provider = AsyncMock(return_value=_provider_result(test_id))
    monkeypatch.setattr("app.routers.users.submit_expo_provider_test", provider)

    no_approval = await client.post(
        "/users/provider-test/send",
        headers=auth_headers(identities["admin"]),
        json={
            "test_id": test_id,
            "confirmed_recipient_user_id": user_id,
            "physical_device_confirmed": True,
            "approve_single_send": False,
        },
    )
    no_dry_run = await client.post(
        "/users/provider-test/send",
        headers=auth_headers(identities["admin"]),
        json={
            "test_id": "provider-test:not-prepared",
            "confirmed_recipient_user_id": user_id,
            "physical_device_confirmed": True,
            "approve_single_send": True,
        },
    )

    assert no_approval.status_code == 409
    assert no_approval.json()["detail"]["reason"] == "explicit_one_send_approval_required"
    assert no_dry_run.status_code == 409
    assert no_dry_run.json()["detail"]["reason"] == "valid_dry_run_required"
    provider.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("gate", "expected_reason"),
    [
        ("controlled_mode", "controlled_mode_required"),
        ("invalid_token", "invalid_push_token"),
        ("identity", "recipient_identity_mismatch"),
        ("physical_device", "physical_device_confirmation_required"),
        ("multiple_recipients", "exactly_one_recipient_required"),
    ],
)
async def test_send_rechecks_every_safety_gate_after_dry_run(
    client,
    identities,
    monkeypatch,
    gate,
    expected_reason,
):
    user_id = await _configure_controlled_admin(identities, monkeypatch)
    dry_run = await _dry_run(client, identities, user_id)
    test_id = dry_run.json()["test_id"]
    provider = AsyncMock(return_value=_provider_result(test_id))
    monkeypatch.setattr("app.routers.users.submit_expo_provider_test", provider)
    confirmed_user_id = user_id
    physical_device_confirmed = True

    if gate == "controlled_mode":
        monkeypatch.setattr(settings, "ECHOSENSE_CONTROLLED_TEST_MODE", False)
    elif gate == "invalid_token":
        async with AsyncSessionLocal() as session:
            admin = await session.get(User, user_id)
            admin.push_token = "PushToken[malformed]"
            await session.commit()
    elif gate == "identity":
        confirmed_user_id = user_id + 1
    elif gate == "physical_device":
        physical_device_confirmed = False
    else:
        selection = RecipientSelection(
            controlled_test_mode=True,
            tokens=(VALID_EXPO_TOKEN,),
            configured_recipient_resolved=False,
            recipient_identifier_masked="user_id:configured",
            has_push_token=True,
            configured_user_reference_present=True,
            selected_recipient_count=2,
            token_structurally_valid=True,
            token_provider="expo",
            selected_recipient_source="controlled_user",
        )

        async def multiple_selection(*args, **kwargs):
            return selection

        monkeypatch.setattr(
            "app.routers.users.resolve_notification_recipients",
            multiple_selection,
        )

    response = await client.post(
        "/users/provider-test/send",
        headers=auth_headers(identities["admin"]),
        json={
            "test_id": test_id,
            "confirmed_recipient_user_id": confirmed_user_id,
            "physical_device_confirmed": physical_device_confirmed,
            "approve_single_send": True,
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == expected_reason
    provider.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "classification",
    ["accepted", "rejected", "temporary_failure"],
)
async def test_provider_result_never_creates_alert_or_changes_analytics(
    client,
    identities,
    monkeypatch,
    classification,
):
    user_id = await _configure_controlled_admin(identities, monkeypatch)
    dry_run = await _dry_run(client, identities, user_id)
    test_id = dry_run.json()["test_id"]
    provider = AsyncMock(return_value=_provider_result(test_id, classification))
    monkeypatch.setattr("app.routers.users.submit_expo_provider_test", provider)

    before_analytics = await client.get(
        "/alerts/analytics/summary",
        headers=auth_headers(identities["admin"]),
    )
    async with AsyncSessionLocal() as session:
        before_count = await session.scalar(select(func.count(Alert.id)))

    response = await client.post(
        "/users/provider-test/send",
        headers=auth_headers(identities["admin"]),
        json={
            "test_id": test_id,
            "confirmed_recipient_user_id": user_id,
            "physical_device_confirmed": True,
            "approve_single_send": True,
        },
    )

    async with AsyncSessionLocal() as session:
        after_count = await session.scalar(select(func.count(Alert.id)))
        event_id_count = await session.scalar(
            select(func.count(Alert.id)).where(Alert.event_id.isnot(None))
        )
    after_analytics = await client.get(
        "/alerts/analytics/summary",
        headers=auth_headers(identities["admin"]),
    )

    assert response.status_code == 200
    assert response.json()["provider_classification"] == classification
    assert response.json()["message_count"] == 1
    assert before_count == after_count == 0
    assert event_id_count == 0
    assert before_analytics.json() == after_analytics.json()
    provider.assert_awaited_once_with(VALID_EXPO_TOKEN, test_id)
    assert VALID_EXPO_TOKEN not in response.text


@pytest.mark.asyncio
async def test_consumed_dry_run_cannot_send_twice(
    client,
    identities,
    monkeypatch,
):
    user_id = await _configure_controlled_admin(identities, monkeypatch)
    dry_run = await _dry_run(client, identities, user_id)
    test_id = dry_run.json()["test_id"]
    provider = AsyncMock(return_value=_provider_result(test_id))
    monkeypatch.setattr("app.routers.users.submit_expo_provider_test", provider)
    request = {
        "test_id": test_id,
        "confirmed_recipient_user_id": user_id,
        "physical_device_confirmed": True,
        "approve_single_send": True,
    }

    first = await client.post(
        "/users/provider-test/send",
        headers=auth_headers(identities["admin"]),
        json=request,
    )
    duplicate = await client.post(
        "/users/provider-test/send",
        headers=auth_headers(identities["admin"]),
        json=request,
    )

    assert first.status_code == 200
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["reason"] == "valid_dry_run_required"
    provider.assert_awaited_once()


@pytest.mark.asyncio
async def test_provider_acceptance_parses_and_redacts_ticket(monkeypatch):
    captured = {}

    class Response:
        status_code = 200

        def json(self):
            return {"data": [{"status": "ok", "id": "ticket-sensitive-abcdef"}]}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            captured.update(url=url, **kwargs)
            return Response()

    monkeypatch.setattr("app.notifications.push.httpx.AsyncClient", FakeClient)

    result = await submit_expo_provider_test(VALID_EXPO_TOKEN, "provider-test:accepted")

    assert result.provider_classification == "accepted"
    assert result.provider_http_status == 200
    assert result.provider_ticket_id_redacted == "tick…cdef"
    assert captured["json"][0]["data"]["type"] == "provider_test"
    assert len(captured["json"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "ticket", "expected"),
    [
        (429, None, "rate_limited"),
        (503, None, "temporary_failure"),
        (400, None, "rejected"),
        (
            200,
            {
                "data": [
                    {
                        "status": "error",
                        "details": {"error": "DeviceNotRegistered"},
                    }
                ]
            },
            "invalid_token",
        ),
        (
            200,
            {
                "data": [
                    {
                        "status": "error",
                        "details": {"error": "MessageRateExceeded"},
                    }
                ]
            },
            "rate_limited",
        ),
        (200, {"data": [{"status": "error", "message": "rejected"}]}, "rejected"),
        (200, {"unexpected": "shape"}, "unknown"),
    ],
)
async def test_provider_response_is_classified_without_returning_raw_body(
    monkeypatch,
    status_code,
    ticket,
    expected,
):
    class Response:
        def __init__(self):
            self.status_code = status_code

        def json(self):
            return ticket

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            return Response()

    monkeypatch.setattr("app.notifications.push.httpx.AsyncClient", FakeClient)

    result = await submit_expo_provider_test(VALID_EXPO_TOKEN, "provider-test:classified")

    assert result.provider_classification == expected
    assert result.provider_http_status == status_code
    assert result.provider_ticket_id_redacted is None
    assert not hasattr(result, "provider_response")


@pytest.mark.asyncio
async def test_provider_timeout_is_not_retried_or_logged_with_token(
    monkeypatch,
    caplog,
):
    calls = 0

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            nonlocal calls
            calls += 1
            raise httpx.ReadTimeout(f"timeout for {VALID_EXPO_TOKEN}")

    monkeypatch.setattr("app.notifications.push.httpx.AsyncClient", FakeClient)

    with caplog.at_level(logging.INFO, logger="app.notifications.push"):
        result = await submit_expo_provider_test(
            VALID_EXPO_TOKEN,
            "provider-test:timeout",
        )

    assert result.provider_classification == "temporary_failure"
    assert result.provider_http_status is None
    assert calls == 1
    assert VALID_EXPO_TOKEN not in caplog.text
