import os
from urllib.parse import urlparse

import httpx
import pytest_asyncio
from sqlalchemy import text

TEST_DATABASE_URL = os.environ.get("ECHOSENSE_TEST_DATABASE_URL")
if not TEST_DATABASE_URL:
    raise RuntimeError(
        "ECHOSENSE_TEST_DATABASE_URL is required; tests refuse to inherit the application database"
    )

parsed_database_url = urlparse(TEST_DATABASE_URL)
if parsed_database_url.hostname not in {"127.0.0.1", "localhost", "::1"}:
    raise RuntimeError("Audit tests are restricted to a localhost PostgreSQL database")

os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["SECRET_KEY"] = "local-test-secret-not-for-production"
os.environ["RUN_LEGACY_STARTUP_MAINTENANCE"] = "false"
os.environ["SQL_ECHO"] = "false"
os.environ["TRUSTED_PROXY_CIDRS"] = ""
os.environ["TESTING"] = "true"

from app.database import AsyncSessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models.system_settings import SystemSettings  # noqa: E402
from app.models.user import User  # noqa: E402
from app.routers.auth import create_token, pwd_context  # noqa: E402


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
                    system_settings, alerts, users
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
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"User-Agent": "EchoSense audit test"},
    ) as test_client:
        yield test_client


def auth_headers(identity: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {identity['token']}"}
