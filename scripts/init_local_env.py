#!/usr/bin/env python3
"""Create a private local-development environment without displaying secrets."""

import os
import secrets
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = REPOSITORY_ROOT / ".env.local.example"
TARGET_PATH = REPOSITORY_ROOT / ".env.local"


def main() -> None:
    if TARGET_PATH.exists():
        print(".env.local already exists; left unchanged")
        return

    content = TEMPLATE_PATH.read_text(encoding="utf-8")
    replacements = {
        "replace-with-generated-local-postgres-password": secrets.token_urlsafe(24),
        "replace-with-generated-local-secret-key": secrets.token_urlsafe(48),
        "replace-with-generated-local-admin-password": secrets.token_urlsafe(18),
        "replace-with-generated-local-staff-password": secrets.token_urlsafe(18),
        "replace-with-generated-local-counselor-password": secrets.token_urlsafe(18),
    }
    for placeholder, secret in replacements.items():
        content = content.replace(placeholder, secret)
    if "replace-with-generated" in content:
        raise RuntimeError("The local environment template contains an unknown placeholder")

    descriptor = os.open(TARGET_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, content.encode("utf-8"))
    finally:
        os.close(descriptor)
    print("Created private .env.local with generated local-only credentials")


if __name__ == "__main__":
    main()
