#!/usr/bin/env python3
"""Verify Neon connectivity and schema using a read-only transaction."""

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))
load_dotenv(REPOSITORY_ROOT / ".env.integration", override=True)

from app.database import engine  # noqa: E402
from scripts.integration_safety import validate_integration_environment  # noqa: E402

EXPECTED_MIGRATION = "20261021_0012"
REQUIRED_TABLES = frozenset({"alerts", "edge_devices", "users"})
REQUIRED_ALERT_COLUMNS = frozenset(
    {
        "classroom_name_snapshot",
        "event_id",
        "request_fingerprint",
        "schema_version",
        "school_name_snapshot",
        "severity_reasons",
        "trigger_type",
    }
)


async def check() -> None:
    validate_integration_environment()
    async with engine.connect() as connection:
        async with connection.begin():
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            read_only = await connection.scalar(text("SHOW transaction_read_only"))
            migration = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            tables = set(
                (
                    await connection.execute(
                        text(
                            """
                            SELECT table_name
                            FROM information_schema.tables
                            WHERE table_schema = 'public'
                              AND table_name IN ('alerts', 'edge_devices', 'users')
                            """
                        )
                    )
                ).scalars()
            )
            alert_columns = set(
                (
                    await connection.execute(
                        text(
                            """
                            SELECT column_name
                            FROM information_schema.columns
                            WHERE table_schema = 'public'
                              AND table_name = 'alerts'
                            """
                        )
                    )
                ).scalars()
            )
            existing_data = await connection.scalar(
                text(
                    """
                    SELECT EXISTS (SELECT 1 FROM users LIMIT 1)
                        OR EXISTS (SELECT 1 FROM edge_devices LIMIT 1)
                        OR EXISTS (SELECT 1 FROM alerts LIMIT 1)
                    """
                )
            )

    await engine.dispose()
    if read_only != "on":
        raise RuntimeError("Neon verification transaction was not read-only")
    if migration != EXPECTED_MIGRATION:
        raise RuntimeError(
            f"Neon schema is at {migration!r}; expected {EXPECTED_MIGRATION}. No migration was run."
        )
    if not REQUIRED_TABLES <= tables:
        raise RuntimeError("Neon is missing required application tables")
    if not REQUIRED_ALERT_COLUMNS <= alert_columns:
        raise RuntimeError("Neon alerts schema is missing finalized columns")
    if not existing_data:
        raise RuntimeError("Neon schema is readable but expected existing data was not found")

    print("Neon connectivity: pass")
    print(f"Schema: {EXPECTED_MIGRATION}")
    print("Existing data readable: pass")
    print("Verification transaction: read-only")


if __name__ == "__main__":
    asyncio.run(check())
