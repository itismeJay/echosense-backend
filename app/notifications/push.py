import httpx
import logging

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
PUSH_TITLE = "Possible aggression alert"
PUSH_BODY = "Unverified possible-aggression alert. Human review required."


async def send_expo_pushes(tokens: list, alert_id: int, severity: str, location: str):
    if not tokens:
        return
    messages = [
        {
            "to": token,
            "title": PUSH_TITLE,
            "body": PUSH_BODY,
            "sound": "default",
            "data": {"alertId": alert_id, "severity": severity},
        }
        for token in tokens
    ]
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(EXPO_PUSH_URL, json=messages, timeout=10)
            resp.raise_for_status()
            logger.info("Expo push sent to %d token(s)", len(tokens))
    except Exception as exc:
        logger.warning("Expo push failed reason=%s", type(exc).__name__)
