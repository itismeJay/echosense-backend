import httpx
from datetime import datetime

async def send_push_notification(alert_data: dict):
    message = {
        "title": f"🚨 EchoSense Alert - {alert_data['severity'].upper()}",
        "body": f"Aggression detected! Confidence: {alert_data['confidence']*100:.0f}% at {alert_data['location']}",
        "severity": alert_data['severity'],
        "timestamp": datetime.utcnow().isoformat()
    }
    return message

async def notify_subscribers(alert_data: dict):
    notification = await send_push_notification(alert_data)
    print(f"[PUSH NOTIFICATION] {notification['title']}")
    print(f"[PUSH NOTIFICATION] {notification['body']}")
    return notification