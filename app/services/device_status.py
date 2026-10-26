"""One rule for whether a classroom device is online, used by every status view."""

from datetime import datetime, timedelta, timezone

# Mirrors DEVICE_ONLINE_WINDOW_MS in the frontend (lib/device-status.ts).
ONLINE_WINDOW_SECONDS = 120


def is_device_online(last_seen_at: datetime | None, now: datetime | None = None) -> bool:
    """A device is online if it checked in during the last two minutes."""

    if last_seen_at is None:
        return False
    current = now or datetime.now(timezone.utc)
    seen = last_seen_at if last_seen_at.tzinfo else last_seen_at.replace(tzinfo=timezone.utc)
    return current - seen <= timedelta(seconds=ONLINE_WINDOW_SECONDS)
