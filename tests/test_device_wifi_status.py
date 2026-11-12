"""Authenticated heartbeat Wi-Fi status and administrator response contract."""

import pytest
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.edge_device import EdgeDevice
from tests.conftest import TEST_DEVICE_CODE, auth_headers


WIFI_HEARTBEAT = {
    "ip": "192.168.50.8",
    "ssid": "School Staff",
    "wifi_status": "connected",
    "wifi_change_method": "local_portal",
    "wifi_local_url": "http://192.168.50.8/",
    "wifi_remote_scan_supported": False,
    "stt_model_version": "ignored legacy-compatible field",
}


@pytest.mark.asyncio
async def test_authenticated_heartbeat_stores_non_secret_wifi_status_for_admins(
    client, edge_device_identity, org_identities
):
    heartbeat = await client.post(
        "/system-settings/heartbeat",
        headers=edge_device_identity["headers"],
        json=WIFI_HEARTBEAT,
    )
    devices = await client.get("/devices", headers=auth_headers(org_identities["admin_a"]))

    assert heartbeat.status_code == 200
    assert devices.status_code == 200
    device = devices.json()[0]
    assert device["wifi_status"] == "connected"
    assert device["wifi_ssid"] == "School Staff"
    assert device["wifi_local_url"] == "http://192.168.50.8/"
    assert "password" not in devices.text.casefold()

    async with AsyncSessionLocal() as session:
        stored = await session.scalar(
            select(EdgeDevice).where(EdgeDevice.device_code == TEST_DEVICE_CODE)
        )
        assert stored.wifi_status == "connected"
        assert stored.wifi_ssid == "School Staff"
        assert stored.wifi_local_url == "http://192.168.50.8/"


@pytest.mark.asyncio
async def test_teacher_status_response_does_not_expose_wifi_management_fields(
    client, edge_device_identity, org_identities
):
    await client.post(
        "/system-settings/heartbeat",
        headers=edge_device_identity["headers"],
        json=WIFI_HEARTBEAT,
    )

    response = await client.get("/devices/status", headers=auth_headers(org_identities["staff_a"]))

    assert response.status_code == 200
    assert (
        not {
            "wifi_status",
            "wifi_ssid",
            "wifi_local_url",
        }
        & response.json()["devices"][0].keys()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe_fields",
    [
        {"wifi_password": "must-never-be-accepted"},
        {"psk": "must-never-be-accepted"},
        {"wifi_local_url": "https://192.168.50.8/"},
        {"wifi_local_url": "http://203.0.113.10/"},
        {"wifi_local_url": "http://device.example.test/"},
    ],
)
async def test_heartbeat_rejects_secrets_and_nonlocal_portals(
    client, edge_device_identity, unsafe_fields
):
    response = await client.post(
        "/system-settings/heartbeat",
        headers=edge_device_identity["headers"],
        json={**WIFI_HEARTBEAT, **unsafe_fields},
    )

    assert response.status_code == 422
    assert "must-never-be-accepted" not in response.text
    async with AsyncSessionLocal() as session:
        stored = await session.scalar(
            select(EdgeDevice).where(EdgeDevice.device_code == TEST_DEVICE_CODE)
        )
        assert stored.wifi_status is None
        assert stored.wifi_ssid is None
        assert stored.wifi_local_url is None


@pytest.mark.asyncio
async def test_legacy_heartbeat_without_body_keeps_existing_wifi_status(
    client, edge_device_identity
):
    async with AsyncSessionLocal() as session:
        device = await session.scalar(
            select(EdgeDevice).where(EdgeDevice.device_code == TEST_DEVICE_CODE)
        )
        device.wifi_status = "connected"
        device.wifi_ssid = "Existing Network"
        device.wifi_local_url = "http://10.0.0.12/"
        await session.commit()

    response = await client.post(
        "/system-settings/heartbeat", headers=edge_device_identity["headers"]
    )

    assert response.status_code == 200
    async with AsyncSessionLocal() as session:
        stored = await session.scalar(
            select(EdgeDevice).where(EdgeDevice.device_code == TEST_DEVICE_CODE)
        )
        assert stored.wifi_status == "connected"
        assert stored.wifi_ssid == "Existing Network"
        assert stored.wifi_local_url == "http://10.0.0.12/"
