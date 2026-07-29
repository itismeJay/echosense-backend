import httpx
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from app.config import settings
from app.notifications.tokens import is_structurally_valid_push_token

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
ANDROID_ALERT_CHANNEL_ID = "echosense-alerts"
ANDROID_HIGH_ALERT_CHANNEL_ID = "echosense-high-alerts"
PROVIDER_TEST_TITLE = "EchoSense notification test"
PROVIDER_TEST_BODY = (
    "This is a controlled delivery test for the approved device. No classroom alert was created."
)
PROVIDER_TEST_ROUTE = "/notifications/test"
PROVIDER_TEST_DATA_KEYS = frozenset({"type", "test_id", "route", "severity", "is_test"})
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
    return ANDROID_HIGH_ALERT_CHANNEL_ID if normalized == "HIGH" else ANDROID_ALERT_CHANNEL_ID


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
        "channelId": ANDROID_ALERT_CHANNEL_ID,
        "data": data,
    }


def provider_test_payload_preview(test_id: str) -> dict:
    safe_test_id = _validate_provider_test_id(test_id)
    return {
        "title": PROVIDER_TEST_TITLE,
        "body": PROVIDER_TEST_BODY,
        "priority": "normal",
        "channelId": ANDROID_ALERT_CHANNEL_ID,
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


async def send_expo_pushes(tokens: list, alert_id: int, severity: str, location: str):
    valid_tokens = [token.strip() for token in tokens if isinstance(token, str) and token.strip()]
    if not valid_tokens:
        return
    normalized, template = get_notification_template(severity)
    messages = [
        {
            "to": token,
            "title": template.title,
            "body": template.body,
            "sound": "default",
            "priority": template.priority,
            "channelId": notification_channel_id(normalized),
            "data": {
                "alertId": alert_id,
                # Preserve the existing mobile boundary while exposing the
                # canonical representation additively.
                "severity": normalized.lower(),
                "severityLevel": normalized,
            },
        }
        for token in valid_tokens
    ]
    try:
        async with httpx.AsyncClient() as client:
            request_kwargs = {"json": messages, "timeout": 10}
            if settings.EXPO_ACCESS_TOKEN:
                request_kwargs["headers"] = {
                    "Authorization": f"Bearer {settings.EXPO_ACCESS_TOKEN}"
                }
            resp = await client.post(EXPO_PUSH_URL, **request_kwargs)
            resp.raise_for_status()
            logger.info("Expo push sent to %d token(s)", len(messages))
    except Exception as exc:
        logger.warning("Expo push failed reason=%s", type(exc).__name__)
