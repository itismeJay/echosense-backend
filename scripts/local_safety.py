#!/usr/bin/env python3
"""Fail closed unless local tooling targets isolated loopback PostgreSQL databases."""

import os
from urllib.parse import urlparse

from dotenv import load_dotenv

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _database_identity(variable: str) -> tuple[str, int, str, str | None]:
    value = os.environ.get(variable)
    if not value:
        raise RuntimeError(f"{variable} is required")
    parsed = urlparse(value)
    if parsed.scheme not in {
        "postgresql",
        "postgresql+asyncpg",
        "postgresql+psycopg2",
    }:
        raise RuntimeError(f"{variable} must select PostgreSQL")
    if parsed.hostname not in LOCAL_HOSTS:
        raise RuntimeError(f"{variable} must use a loopback host, not {parsed.hostname!r}")
    database = parsed.path.lstrip("/")
    if not database:
        raise RuntimeError(f"{variable} must select a database")
    return parsed.hostname, parsed.port or 5432, database, parsed.username


def validate_local_environment() -> None:
    if os.environ.get("ENVIRONMENT") != "development":
        raise RuntimeError("ENVIRONMENT must be development")

    expected_port = int(os.environ.get("ECHOSENSE_POSTGRES_PORT", "55432"))
    expected_database = os.environ.get("ECHOSENSE_POSTGRES_DB", "echosense_dev")
    expected_test_database = os.environ.get("ECHOSENSE_POSTGRES_TEST_DB", "echosense_test")
    expected_user = os.environ.get("ECHOSENSE_POSTGRES_USER", "echosense_dev")

    if not expected_database.endswith("_dev"):
        raise RuntimeError("ECHOSENSE_POSTGRES_DB must end in _dev")
    if not expected_test_database.endswith("_test"):
        raise RuntimeError("ECHOSENSE_POSTGRES_TEST_DB must end in _test")
    if expected_database == expected_test_database:
        raise RuntimeError("Development and test databases must be different")

    for variable, expected_name in (
        ("DATABASE_URL", expected_database),
        ("ALEMBIC_DATABASE_URL", expected_database),
        ("ECHOSENSE_TEST_DATABASE_URL", expected_test_database),
    ):
        _, port, database, username = _database_identity(variable)
        if port != expected_port:
            raise RuntimeError(f"{variable} must use local port {expected_port}")
        if database != expected_name:
            raise RuntimeError(f"{variable} must select {expected_name}")
        if username != expected_user:
            raise RuntimeError(f"{variable} must use local user {expected_user}")


def main() -> None:
    load_dotenv(".env.local", override=True)
    validate_local_environment()
    print("Local database safety check: pass")


if __name__ == "__main__":
    main()
