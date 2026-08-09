#!/usr/bin/env python3
"""Seed a controlled dataset in the isolated local development database."""

import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx
from dotenv import dotenv_values, load_dotenv
from sqlalchemy import select

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))
load_dotenv(REPOSITORY_ROOT / ".env.local", override=True)

from app.database import AsyncSessionLocal  # noqa: E402
from app.models.user import User  # noqa: E402
from app.routers.auth import pwd_context  # noqa: E402
from scripts.local_safety import validate_local_environment  # noqa: E402


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required in .env.local")
    return value


def _local_api_url() -> str:
    value = _required("ECHOSENSE_LOCAL_API_URL").rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("ECHOSENSE_LOCAL_API_URL must use HTTP on a loopback host")
    return value


def _credential_path() -> Path:
    configured = _required("ECHOSENSE_DEVICE_CREDENTIALS_FILE")
    path = (REPOSITORY_ROOT / configured).resolve()
    if path.parent != REPOSITORY_ROOT or not path.name.startswith(".env.local"):
        raise RuntimeError("Device credentials must use a .env.local* file in the repository")
    return path


async def _bootstrap_admin(email: str, password: str) -> str:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.email == email))
        admin = result.scalar_one_or_none()
        if admin is None:
            session.add(
                User(
                    email=email,
                    hashed_password=pwd_context.hash(password),
                    role="admin",
                )
            )
            await session.commit()
            return "created"
        if admin.role != "admin":
            raise RuntimeError("The configured local admin email has a non-admin role")
        return "existing"


def _write_device_credentials(path: Path, device_code: str, device_key: str) -> None:
    content = f"ECHOSENSE_DEVICE_ID={device_code}\nECHOSENSE_DEVICE_KEY={device_key}\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, content.encode("utf-8"))
    finally:
        os.close(descriptor)


async def seed() -> None:
    validate_local_environment()
    api_url = _local_api_url()
    admin_email = _required("ECHOSENSE_DEV_ADMIN_EMAIL")
    admin_password = _required("ECHOSENSE_DEV_ADMIN_PASSWORD")
    credentials_path = _credential_path()

    states = {"admin": await _bootstrap_admin(admin_email, admin_password)}

    async with httpx.AsyncClient(base_url=api_url, timeout=15.0) as client:
        health = await client.get("/health")
        health.raise_for_status()
        login = await client.post(
            "/auth/login",
            json={"email": admin_email, "password": admin_password},
        )
        if login.status_code != 200:
            raise RuntimeError(
                "Local admin login failed. Keep .env.local credentials aligned with the local DB."
            )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        users_response = await client.get("/users/", headers=headers)
        users_response.raise_for_status()
        users = {user["email"]: user for user in users_response.json()}
        for role in ("staff", "counselor"):
            email = _required(f"ECHOSENSE_DEV_{role.upper()}_EMAIL")
            password = _required(f"ECHOSENSE_DEV_{role.upper()}_PASSWORD")
            existing = users.get(email)
            if existing is not None:
                if existing["role"] != role:
                    raise RuntimeError(f"Configured {role} email already has another role")
                states[role] = "existing"
                continue
            response = await client.post(
                "/users/",
                headers=headers,
                json={"email": email, "password": password, "role": role},
            )
            response.raise_for_status()
            states[role] = "created"

        device_code = _required("ECHOSENSE_DEV_DEVICE_CODE")
        device_payload = {
            "device_code": device_code,
            "display_name": _required("ECHOSENSE_DEV_DEVICE_NAME"),
            "classroom_name": _required("ECHOSENSE_DEV_CLASSROOM"),
            "school_name": _required("ECHOSENSE_DEV_SCHOOL"),
        }
        devices_response = await client.get("/devices", headers=headers)
        devices_response.raise_for_status()
        existing_device = next(
            (device for device in devices_response.json() if device["device_code"] == device_code),
            None,
        )
        if existing_device is None:
            if credentials_path.exists():
                raise RuntimeError(
                    f"{credentials_path.name} already exists but the local device does not"
                )
            response = await client.post("/devices", headers=headers, json=device_payload)
            response.raise_for_status()
            body = response.json()
            _write_device_credentials(credentials_path, device_code, body["device_key"])
            states["edge_device"] = "created"
        else:
            expected = {
                "display_name": device_payload["display_name"],
                "classroom_name": device_payload["classroom_name"],
                "school_name": device_payload["school_name"],
                "is_active": True,
            }
            changes = {
                key: value for key, value in expected.items() if existing_device[key] != value
            }
            if changes:
                response = await client.patch(
                    f"/devices/{existing_device['id']}", headers=headers, json=changes
                )
                response.raise_for_status()
            if not credentials_path.exists():
                raise RuntimeError(
                    "The device exists but its one-time local key file is missing. "
                    "Rotate the local device key through the admin API."
                )
            stored_credentials = dotenv_values(credentials_path)
            if stored_credentials.get("ECHOSENSE_DEVICE_ID") != device_code:
                raise RuntimeError("The local device credential file identifies another device")
            states["edge_device"] = "existing"

    states["school"] = states["edge_device"]
    states["classroom"] = states["edge_device"]
    print("EchoSense local seed")
    for label in ("admin", "staff", "counselor", "school", "classroom", "edge_device"):
        print(f"{label.replace('_', ' ').title()}: {states[label]}")
    print(f"Device key: stored privately in {credentials_path.name} (value not displayed)")


if __name__ == "__main__":
    asyncio.run(seed())
