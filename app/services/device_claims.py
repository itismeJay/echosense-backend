"""QR setup codes for edge boxes: generation, normalization, hashing, throttling.

A provisioned box carries three values:
- device_code (``es-xxxx``): printed on the sticker, not secret.
- claim code (12 characters, ~59 bits): in the sticker's QR and typed by hand as a
  fallback. Proves the admin holds this box. Stored as a bcrypt hash.
- box secret (256 bits): only in the box's identity.json, never printed. Together with
  the claim code it lets the box itself fetch its device key once, so a photo of the
  sticker is not enough to take the key. Stored as a SHA-256 hash (high entropy).
"""

from __future__ import annotations

from collections import deque
import hashlib
import hmac
import math
import re
import secrets
import threading
import time
from typing import Callable
from urllib.parse import urlencode

from app.config import settings
from app.routers.auth import pwd_context

# No 0/O, 1/I/L: unambiguous when read off a sticker and typed.
CLAIM_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CLAIM_CODE_LENGTH = 12
DEVICE_CODE_ALPHABET = CLAIM_CODE_ALPHABET.lower()
DEVICE_CODE_SUFFIX_LENGTH = 4
DEVICE_CODE_PREFIX = "es-"
QR_SCHEME_PREFIX = "echosense://claim"

_CLAIM_CODE_PATTERN = re.compile(f"^[{CLAIM_CODE_ALPHABET}]{{{CLAIM_CODE_LENGTH}}}$")
_DUMMY_CLAIM_CODE_HASH = pwd_context.hash("DUMMYCLAIMCODE")


def generate_device_code() -> str:
    suffix = "".join(secrets.choice(DEVICE_CODE_ALPHABET) for _ in range(DEVICE_CODE_SUFFIX_LENGTH))
    return f"{DEVICE_CODE_PREFIX}{suffix}"


def generate_claim_code() -> str:
    raw = "".join(secrets.choice(CLAIM_CODE_ALPHABET) for _ in range(CLAIM_CODE_LENGTH))
    return format_claim_code(raw)


def format_claim_code(normalized: str) -> str:
    return "-".join(normalized[index : index + 4] for index in range(0, len(normalized), 4))


def normalize_claim_code(value: str) -> str | None:
    """Uppercase and drop spaces/hyphens; None when it can't be a claim code."""

    compact = re.sub(r"[\s-]+", "", str(value or "")).upper()
    return compact if _CLAIM_CODE_PATTERN.fullmatch(compact) else None


def hash_claim_code(normalized: str) -> str:
    return pwd_context.hash(normalized)


def verify_claim_code(value: str, claim_code_hash: str | None) -> bool:
    """Constant-ish time: always runs one bcrypt verify, even for a missing hash."""

    normalized = normalize_claim_code(value)
    target = claim_code_hash if (claim_code_hash and normalized) else _DUMMY_CLAIM_CODE_HASH
    try:
        matched = pwd_context.verify(normalized or "INVALID", target)
    except (TypeError, ValueError):
        return False
    return matched and bool(claim_code_hash) and normalized is not None


def generate_box_secret() -> str:
    return f"ebs_{secrets.token_urlsafe(32)}"


def hash_box_secret(box_secret: str) -> str:
    return hashlib.sha256(box_secret.encode("utf-8")).hexdigest()


def verify_box_secret(box_secret: str, box_secret_hash: str | None) -> bool:
    candidate = hash_box_secret(str(box_secret or ""))
    return hmac.compare_digest(candidate, box_secret_hash or "0" * 64) and bool(box_secret_hash)


def qr_payload(device_code: str, claim_code: str) -> str:
    return f"{QR_SCHEME_PREFIX}?{urlencode({'d': device_code, 'c': claim_code})}"


class FailureRateLimiter:
    """Sliding-window count of failed setup-code attempts per key (device, user, IP).

    In-process, like the login limiter: with several workers each keeps its own counts,
    which still bounds guessing. Only failures count, so a box polling while it waits
    is never throttled.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._failures: dict[str, deque[float]] = {}

    def _prune(self, key: str, now: float) -> deque[float]:
        window = settings.DEVICE_CLAIM_FAILURE_WINDOW_SECONDS
        entries = self._failures.get(key)
        if entries is None:
            return deque()
        while entries and now - entries[0] >= window:
            entries.popleft()
        if not entries:
            self._failures.pop(key, None)
        return entries

    def retry_after(self, limits: dict[str, int]) -> int | None:
        """Seconds until allowed for the given {key: limit}, or None when allowed now."""

        if not settings.DEVICE_CLAIM_RATE_LIMIT_ENABLED:
            return None
        now = self._clock()
        waits = []
        with self._lock:
            for key, limit in limits.items():
                entries = self._prune(key, now)
                if len(entries) >= limit:
                    oldest = entries[len(entries) - limit]
                    waits.append(settings.DEVICE_CLAIM_FAILURE_WINDOW_SECONDS - (now - oldest))
        if not waits:
            return None
        return max(1, math.ceil(max(waits)))

    def record_failure(self, keys) -> None:
        if not settings.DEVICE_CLAIM_RATE_LIMIT_ENABLED:
            return
        now = self._clock()
        with self._lock:
            for key in keys:
                self._failures.setdefault(key, deque()).append(now)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()


claim_rate_limiter = FailureRateLimiter()


def claim_limits(device_code: str, user_id: int, ip_address: str | None) -> dict[str, int]:
    return {
        f"claim-device:{device_code}": settings.DEVICE_CLAIM_MAX_FAILURES_PER_DEVICE,
        f"claim-user:{user_id}": settings.DEVICE_CLAIM_MAX_FAILURES_PER_USER,
        f"claim-ip:{ip_address or 'unknown'}": settings.DEVICE_CLAIM_MAX_FAILURES_PER_IP,
    }


def activate_limits(device_code: str, ip_address: str | None) -> dict[str, int]:
    return {
        f"activate-device:{device_code}": settings.DEVICE_CLAIM_MAX_FAILURES_PER_DEVICE,
        f"activate-ip:{ip_address or 'unknown'}": settings.DEVICE_CLAIM_MAX_FAILURES_PER_IP,
    }
