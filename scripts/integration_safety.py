#!/usr/bin/env python3
"""Fail closed unless integration mode targets the existing Neon database safely."""

import hmac
import os
from urllib.parse import urlparse

from dotenv import dotenv_values, load_dotenv


def validate_integration_environment(source_database_url: str | None = None) -> None:
    if os.environ.get("ENVIRONMENT") != "integration":
        raise RuntimeError("ENVIRONMENT must be integration")

    database_url = os.environ.get("DATABASE_URL", "")
    parsed = urlparse(database_url)
    if parsed.scheme not in {"postgresql", "postgresql+asyncpg"}:
        raise RuntimeError("DATABASE_URL must select PostgreSQL")
    hostname = (parsed.hostname or "").casefold()
    if not hostname.endswith(".neon.tech"):
        raise RuntimeError("Integration DATABASE_URL must select Neon")
    if source_database_url is not None and not hmac.compare_digest(
        database_url, source_database_url
    ):
        raise RuntimeError("Integration DATABASE_URL does not match the existing private .env")

    if os.environ.get("ALEMBIC_DATABASE_URL"):
        raise RuntimeError("ALEMBIC_DATABASE_URL must be unset in integration mode")
    if os.environ.get("ECHOSENSE_TEST_DATABASE_URL"):
        raise RuntimeError("ECHOSENSE_TEST_DATABASE_URL must be unset in integration mode")
    if os.environ.get("RUN_LEGACY_STARTUP_MAINTENANCE", "").casefold() != "false":
        raise RuntimeError("RUN_LEGACY_STARTUP_MAINTENANCE must be false")
    if os.environ.get("ECHOSENSE_ALLOW_TEST_ALERTS", "").casefold() != "false":
        raise RuntimeError("ECHOSENSE_ALLOW_TEST_ALERTS must be false")
    if os.environ.get("TESTING", "").casefold() != "false":
        raise RuntimeError("TESTING must be false")


def main() -> None:
    load_dotenv(".env.integration", override=True)
    source_database_url = dotenv_values(".env").get("DATABASE_URL")
    validate_integration_environment(source_database_url)
    print("Integration safety check: pass")


if __name__ == "__main__":
    main()
