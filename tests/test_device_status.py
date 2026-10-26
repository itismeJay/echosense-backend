"""Device status comes from each device's own last check-in, not the global heartbeat."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.edge_device import EdgeDevice
from app.models.system_settings import SystemSettings
from app.services.device_status import ONLINE_WINDOW_SECONDS, is_device_online
from tests.conftest import TEST_DEVICE_CODE, auth_headers


def test_online_means_seen_in_the_last_two_minutes():
    now = datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc)

    assert ONLINE_WINDOW_SECONDS == 120
    assert is_device_online(now - timedelta(seconds=119), now)
    assert is_device_online(now - timedelta(seconds=120), now)
    assert not is_device_online(now - timedelta(seconds=121), now)
    assert not is_device_online(None, now)
    # Naive timestamps are treated as UTC.
    assert is_device_online((now - timedelta(seconds=30)).replace(tzinfo=None), now)


async def _set_last_seen(value):
    async with AsyncSessionLocal() as session:
        device = await session.scalar(
            select(EdgeDevice).where(EdgeDevice.device_code == TEST_DEVICE_CODE)
        )
        device.last_seen_at = value
        await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["staff_a", "counselor_a", "admin_a"])
async def test_every_school_role_sees_its_devices_status(client, org_identities, role):
    await _set_last_seen(datetime.now(timezone.utc) - timedelta(seconds=30))

    response = await client.get("/devices/status", headers=auth_headers(org_identities[role]))

    assert response.status_code == 200
    body = response.json()
    assert body["online_window_seconds"] == 120
    assert [(device["device_code"], device["online"]) for device in body["devices"]] == [
        (TEST_DEVICE_CODE, True)
    ]
    assert body["devices"][0]["classroom_name"] == "Synthetic Test Classroom"


@pytest.mark.asyncio
async def test_a_device_seen_three_minutes_ago_is_offline_with_its_last_check_in(
    client, org_identities
):
    seen = datetime.now(timezone.utc) - timedelta(minutes=3)
    await _set_last_seen(seen)

    response = await client.get("/devices/status", headers=auth_headers(org_identities["staff_a"]))

    device = response.json()["devices"][0]
    assert device["online"] is False
    assert datetime.fromisoformat(device["last_seen_at"]) == seen


@pytest.mark.asyncio
async def test_global_heartbeat_does_not_make_a_silent_device_online(
    client, unauthenticated_edge_client, org_identities
):
    await _set_last_seen(None)

    anonymous = await unauthenticated_edge_client.post("/system-settings/heartbeat")
    response = await client.get("/devices/status", headers=auth_headers(org_identities["admin_a"]))

    assert anonymous.status_code == 200
    assert anonymous.json()["device_recognized"] is False
    assert response.json()["devices"][0]["online"] is False
    async with AsyncSessionLocal() as session:
        settings_row = await session.scalar(select(SystemSettings))
        assert settings_row.device_status == "online"


@pytest.mark.asyncio
async def test_heartbeat_with_device_credentials_records_that_devices_check_in(
    client, edge_device_identity, org_identities
):
    await _set_last_seen(None)

    heartbeat = await client.post(
        "/system-settings/heartbeat", headers=edge_device_identity["headers"]
    )
    response = await client.get("/devices/status", headers=auth_headers(org_identities["staff_a"]))

    assert heartbeat.status_code == 200
    assert heartbeat.json()["device_recognized"] is True
    assert response.json()["devices"][0]["online"] is True


@pytest.mark.asyncio
async def test_heartbeat_with_a_wrong_key_records_nothing(unauthenticated_edge_client):
    await _set_last_seen(None)

    heartbeat = await unauthenticated_edge_client.post(
        "/system-settings/heartbeat",
        headers={"X-EchoSense-Device-Id": TEST_DEVICE_CODE, "X-EchoSense-Device-Key": "wrong-key"},
    )

    assert heartbeat.status_code == 200
    assert heartbeat.json()["device_recognized"] is False
    async with AsyncSessionLocal() as session:
        device = await session.scalar(
            select(EdgeDevice).where(EdgeDevice.device_code == TEST_DEVICE_CODE)
        )
        assert device.last_seen_at is None


@pytest.mark.asyncio
async def test_disabled_device_is_never_online(client, edge_device_identity, org_identities):
    async with AsyncSessionLocal() as session:
        device = await session.scalar(
            select(EdgeDevice).where(EdgeDevice.device_code == TEST_DEVICE_CODE)
        )
        device.is_active = False
        device.last_seen_at = datetime.now(timezone.utc)
        await session.commit()

    heartbeat = await client.post(
        "/system-settings/heartbeat", headers=edge_device_identity["headers"]
    )
    response = await client.get("/devices/status", headers=auth_headers(org_identities["admin_a"]))

    assert heartbeat.json()["device_recognized"] is False
    assert response.json()["devices"][0]["online"] is False


@pytest.mark.asyncio
async def test_other_schools_devices_stay_hidden(client, org_identities):
    response = await client.get("/devices/status", headers=auth_headers(org_identities["staff_b"]))
    forbidden = await client.get(
        "/devices/status",
        params={"school_id": str(org_identities["school_a_id"])},
        headers=auth_headers(org_identities["staff_b"]),
    )

    assert response.json()["devices"] == []
    assert forbidden.status_code == 403


@pytest.mark.asyncio
async def test_super_admin_sees_every_school_or_one(client, org_identities):
    headers = auth_headers(org_identities["super"])

    everything = await client.get("/devices/status", headers=headers)
    school_b = await client.get(
        "/devices/status", params={"school_id": str(org_identities["school_b_id"])}, headers=headers
    )

    assert len(everything.json()["devices"]) == 1
    assert school_b.json()["devices"] == []


@pytest.mark.asyncio
async def test_device_status_needs_sign_in(unauthenticated_edge_client):
    response = await unauthenticated_edge_client.get("/devices/status")

    assert response.status_code == 401
