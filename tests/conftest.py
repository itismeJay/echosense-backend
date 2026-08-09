import os
from unittest.mock import AsyncMock
from urllib.parse import urlparse
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from dotenv import dotenv_values
from sqlalchemy import select, text

TEST_DATABASE_URL = os.environ.get("ECHOSENSE_TEST_DATABASE_URL")
if not TEST_DATABASE_URL:
    raise RuntimeError(
        "ECHOSENSE_TEST_DATABASE_URL is required; tests refuse to inherit the application database"
    )

parsed_database_url = urlparse(TEST_DATABASE_URL)
if parsed_database_url.scheme not in {
    "postgresql",
    "postgresql+asyncpg",
    "postgresql+psycopg2",
}:
    raise RuntimeError("ECHOSENSE_TEST_DATABASE_URL must select PostgreSQL")
if parsed_database_url.hostname not in {"127.0.0.1", "localhost", "::1"}:
    raise RuntimeError("Audit tests are restricted to a localhost PostgreSQL database")
test_database_name = parsed_database_url.path.lstrip("/").casefold()
if "test" not in test_database_name:
    raise RuntimeError("ECHOSENSE_TEST_DATABASE_URL database name must contain 'test'")
if test_database_name in {"postgres", "production", "prod", "echosense"}:
    raise RuntimeError("ECHOSENSE_TEST_DATABASE_URL names an unsafe application database")
if any(
    marker in test_database_name for marker in ("production", "prod_", "_prod", "live_", "_live")
):
    raise RuntimeError("ECHOSENSE_TEST_DATABASE_URL resembles a production database name")


def _database_identity(database_url: str | None):
    if not database_url:
        return None
    parsed = urlparse(database_url)
    host = parsed.hostname.casefold() if parsed.hostname else None
    if host in {"localhost", "127.0.0.1", "::1"}:
        host = "loopback"
    return (
        host,
        parsed.port or 5432,
        parsed.path.lstrip("/").casefold(),
        parsed.username,
    )


repository_env = dotenv_values(".env")
application_database_url = os.environ.get("DATABASE_URL") or repository_env.get("DATABASE_URL")
if _database_identity(TEST_DATABASE_URL) == _database_identity(application_database_url):
    raise RuntimeError(
        "ECHOSENSE_TEST_DATABASE_URL must not identify the application DATABASE_URL database"
    )

os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["SECRET_KEY"] = "local-test-secret-not-for-production"
os.environ["RUN_LEGACY_STARTUP_MAINTENANCE"] = "false"
os.environ["SQL_ECHO"] = "false"
os.environ["TRUSTED_PROXY_CIDRS"] = ""
os.environ["TESTING"] = "true"
os.environ["ECHOSENSE_ALLOW_TEST_ALERTS"] = "true"

from app.database import AsyncSessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models.edge_device import EdgeDevice  # noqa: E402
from app.models.system_settings import SystemSettings  # noqa: E402
from app.models.user import User  # noqa: E402
from app.routers.auth import create_token, pwd_context  # noqa: E402

TEST_DEVICE_CODE = "classroom-test-pi"
TEST_DEVICE_KEY = "synthetic-device-key-for-tests-only"
TEST_DEVICE_KEY_HASH = pwd_context.hash(TEST_DEVICE_KEY)


def finalized_alert_fields(event_id=None, *, trigger_type="KEYWORD", test_mode=False) -> dict:
    return {
        "event_id": str(event_id or uuid4()),
        "schema_version": 2,
        "trigger_type": trigger_type,
        "severity_reasons": ["synthetic_test_reason"],
        "review_message": "Unverified possible-aggression alert. Human review required.",
        "device_identifier": TEST_DEVICE_CODE,
        "event_start_timestamp": "2026-08-04T00:00:00Z",
        "event_end_timestamp": "2026-08-04T00:00:01Z",
        "transcription_status": "complete",
        "test_mode": test_mode,
    }


class Phase3TestClient(httpx.AsyncClient):
    async def request(self, method, url, **kwargs):
        headers = dict(kwargs.pop("headers", {}) or {})
        if method.upper() == "POST" and str(url).split("?", 1)[0].rstrip("/") == "/alerts":
            body = kwargs.get("json")
            if isinstance(body, dict) and body.get("event_id") and "Idempotency-Key" not in headers:
                headers["Idempotency-Key"] = str(body["event_id"])
        return await super().request(method, url, headers=headers, **kwargs)


@pytest.fixture(autouse=True)
def prevent_external_notifications(monkeypatch):
    mocked_sender = AsyncMock()
    monkeypatch.setattr("app.routers.alerts.send_expo_pushes", mocked_sender)
    return mocked_sender


@pytest_asyncio.fixture(autouse=True)
async def isolated_database():
    async with engine.begin() as connection:
        await connection.execute(
            text("ALTER TABLE audit_logs DISABLE TRIGGER trg_audit_logs_prevent_truncate")
        )
        await connection.execute(
            text(
                """
                TRUNCATE TABLE reports, audit_logs, alert_matched_terms, slur_dictionary,
                    system_settings, alerts, edge_devices, users
                RESTART IDENTITY CASCADE
                """
            )
        )
        await connection.execute(
            text("ALTER TABLE audit_logs ENABLE TRIGGER trg_audit_logs_prevent_truncate")
        )

    async with AsyncSessionLocal() as session:
        session.add(
            SystemSettings(
                confidence_threshold=0.55,
                aggression_duration_threshold=2.0,
                device_status="offline",
                vosk_version="local-test",
                yamnet_version="local-test",
            )
        )
        session.add(
            EdgeDevice(
                device_code=TEST_DEVICE_CODE,
                display_name="Synthetic Test Device",
                classroom_name="Synthetic Test Classroom",
                school_name="Synthetic Test School",
                api_key_hash=TEST_DEVICE_KEY_HASH,
            )
        )
        await session.commit()

    yield


@pytest_asyncio.fixture
async def identities():
    async with AsyncSessionLocal() as session:
        users = {
            role: User(
                email=f"{role}@school.test",
                hashed_password=pwd_context.hash(f"{role}-password"),
                role=role,
            )
            for role in ("admin", "staff", "counselor")
        }
        session.add_all(users.values())
        await session.commit()
        for user in users.values():
            await session.refresh(user)

        return {
            role: {
                "id": user.id,
                "email": user.email,
                "password": f"{role}-password",
                "token": create_token(user),
            }
            for role, user in users.items()
        }


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 12345))
    async with Phase3TestClient(
        transport=transport,
        base_url="http://testserver",
        headers={
            "User-Agent": "EchoSense audit test",
            "X-EchoSense-Device-Id": TEST_DEVICE_CODE,
            "X-EchoSense-Device-Key": TEST_DEVICE_KEY,
        },
    ) as test_client:
        yield test_client


@pytest_asyncio.fixture
async def unauthenticated_edge_client():
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 12345))
    async with Phase3TestClient(
        transport=transport,
        base_url="http://testserver",
        headers={"User-Agent": "EchoSense unauthenticated edge test"},
    ) as test_client:
        yield test_client


@pytest_asyncio.fixture
async def edge_device_identity():
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(EdgeDevice).where(EdgeDevice.device_code == TEST_DEVICE_CODE)
        )
        device = result.scalar_one()
        return {
            "id": device.id,
            "device_code": TEST_DEVICE_CODE,
            "device_key": TEST_DEVICE_KEY,
            "headers": {
                "X-EchoSense-Device-Id": TEST_DEVICE_CODE,
                "X-EchoSense-Device-Key": TEST_DEVICE_KEY,
            },
        }


def auth_headers(identity: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {identity['token']}"}
