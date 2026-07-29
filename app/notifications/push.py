import httpx
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


@dataclass(frozen=True)
class NotificationTemplate:
    title: str
    body: str
    priority: str


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


def get_notification_template(severity: str) -> tuple[str, NotificationTemplate]:
    try:
        normalized = severity.strip().upper()
        return normalized, NOTIFICATION_TEMPLATES[normalized]
    except (AttributeError, KeyError) as exc:
        raise ValueError("notification severity must be LOW, MEDIUM, or HIGH") from exc


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
            resp = await client.post(EXPO_PUSH_URL, json=messages, timeout=10)
            resp.raise_for_status()
            logger.info("Expo push sent to %d token(s)", len(tokens))
    except Exception as exc:
        logger.warning("Expo push failed reason=%s", type(exc).__name__)
