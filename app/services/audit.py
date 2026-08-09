import ipaddress
import math
import re
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.audit_log import AuditLog
from app.models.user import User


class AuditStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


class AuditAction(str, Enum):
    LOGIN = "LOGIN"
    LOGIN_FAILED = "LOGIN_FAILED"
    CREATE_USER = "CREATE_USER"
    DELETE_USER = "DELETE_USER"
    ADD_MONITORED_TERM = "ADD_MONITORED_TERM"
    DELETE_MONITORED_TERM = "DELETE_MONITORED_TERM"
    UPDATE_SETTINGS = "UPDATE_SETTINGS"
    GENERATE_REPORT = "GENERATE_REPORT"
    TRIGGER_OTA_PUSH = "TRIGGER_OTA_PUSH"
    EXPORT_AUDIT_LOGS = "EXPORT_AUDIT_LOGS"
    REGISTER_EDGE_DEVICE = "REGISTER_EDGE_DEVICE"
    UPDATE_EDGE_DEVICE = "UPDATE_EDGE_DEVICE"
    ROTATE_EDGE_DEVICE_KEY = "ROTATE_EDGE_DEVICE_KEY"
    PERMISSION_DENIED = "PERMISSION_DENIED"


class AuditResource(str, Enum):
    AUTHENTICATION = "Authentication"
    USER = "User"
    MONITORED_TERM = "MonitoredTerm"
    SETTINGS = "Settings"
    REPORT = "Report"
    AUDIT_LOG = "AuditLog"
    EDGE_DEVICE = "EdgeDevice"
    SECURITY = "Security"


_ACTION_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,99}$")
_SENSITIVE_KEY_PARTS = {
    "password",
    "password_hash",
    "current_password",
    "new_password",
    "access_token",
    "refresh_token",
    "token",
    "authorization",
    "cookie",
    "session_cookie",
    "api_key",
    "secret",
    "secret_key",
    "database_url",
    "credential",
    "credentials",
}
_MAX_METADATA_DEPTH = 6
_MAX_METADATA_ITEMS = 100
_MAX_METADATA_STRING_LENGTH = 500


def _enum_value(value: str | Enum) -> str:
    return str(value.value if isinstance(value, Enum) else value)


def _normalise_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(key).strip().lower()).strip("_")


def _is_sensitive_key(key: Any) -> bool:
    normalised = _normalise_key(key)
    compact = normalised.replace("_", "")
    return any(part.replace("_", "") in compact for part in _SENSITIVE_KEY_PARTS)


def _sanitise_metadata_value(value: Any, depth: int) -> Any:
    if depth > _MAX_METADATA_DEPTH:
        return None

    if isinstance(value, Mapping):
        clean: dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:_MAX_METADATA_ITEMS]:
            if _is_sensitive_key(raw_key):
                continue
            key = str(raw_key).strip()[:100]
            if not key:
                continue
            clean[key] = _sanitise_metadata_value(raw_value, depth + 1)
        return clean

    if isinstance(value, (list, tuple, set)):
        return [
            _sanitise_metadata_value(item, depth + 1) for item in list(value)[:_MAX_METADATA_ITEMS]
        ]

    if isinstance(value, str):
        return value[:_MAX_METADATA_STRING_LENGTH]

    if isinstance(value, bool) or value is None or isinstance(value, int):
        return value

    if isinstance(value, float):
        return value if math.isfinite(value) else None

    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    if isinstance(value, date):
        return value.isoformat()

    # Do not serialise arbitrary objects, exceptions, ORM instances, or tokens.
    return None


def sanitise_metadata(metadata: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    if not metadata:
        return {}
    clean = _sanitise_metadata_value(metadata, 0)
    return clean if isinstance(clean, dict) else {}


def _sanitise_text(value: Any, max_length: int) -> Optional[str]:
    if value is None:
        return None
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value)).strip()
    return text[:max_length] or None


def _trusted_proxy_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for raw_cidr in settings.TRUSTED_PROXY_CIDRS.split(","):
        cidr = raw_cidr.strip()
        if not cidr:
            continue
        try:
            networks.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            continue
    return networks


def extract_ip_address(request: Optional[Request]) -> Optional[str]:
    if request is None or request.client is None:
        return None

    direct_host = request.client.host
    try:
        direct_ip = ipaddress.ip_address(direct_host)
    except ValueError:
        return None

    if any(direct_ip in network for network in _trusted_proxy_networks()):
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            candidate = forwarded_for.split(",", 1)[0].strip()
            try:
                return str(ipaddress.ip_address(candidate))
            except ValueError:
                pass

    return str(direct_ip)


def _utc_datetime(value: Optional[datetime]) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def record_audit_event(
    db: AsyncSession,
    request: Optional[Request],
    action: str | AuditAction,
    resource: str | AuditResource,
    status: str | AuditStatus,
    *,
    actor: Optional[User] = None,
    actor_email: Optional[str] = None,
    actor_role: Optional[str] = None,
    resource_id: Any = None,
    target: Optional[str] = None,
    description: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
    occurred_at: Optional[datetime] = None,
) -> AuditLog:
    action_value = _enum_value(action)
    resource_value = _enum_value(resource)
    status_value = _enum_value(status)

    if not _ACTION_PATTERN.fullmatch(action_value):
        raise ValueError("Audit action must be a stable uppercase machine-readable name")
    if not resource_value or len(resource_value) > 100:
        raise ValueError("Audit resource must be between 1 and 100 characters")
    if status_value not in {item.value for item in AuditStatus}:
        raise ValueError("Audit status must be SUCCESS or FAILURE")

    event = AuditLog(
        occurred_at=_utc_datetime(occurred_at),
        actor_user_id=actor.id if actor is not None else None,
        actor_email=_sanitise_text(
            actor.email if actor is not None else actor_email,
            320,
        ),
        actor_role=_sanitise_text(
            actor.role if actor is not None else actor_role,
            50,
        ),
        action=action_value,
        resource=resource_value,
        resource_id=_sanitise_text(resource_id, 100),
        target=_sanitise_text(target, 500),
        status=status_value,
        description=_sanitise_text(description, 500),
        ip_address=extract_ip_address(request),
        user_agent=_sanitise_text(
            request.headers.get("user-agent") if request is not None else None,
            512,
        ),
        request_id=_sanitise_text(
            getattr(request.state, "request_id", None) if request is not None else None,
            36,
        ),
        metadata_json=sanitise_metadata(metadata),
        created_at=datetime.now(timezone.utc),
    )
    db.add(event)
    await db.flush()
    return event
