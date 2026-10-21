#!/usr/bin/env python3
"""Send one controlled schema-v2 alert to a local EchoSense backend."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from dotenv import dotenv_values
from pydantic import ValidationError

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from app.schemas.alert import (  # noqa: E402
    REVIEW_NOTICE,
    AlertCreate,
    SeverityLevel,
    TriggerType,
)

DEFAULT_BACKEND_URL = "http://127.0.0.1:8000"
DEFAULT_CREDENTIALS_FILE = ".env.local.device"
NONLOCAL_OVERRIDE_VARIABLE = "ECHOSENSE_ALLOW_NONLOCAL_ALERT_BACKEND"
VALID_PUSH_STATUSES = frozenset({"pending", "accepted", "partial", "rejected", "failed", "skipped"})


class ControlledAlertError(RuntimeError):
    """A safe, user-facing failure that must prevent submission."""


class DeviceRegistrationRequired(ControlledAlertError):
    """The configured registered-device credential is unavailable."""


@dataclass(frozen=True)
class DeviceCredentials:
    device_id: str
    device_key: str


@dataclass(frozen=True)
class BackendTarget:
    url: str
    label: str


def _normalize_enum(value: str, enum_type: type, field_name: str) -> str:
    normalized = value.strip().upper()
    try:
        return enum_type(normalized).value
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise ControlledAlertError(f"{field_name} must be one of: {allowed}") from exc


def validate_severity(value: str) -> str:
    return _normalize_enum(value, SeverityLevel, "severity")


def validate_trigger_type(value: str) -> str:
    return _normalize_enum(value, TriggerType, "trigger_type")


def resolve_backend(environ: Mapping[str, str]) -> BackendTarget:
    configured_url = environ.get("ECHOSENSE_TEST_BACKEND_URL", DEFAULT_BACKEND_URL).strip()
    if not configured_url:
        raise ControlledAlertError("ECHOSENSE_TEST_BACKEND_URL must not be empty")

    try:
        parsed = urlsplit(configured_url)
        parsed.port
    except ValueError as exc:
        raise ControlledAlertError("ECHOSENSE_TEST_BACKEND_URL is invalid") from exc

    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ControlledAlertError("ECHOSENSE_TEST_BACKEND_URL must be an HTTP(S) origin")

    hostname = parsed.hostname.casefold()
    is_loopback = hostname in {"127.0.0.1", "localhost", "::1"}
    if is_loopback:
        if parsed.scheme != "http":
            raise ControlledAlertError("The local backend must use HTTP on a loopback host")
        return BackendTarget(configured_url.rstrip("/"), "local")

    if environ.get(NONLOCAL_OVERRIDE_VARIABLE, "").casefold() != "true":
        raise ControlledAlertError(
            "Refusing a non-local backend; set "
            f"{NONLOCAL_OVERRIDE_VARIABLE}=true only for an intentional override"
        )
    return BackendTarget(configured_url.rstrip("/"), "configured override")


def _credentials_path(environ: Mapping[str, str]) -> Path:
    configured_path = environ.get(
        "ECHOSENSE_DEVICE_CREDENTIALS_FILE", DEFAULT_CREDENTIALS_FILE
    ).strip()
    if not configured_path:
        raise DeviceRegistrationRequired("DEVICE REGISTRATION REQUIRED")

    candidate = Path(configured_path)
    if not candidate.is_absolute():
        candidate = REPOSITORY_ROOT / candidate
    resolved = candidate.resolve()
    if (
        resolved.parent != REPOSITORY_ROOT.resolve()
        or not resolved.name.startswith(".env.")
        or resolved.name.endswith(".example")
    ):
        raise ControlledAlertError(
            "Device credentials must use a repository-local, gitignored .env.* file"
        )
    return resolved


def load_device_credentials(environ: Mapping[str, str]) -> DeviceCredentials:
    path = _credentials_path(environ)
    if not path.is_file():
        raise DeviceRegistrationRequired("DEVICE REGISTRATION REQUIRED")

    values = dotenv_values(path)
    device_id = (values.get("ECHOSENSE_DEVICE_ID") or "").strip()
    device_key = (values.get("ECHOSENSE_DEVICE_KEY") or "").strip()
    if not device_id or not device_key or len(device_id) > 100 or len(device_key) > 256:
        raise DeviceRegistrationRequired("DEVICE REGISTRATION REQUIRED")
    return DeviceCredentials(device_id=device_id, device_key=device_key)


def build_alert_payload(
    severity: str,
    trigger_type: str,
    transcript: str | None,
    credentials: DeviceCredentials,
    event_id: UUID,
    event_timestamp: datetime,
) -> dict[str, Any]:
    normalized_severity = validate_severity(severity)
    normalized_trigger = validate_trigger_type(trigger_type)
    if transcript is not None and not transcript.strip():
        raise ControlledAlertError("transcript must not be blank when supplied")

    payload: dict[str, Any] = {
        "event_id": event_id,
        "schema_version": 2,
        "trigger_type": normalized_trigger,
        "severity": normalized_severity,
        "severity_reasons": [f"controlled developer {normalized_trigger} alert"],
        "review_message": REVIEW_NOTICE,
        "device_identifier": credentials.device_id,
        "event_start_timestamp": event_timestamp,
        "event_end_timestamp": event_timestamp,
        "transcription_status": "complete" if transcript is not None else "not_attempted",
        "test_mode": normalized_trigger == TriggerType.TEST.value,
    }
    if transcript is not None:
        payload["transcribed_text"] = transcript

    try:
        validated = AlertCreate.model_validate(payload)
    except ValidationError as exc:
        raise ControlledAlertError("Generated alert failed the schema-v2 contract") from exc
    return validated.model_dump(mode="json", exclude_unset=True)


def _response_detail(response: Any) -> str | None:
    try:
        body = response.json()
    except Exception:
        return None
    if isinstance(body, dict) and isinstance(body.get("detail"), str):
        return body["detail"]
    return None


def _print_rejected_submission(response: Any, trigger_type: str, event_id: UUID) -> None:
    status_code = response.status_code
    detail = _response_detail(response)
    print(f"HTTP: {status_code}")
    print(f"Event ID: {event_id}")
    if status_code == 401 or (status_code == 403 and detail == "Invalid device credentials"):
        print("DEVICE REGISTRATION REQUIRED")
    elif (
        status_code == 403
        and trigger_type == TriggerType.TEST.value
        and detail == "Synthetic alert ingestion is disabled"
    ):
        print("TEST alert ingestion is disabled by the configured backend")
    elif status_code == 422:
        print("API contract validation failed")
    else:
        print("Alert submission rejected; no retry attempted")


def run(
    argv: list[str],
    *,
    environ: Mapping[str, str] | None = None,
    post: Callable[..., Any] | None = None,
    uuid_factory: Callable[[], UUID] = uuid4,
    now_factory: Callable[[], datetime] | None = None,
) -> int:
    environment = os.environ if environ is None else environ
    if len(argv) not in {2, 3}:
        raise ControlledAlertError(
            "Usage: ./scripts/send_alert.sh <SEVERITY> <TRIGGER_TYPE> [TRANSCRIPT]"
        )

    severity = validate_severity(argv[0])
    trigger_type = validate_trigger_type(argv[1])
    transcript = argv[2] if len(argv) == 3 else None
    backend = resolve_backend(environment)
    credentials = load_device_credentials(environment)
    event_id = uuid_factory()
    event_timestamp = now_factory() if now_factory is not None else datetime.now(timezone.utc)
    payload = build_alert_payload(
        severity,
        trigger_type,
        transcript,
        credentials,
        event_id,
        event_timestamp,
    )
    headers = {
        "X-EchoSense-Device-Id": credentials.device_id,
        "X-EchoSense-Device-Key": credentials.device_key,
        "Idempotency-Key": str(event_id),
    }

    print("EchoSense controlled alert")
    print()
    print(f"Backend: {backend.label}")
    print(f"Trigger: {trigger_type}")
    print(f"Severity: {severity}")

    submit = httpx.post if post is None else post
    try:
        response = submit(
            f"{backend.url}/alerts/",
            json=payload,
            headers=headers,
            timeout=15.0,
            follow_redirects=False,
        )
    except httpx.RequestError:
        print("HTTP: unavailable")
        print(f"Event ID: {event_id}")
        print("Backend unavailable or submission outcome ambiguous; no retry attempted")
        return 1

    if not 200 <= response.status_code < 300:
        _print_rejected_submission(response, trigger_type, event_id)
        return 1

    try:
        body = response.json()
    except Exception:
        body = None
    if (
        not isinstance(body, dict)
        or not isinstance(body.get("id"), int)
        or str(body.get("event_id")) != str(event_id)
        or body.get("delivery_status") != "stored"
    ):
        print(f"HTTP: {response.status_code}")
        print(f"Event ID: {event_id}")
        print("Submission response was invalid or ambiguous; no retry attempted")
        return 1

    push_status = body.get("push_status")
    push_output = push_status if push_status in VALID_PUSH_STATUSES else "none"
    print(f"HTTP: {response.status_code}")
    print(f"Alert ID: {body['id']}")
    print(f"Event ID: {event_id}")
    print(f"Push: {push_output}")
    return 0


def main() -> int:
    try:
        return run(sys.argv[1:])
    except DeviceRegistrationRequired:
        print("DEVICE REGISTRATION REQUIRED", file=sys.stderr)
        return 1
    except ControlledAlertError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
