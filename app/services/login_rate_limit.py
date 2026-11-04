"""Basic in-process throttle for failed sign-ins.

Counts failed attempts in a sliding window, per account (email, normalized) and per client
IP. While either is over its limit, /auth/login answers 429 before checking the password,
so guessing stops being useful. A successful sign-in clears that account's failures.

Limits are per process: with several workers each keeps its own counts, which still bounds
guessing. The client IP is the one audit logging uses (X-Forwarded-For only from
TRUSTED_PROXY_CIDRS); behind an untrusted proxy every client shares the proxy's IP, so the
per-IP limit is set well above the per-account one.
"""

from __future__ import annotations

from collections import deque
import math
import threading
import time
from typing import Callable

from app.config import settings


class LoginRateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._failures: dict[str, deque[float]] = {}

    @staticmethod
    def _keys(email: str, ip_address: str | None) -> tuple[str, str]:
        account = "account:" + " ".join(str(email or "").strip().casefold().split())
        return account, f"ip:{ip_address or 'unknown'}"

    def _prune(self, key: str, now: float) -> deque[float]:
        window = settings.LOGIN_FAILURE_WINDOW_SECONDS
        entries = self._failures.get(key)
        if entries is None:
            return deque()
        while entries and now - entries[0] >= window:
            entries.popleft()
        if not entries:
            self._failures.pop(key, None)
        return entries

    def retry_after(self, email: str, ip_address: str | None) -> int | None:
        """Seconds until the next attempt is allowed, or None when it is allowed now."""

        if not settings.LOGIN_RATE_LIMIT_ENABLED:
            return None
        now = self._clock()
        account_key, ip_key = self._keys(email, ip_address)
        waits = []
        with self._lock:
            for key, limit in (
                (account_key, settings.LOGIN_MAX_FAILURES_PER_ACCOUNT),
                (ip_key, settings.LOGIN_MAX_FAILURES_PER_IP),
            ):
                entries = self._prune(key, now)
                if len(entries) >= limit:
                    # Allowed again once the oldest failure that keeps it over the limit expires.
                    oldest = entries[len(entries) - limit]
                    waits.append(settings.LOGIN_FAILURE_WINDOW_SECONDS - (now - oldest))
        if not waits:
            return None
        return max(1, math.ceil(max(waits)))

    def record_failure(self, email: str, ip_address: str | None) -> None:
        if not settings.LOGIN_RATE_LIMIT_ENABLED:
            return
        now = self._clock()
        with self._lock:
            for key in self._keys(email, ip_address):
                self._failures.setdefault(key, deque()).append(now)

    def record_success(self, email: str, ip_address: str | None) -> None:
        account_key, _ = self._keys(email, ip_address)
        with self._lock:
            self._failures.pop(account_key, None)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()


login_rate_limiter = LoginRateLimiter()
