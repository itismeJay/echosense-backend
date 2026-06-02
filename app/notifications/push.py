import httpx
import logging

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


async def send_expo_pushes(tokens: list, alert_id: int, severity: str, location: str):
    if not tokens:
        return
    messages = [
        {
            "to": token,
            "title": "🚨 EchoSense Alert",
            "body": f"{severity.upper()} aggression detected in {location}",
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
        logger.warning("Expo push failed: %s", exc)
