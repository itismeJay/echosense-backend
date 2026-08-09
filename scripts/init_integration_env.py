#!/usr/bin/env python3
"""Create a private Neon integration environment without displaying secrets."""

import os
import shlex
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = REPOSITORY_ROOT / ".env"
TEMPLATE_PATH = REPOSITORY_ROOT / ".env.integration.example"
TARGET_PATH = REPOSITORY_ROOT / ".env.integration"


def _required(values: dict, name: str) -> str:
    value = values.get(name)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"The existing private .env does not contain {name}")
    return value.strip()


def main() -> None:
    if TARGET_PATH.exists():
        print(".env.integration already exists; left unchanged")
        return

    source = dotenv_values(SOURCE_PATH)
    database_url = _required(source, "DATABASE_URL")
    secret_key = _required(source, "SECRET_KEY")
    parsed = urlparse(database_url)
    if parsed.scheme not in {"postgresql", "postgresql+asyncpg"}:
        raise RuntimeError("The existing private DATABASE_URL must select PostgreSQL")
    if not (parsed.hostname or "").casefold().endswith(".neon.tech"):
        raise RuntimeError("The existing private DATABASE_URL is not a Neon endpoint")

    content = TEMPLATE_PATH.read_text(encoding="utf-8")
    replacements = {
        "copy-existing-neon-url-without-committing-it": shlex.quote(database_url),
        "copy-existing-secret-key-without-committing-it": shlex.quote(secret_key),
    }
    for placeholder, value in replacements.items():
        content = content.replace(placeholder, value)

    expo_token = source.get("EXPO_ACCESS_TOKEN")
    if isinstance(expo_token, str) and expo_token.strip():
        content = content.replace(
            "EXPO_ACCESS_TOKEN=", f"EXPO_ACCESS_TOKEN={shlex.quote(expo_token)}"
        )

    descriptor = os.open(TARGET_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, content.encode("utf-8"))
    finally:
        os.close(descriptor)
    print("Created private .env.integration from existing Neon configuration")
    print("Database credentials and secret values were not displayed")


if __name__ == "__main__":
    main()
