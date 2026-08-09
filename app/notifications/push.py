import httpx
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from app.config import settings
from app.notifications.tokens import is_structurally_valid_push_token

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
ANDROID_PROVIDER_TEST_CHANNEL_ID = "echosense-alerts"
ANDROID_PHASE3_ALERT_CHANNEL_ID = "echosense-phase3-alerts"
# Backward-compatible import name now points at the finalized Phase 3 alert channel.
ANDROID_ALERT_CHANNEL_ID = ANDROID_PHASE3_ALERT_CHANNEL_ID
ANDROID_HIGH_ALERT_CHANNEL_ID = "echosense-high-alerts"
PHASE3_TEST_TITLE = "EchoSense Alert — TEST"
PHASE3_TEST_BODY = "TEST possible verbal-aggression event. Human review required."
PROVIDER_TEST_TITLE = "EchoSense notification test"
PROVIDER_TEST_BODY = (
    "This is a controlled delivery test for the approved device. No classroom alert was created."
)
PROVIDER_TEST_ROUTE = "/notifications/test"
PROVIDER_TEST_DATA_KEYS = frozenset({"type", "test_id", "route", "severity", "is_test"})
CLASSROOM_ALERT_DATA_KEYS = frozenset(
    {
        "type",
        "alertId",
        "event_id",
        "severity",
        "severityLevel",
        "trigger_type",
        "route",
        "is_test",
    }
)
_PROVIDER_TEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

ProviderClassification = Literal[
    "accepted",
    "rejected",
    "invalid_token",
    "rate_limited",
    "temporary_failure",
    "unknown",
]


@dataclass(frozen=True)
class NotificationTemplate:
    title: str
    body: str
    priority: str


@dataclass(frozen=True)
class ProviderSubmissionResult:
    submission_timestamp: datetime
    test_id: str
    selected_recipient_count: int
    provider_http_status: int | None
    provider_classification: ProviderClassification
    provider_ticket_id_redacted: str | None
    message_count: int


@dataclass(frozen=True)
class PushBatchResult:
    status: Literal["accepted", "partial", "rejected", "failed", "skipped"]
    attempted_count: int
    accepted_count: int
    rejected_count: int
    provider_ticket_ids: tuple[str, ...] = ()
    last_error: str | None = None


NOTIFICATION_TEMPLATES = {
    "LOW": NotificationTemplate(
        title="Possible classroom concern",
        body="A low-severity unverified alert requires staff review.",
        priority="normal",
    ),
    "MEDIUM": NotificationTemplate(
        title="Possible verbal-aggression indicators",
        body="A medium-severity unverified alert requires staff review.",
        priority="normal",
    ),
    "HIGH": NotificationTemplate(
        title="High-priority classroom alert",
        body=(
            "Strong possible-aggression indicators were detected. "
            "Prompt human review is recommended."
        ),
        priority="high",
    ),
}


def notification_channel_id(severity: str) -> str:
    normalized, _ = get_notification_template(severity)
    return (
        ANDROID_HIGH_ALERT_CHANNEL_ID if normalized == "HIGH" else ANDROID_PHASE3_ALERT_CHANNEL_ID
    )


def get_notification_template(severity: str) -> tuple[str, NotificationTemplate]:
    try:
        normalized = severity.strip().upper()
        return normalized, NOTIFICATION_TEMPLATES[normalized]
    except (AttributeError, KeyError) as exc:
        raise ValueError("notification severity must be LOW, MEDIUM, or HIGH") from exc


def generate_provider_test_id() -> str:
    return f"provider-test:{uuid4()}"


def _validate_provider_test_id(test_id: str) -> str:
    if not isinstance(test_id, str) or not _PROVIDER_TEST_ID_PATTERN.fullmatch(test_id):
        raise ValueError("provider test ID has an invalid structure")
    return test_id


def build_provider_test_message(token: str, test_id: str) -> dict:
    if not is_structurally_valid_push_token(token):
        raise ValueError("provider test token is structurally invalid")
    safe_test_id = _validate_provider_test_id(test_id)
    data = {
        "type": "provider_test",
        "test_id": safe_test_id,
        "route": PROVIDER_TEST_ROUTE,
        "severity": "LOW",
        "is_test": True,
    }
    if frozenset(data) != PROVIDER_TEST_DATA_KEYS:
        raise RuntimeError("provider test payload allowlist mismatch")
    return {
        "to": token.strip(),
        "title": PROVIDER_TEST_TITLE,
        "body": PROVIDER_TEST_BODY,
        "sound": "default",
        "priority": "normal",
        "channelId": ANDROID_PROVIDER_TEST_CHANNEL_ID,
        "data": data,
    }


def provider_test_payload_preview(test_id: str) -> dict:
    safe_test_id = _validate_provider_test_id(test_id)
    return {
        "title": PROVIDER_TEST_TITLE,
        "body": PROVIDER_TEST_BODY,
        "priority": "normal",
        "channelId": ANDROID_PROVIDER_TEST_CHANNEL_ID,
        "data": {
            "type": "provider_test",
            "test_id": safe_test_id,
            "route": PROVIDER_TEST_ROUTE,
            "severity": "LOW",
            "is_test": True,
        },
    }


def _redact_provider_identifier(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if len(value) <= 8:
        return "redacted"
    return f"{value[:4]}…{value[-4:]}"


def _classification_for_http_status(status_code: int) -> ProviderClassification:
    if status_code == 429:
        return "rate_limited"
    if status_code in {408, 425} or status_code >= 500:
        return "temporary_failure"
    if status_code >= 400:
        return "rejected"
    return "unknown"


def _parse_expo_ticket(
    response_data: object,
) -> tuple[ProviderClassification, str | None]:
    if not isinstance(response_data, dict):
        return "unknown", None
    tickets = response_data.get("data")
    if not isinstance(tickets, list) or len(tickets) != 1 or not isinstance(tickets[0], dict):
        return "unknown", None

    ticket = tickets[0]
    if ticket.get("status") == "ok":
        return "accepted", _redact_provider_identifier(ticket.get("id"))
    if ticket.get("status") != "error":
        return "unknown", None

    details = ticket.get("details")
    provider_error = details.get("error") if isinstance(details, dict) else None
    if provider_error == "DeviceNotRegistered":
        return "invalid_token", None
    if provider_error == "MessageRateExceeded":
        return "rate_limited", None
    return "rejected", None


async def submit_expo_provider_test(token: str, test_id: str) -> ProviderSubmissionResult:
    message = build_provider_test_message(token, test_id)
    submitted_at = datetime.now(timezone.utc)
    try:
        async with httpx.AsyncClient() as client:
            request_kwargs = {
                "json": [message],
                "timeout": 10,
            }
            if settings.EXPO_ACCESS_TOKEN:
                request_kwargs["headers"] = {
                    "Authorization": f"Bearer {settings.EXPO_ACCESS_TOKEN}"
                }
            response = await client.post(EXPO_PUSH_URL, **request_kwargs)
            status_code = response.status_code
            if status_code < 200 or status_code >= 300:
                classification = _classification_for_http_status(status_code)
                ticket_id = None
            else:
                try:
                    response_data = response.json()
                except Exception:
                    response_data = None
                classification, ticket_id = _parse_expo_ticket(response_data)
    except httpx.TimeoutException:
        status_code = None
        classification = "temporary_failure"
        ticket_id = None
    except Exception:
        status_code = None
        classification = "unknown"
        ticket_id = None

    logger.info(
        "Expo provider-test submission classification=%s http_status=%s messages=1",
        classification,
        status_code if status_code is not None else "unavailable",
    )
    return ProviderSubmissionResult(
        submission_timestamp=submitted_at,
        test_id=test_id,
        selected_recipient_count=1,
        provider_http_status=status_code,
        provider_classification=classification,
        provider_ticket_id_redacted=ticket_id,
        message_count=1,
    )


def _unique_valid_tokens(tokens: list) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if not is_structurally_valid_push_token(token):
            continue
        normalized = token.strip()
        if normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


def build_classroom_alert_message(
    token: str,
    alert_id: int,
    severity: str,
    *,
    event_id,
    trigger_type: str,
    is_test: bool = False,
) -> dict:
    if not is_structurally_valid_push_token(token):
        raise ValueError("classroom alert token is structurally invalid")
    if not isinstance(alert_id, int) or isinstance(alert_id, bool) or alert_id <= 0:
        raise ValueError("classroom alert ID must be a positive integer")
    try:
        normalized_event_id = str(UUID(str(event_id)))
    except (TypeError, ValueError) as exc:
        raise ValueError("classroom alert event_id must be a UUID") from exc
    normalized_trigger_type = str(trigger_type or "").strip().upper()
    if normalized_trigger_type not in {"KEYWORD", "ACOUSTIC", "TEST"}:
        raise ValueError("classroom alert trigger_type is invalid")
    if (normalized_trigger_type == "TEST") != (is_test is True):
        raise ValueError("classroom alert TEST state is inconsistent")

    normalized_severity, template = get_notification_template(severity)
    data = {
        "type": "classroom_alert",
        "alertId": alert_id,
        "event_id": normalized_event_id,
        "severity": normalized_severity.lower(),
        "severityLevel": normalized_severity,
        "trigger_type": normalized_trigger_type,
        "route": f"/alert/{alert_id}",
        "is_test": is_test,
    }
    if frozenset(data) != CLASSROOM_ALERT_DATA_KEYS:
        raise RuntimeError("classroom alert payload allowlist mismatch")
    return {
        "to": token.strip(),
        "title": PHASE3_TEST_TITLE if is_test else template.title,
        "body": PHASE3_TEST_BODY if is_test else template.body,
        "sound": "default",
        "priority": template.priority,
        "channelId": notification_channel_id(normalized_severity),
        "data": data,
    }


def _parse_expo_batch_tickets(response_data: object, expected_count: int) -> PushBatchResult:
    if not isinstance(response_data, dict):
        return PushBatchResult(
            "rejected", expected_count, 0, expected_count, last_error="bad_response"
        )
    tickets = response_data.get("data")
    if not isinstance(tickets, list) or len(tickets) != expected_count:
        return PushBatchResult(
            "rejected", expected_count, 0, expected_count, last_error="bad_response"
        )

    accepted_ids: list[str] = []
    errors: list[str] = []
    for ticket in tickets:
        if not isinstance(ticket, dict):
            errors.append("bad_ticket")
            continue
        if ticket.get("status") == "ok" and isinstance(ticket.get("id"), str):
            accepted_ids.append(ticket["id"])
            continue
        details = ticket.get("details")
        provider_error = details.get("error") if isinstance(details, dict) else None
        errors.append(str(provider_error)[:100] if provider_error else "provider_rejected")

    accepted_count = len(accepted_ids)
    rejected_count = expected_count - accepted_count
    if accepted_count == expected_count:
        status = "accepted"
    elif accepted_count:
        status = "partial"
    else:
        status = "rejected"
    return PushBatchResult(
        status=status,
        attempted_count=expected_count,
        accepted_count=accepted_count,
        rejected_count=rejected_count,
        provider_ticket_ids=tuple(accepted_ids),
        last_error=errors[-1] if errors else None,
    )


async def _record_alert_push_result(alert_id: int, result: PushBatchResult) -> None:
    from app.database import AsyncSessionLocal
    from app.models.alert import Alert

    try:
        async with AsyncSessionLocal() as db:
            alert = await db.get(Alert, alert_id)
            if alert is None:
                logger.warning("Expo push status not recorded reason=AlertNotFound")
                return
            alert.push_status = result.status
            alert.push_attempt_count = (alert.push_attempt_count or 0) + (
                1 if result.attempted_count else 0
            )
            alert.push_last_error = result.last_error
            alert.push_provider_ticket_id = (
                result.provider_ticket_ids[0] if result.provider_ticket_ids else None
            )
            alert.push_submitted_at = datetime.now(timezone.utc) if result.attempted_count else None
            await db.commit()
    except Exception as exc:
        logger.warning("Expo push status persistence failed reason=%s", type(exc).__name__)


async def send_expo_pushes(
    tokens: list,
    alert_id: int,
    severity: str,
    location: str,
    *,
    event_id=None,
    trigger_type: str | None = None,
    is_test: bool = False,
    record_status: bool = False,
) -> PushBatchResult:
    del location
    valid_tokens = _unique_valid_tokens(tokens)
    if not valid_tokens:
        result = PushBatchResult("skipped", 0, 0, 0, last_error="no_valid_recipients")
        if record_status:
            await _record_alert_push_result(alert_id, result)
        return result

    messages = [
        build_classroom_alert_message(
            token,
            alert_id,
            severity,
            event_id=event_id,
            trigger_type=trigger_type,
            is_test=is_test,
        )
        for token in valid_tokens
    ]
    try:
        async with httpx.AsyncClient() as client:
            request_kwargs = {"json": messages, "timeout": 10}
            if settings.EXPO_ACCESS_TOKEN:
                request_kwargs["headers"] = {
                    "Authorization": f"Bearer {settings.EXPO_ACCESS_TOKEN}"
                }
            response = await client.post(EXPO_PUSH_URL, **request_kwargs)
            response.raise_for_status()
            try:
                response_data = response.json()
            except Exception:
                response_data = None
            result = _parse_expo_batch_tickets(response_data, len(messages))
            redacted_ticket = (
                _redact_provider_identifier(result.provider_ticket_ids[0])
                if result.provider_ticket_ids
                else None
            )
            logger.info(
                "Expo push submission status=%s accepted=%d rejected=%d ticket=%s",
                result.status,
                result.accepted_count,
                result.rejected_count,
                redacted_ticket or "none",
            )
    except Exception as exc:
        result = PushBatchResult(
            "failed",
            len(messages),
            0,
            len(messages),
            last_error=type(exc).__name__,
        )
        logger.warning("Expo push failed reason=%s", type(exc).__name__)

    if record_status:
        await _record_alert_push_result(alert_id, result)
    return result
