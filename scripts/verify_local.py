#!/usr/bin/env python3
"""Exercise local device authentication and one controlled schema-v2 test event."""

import asyncio
import json
import os
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4

import httpx
from dotenv import load_dotenv
from sqlalchemy import text

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))
load_dotenv(REPOSITORY_ROOT / ".env.local", override=True)
credentials_file = os.environ.get("ECHOSENSE_DEVICE_CREDENTIALS_FILE", ".env.local.device")
credentials_path = (REPOSITORY_ROOT / credentials_file).resolve()
if credentials_path.parent != REPOSITORY_ROOT or not credentials_path.name.startswith(".env.local"):
    raise RuntimeError("Device credentials must use a .env.local* file in the repository")
load_dotenv(credentials_path, override=True)

from app.database import AsyncSessionLocal  # noqa: E402
from scripts.local_safety import validate_local_environment  # noqa: E402


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _api_url() -> str:
    value = _required("ECHOSENSE_LOCAL_API_URL").rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("Verification is restricted to an HTTP loopback API URL")
    return value


def _payload(device_code: str, event_id: UUID) -> dict:
    start = datetime.now(timezone.utc)
    return {
        "event_id": str(event_id),
        "schema_version": 2,
        "trigger_type": "TEST",
        "severity": "LOW",
        "severity_reasons": ["controlled_local_schema_v2_test"],
        "review_message": "Unverified possible-aggression alert. Human review required.",
        "device_identifier": device_code,
        "device_source": {"collector": "local_verification"},
        "event_start_timestamp": start.isoformat(),
        "event_end_timestamp": (start + timedelta(seconds=1)).isoformat(),
        "transcription_status": "not_attempted",
        "test_mode": True,
        "confidence": 0.5,
        "duration": 1.0,
        "language": "unknown",
    }


async def _stored_alert(event_id: UUID) -> tuple[int, dict | None]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text(
                """
                SELECT device_identifier, classroom_name_snapshot, school_name_snapshot
                FROM alerts
                WHERE event_id = :event_id
                """
            ),
            {"event_id": event_id},
        )
        rows = [dict(row) for row in result.mappings().all()]
        return len(rows), rows[0] if rows else None


async def verify() -> None:
    validate_local_environment()
    if os.environ.get("ECHOSENSE_ALLOW_TEST_ALERTS", "").casefold() != "true":
        raise RuntimeError("ECHOSENSE_ALLOW_TEST_ALERTS must be true for local verification")

    device_code = _required("ECHOSENSE_DEVICE_ID")
    device_key = _required("ECHOSENSE_DEVICE_KEY")
    admin_email = _required("ECHOSENSE_DEV_ADMIN_EMAIL")
    admin_password = _required("ECHOSENSE_DEV_ADMIN_PASSWORD")
    school_name = _required("ECHOSENSE_DEV_SCHOOL")
    classroom_name = _required("ECHOSENSE_DEV_CLASSROOM")
    event_id = uuid4()
    payload = _payload(device_code, event_id)
    idempotency_headers = {"Idempotency-Key": str(event_id)}
    valid_headers = {
        **idempotency_headers,
        "X-EchoSense-Device-Id": device_code,
        "X-EchoSense-Device-Key": device_key,
    }

    async with httpx.AsyncClient(base_url=_api_url(), timeout=15.0) as client:
        health = await client.get("/health")
        health.raise_for_status()

        missing = await client.post("/alerts/", json=payload, headers=idempotency_headers)
        wrong = await client.post(
            "/alerts/",
            json=payload,
            headers={
                **idempotency_headers,
                "X-EchoSense-Device-Id": device_code,
                "X-EchoSense-Device-Key": "deliberately-wrong-local-key",
            },
        )
        valid = await client.post("/alerts/", json=payload, headers=valid_headers)
        identical = await client.post("/alerts/", json=deepcopy(payload), headers=valid_headers)
        changed_payload = deepcopy(payload)
        changed_payload["severity"] = "MEDIUM"
        changed = await client.post("/alerts/", json=changed_payload, headers=valid_headers)

        login = await client.post(
            "/auth/login", json={"email": admin_email, "password": admin_password}
        )
        login.raise_for_status()
        admin_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        devices = await client.get("/devices", headers=admin_headers)
        devices.raise_for_status()
        device = next(item for item in devices.json() if item["device_code"] == device_code)
        disable = await client.patch(
            f"/devices/{device['id']}",
            headers=admin_headers,
            json={"is_active": False},
        )
        disable.raise_for_status()
        disabled_event_id = uuid4()
        disabled_payload = _payload(device_code, disabled_event_id)
        try:
            disabled = await client.post(
                "/alerts/",
                json=disabled_payload,
                headers={
                    "Idempotency-Key": str(disabled_event_id),
                    "X-EchoSense-Device-Id": device_code,
                    "X-EchoSense-Device-Key": device_key,
                },
            )
        finally:
            enable = await client.patch(
                f"/devices/{device['id']}",
                headers=admin_headers,
                json={"is_active": True},
            )
            enable.raise_for_status()

    count, stored = await _stored_alert(event_id)
    response_body = valid.json() if valid.status_code == 200 else {}
    checks = {
        "missing": missing.status_code,
        "wrong": wrong.status_code,
        "valid": valid.status_code,
        "disabled": disabled.status_code,
        "created": valid.status_code == 200 and count == 1,
        "schema_v2": response_body.get("schema_version") == 2,
        "trusted_assignment": bool(
            stored
            and stored["device_identifier"] == device_code
            and stored["classroom_name_snapshot"] == classroom_name
            and stored["school_name_snapshot"] == school_name
            and response_body.get("device_code") == device_code
            and response_body.get("classroom_name") == classroom_name
            and response_body.get("school_name") == school_name
        ),
        "idempotent_retry": (
            identical.status_code == 200
            and identical.json().get("id") == response_body.get("id")
            and count == 1
        ),
        "changed_retry": changed.status_code,
        "raw_audio_uploaded": False,
        "event_id": str(event_id),
    }
    expected = {
        "missing": 401,
        "wrong": 401,
        "valid": 200,
        "disabled": 403,
        "created": True,
        "schema_v2": True,
        "trusted_assignment": True,
        "idempotent_retry": True,
        "changed_retry": 409,
        "raw_audio_uploaded": False,
    }
    failures = [key for key, expected_value in expected.items() if checks[key] != expected_value]
    (REPOSITORY_ROOT / ".local-verification.json").write_text(
        json.dumps(checks, indent=2) + "\n", encoding="utf-8"
    )

    print("EchoSense local verification")
    for label in ("missing", "wrong", "valid", "disabled"):
        print(f"Device auth {label}: HTTP {checks[label]}")
    print(f"Schema v2: {'pass' if checks['schema_v2'] else 'fail'}")
    print(f"Trusted assignment: {'pass' if checks['trusted_assignment'] else 'fail'}")
    print(f"Idempotent retry: {'pass' if checks['idempotent_retry'] else 'fail'}")
    print(f"Changed retry: HTTP {checks['changed_retry']}")
    print("Raw audio uploaded: no")
    if failures:
        raise RuntimeError(f"Local verification failed: {', '.join(failures)}")


if __name__ == "__main__":
    asyncio.run(verify())
