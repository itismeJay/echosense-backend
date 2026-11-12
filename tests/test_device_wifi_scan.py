"""Remote Wi-Fi scan: queue a scan command, store the device's latest result, never passwords."""

import pytest
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.audit_log import AuditLog
from app.models.edge_device import EdgeDevice
from tests.conftest import TEST_DEVICE_CODE, auth_headers
from tests.test_device_wifi_management import (
    OTHER_DEVICE_CODE,
    _device_headers,
    _paths,
    _school_b_device,
)


def _scan_path(device_id):
    return f"/devices/{device_id}/wifi/scan"


async def _enable_scan(client, identity, supported=True):
    response = await client.post(
        "/system-settings/heartbeat",
        headers=identity["headers"],
        json={"wifi_remote_scan_supported": supported},
    )
    assert response.status_code == 200


async def _pull_one(client, identity):
    pulled = await client.get("/device/wifi/commands", headers=identity["headers"])
    [command] = pulled.json()["commands"]
    return command


async def _report(client, identity, command_id, **body):
    return await client.post(
        f"/device/wifi/commands/{command_id}/result", headers=identity["headers"], json=body
    )


@pytest.mark.asyncio
async def test_heartbeat_sets_and_clears_remote_scan_support(
    client, edge_device_identity, org_identities
):
    device_id = edge_device_identity["id"]
    admin = auth_headers(org_identities["admin_a"])
    view = (await client.get(_paths(device_id)["read"], headers=admin)).json()
    assert view["remote_scan_supported"] is False and view["last_scan"] is None

    await _enable_scan(client, edge_device_identity)
    view = (await client.get(_paths(device_id)["read"], headers=admin)).json()
    assert view["remote_scan_supported"] is True
    device = (await client.get(f"/devices/{device_id}", headers=admin)).json()
    assert device["wifi_remote_scan_supported"] is True

    # A heartbeat without the field leaves it unchanged; an explicit false clears it.
    await client.post(
        "/system-settings/heartbeat", headers=edge_device_identity["headers"], json={}
    )
    async with AsyncSessionLocal() as session:
        stored = await session.scalar(
            select(EdgeDevice).where(EdgeDevice.device_code == TEST_DEVICE_CODE)
        )
        assert stored.wifi_remote_scan_supported is True
    await _enable_scan(client, edge_device_identity, supported=False)
    view = (await client.get(_paths(device_id)["read"], headers=admin)).json()
    assert view["remote_scan_supported"] is False


@pytest.mark.asyncio
async def test_scan_requires_device_support(client, edge_device_identity, org_identities):
    response = await client.post(
        _scan_path(edge_device_identity["id"]), headers=auth_headers(org_identities["admin_a"])
    )
    assert response.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin_a", "super"])
async def test_scan_flow_stores_deduped_sorted_result(
    client, edge_device_identity, org_identities, role
):
    device_id = edge_device_identity["id"]
    headers = auth_headers(org_identities[role])
    await _enable_scan(client, edge_device_identity)

    queued = await client.post(_scan_path(device_id), headers=headers)
    assert queued.status_code == 202
    assert queued.json()["action"] == "scan" and queued.json()["status"] == "pending"
    # A second request while one is pending reuses it.
    again = await client.post(_scan_path(device_id), headers=headers)
    assert again.status_code == 202 and again.json()["id"] == queued.json()["id"]

    command = await _pull_one(client, edge_device_identity)
    assert command["action"] == "scan" and command["password"] is None

    networks = [
        {"ssid": "Lab", "signal": 40, "security": "WPA", "known": False},
        {"ssid": "School Staff", "signal": 55, "security": "wpa", "known": True},
        {"ssid": "Lab", "signal": 80, "security": "wpa", "known": False},
        {"ssid": "Guest", "signal": 90, "security": "open"},
        {"ssid": "", "signal": 99, "security": "wpa"},  # hidden network, dropped
        {"ssid": "Sneaky", "signal": 10, "security": "wpa", "password": "never-stored"},
    ] + [{"ssid": f"Far {i:02d}", "signal": 1, "security": "wpa"} for i in range(60)]
    result = await _report(
        client, edge_device_identity, command["id"], status="applied", networks=networks
    )
    assert result.status_code == 200

    view = (await client.get(_paths(device_id)["read"], headers=headers)).json()
    assert view["pending_commands"] == []
    scan = view["last_scan"]
    assert scan["scanned_at"]
    assert len(scan["networks"]) == 50
    assert scan["networks"][:4] == [
        {"ssid": "Guest", "signal": 90, "security": "open", "known": False},
        {"ssid": "Lab", "signal": 80, "security": "wpa", "known": False},
        {"ssid": "School Staff", "signal": 55, "security": "wpa", "known": True},
        {"ssid": "Sneaky", "signal": 10, "security": "wpa", "known": False},
    ]
    assert "never-stored" not in view.__repr__()
    async with AsyncSessionLocal() as session:
        stored = await session.scalar(
            select(EdgeDevice).where(EdgeDevice.device_code == TEST_DEVICE_CODE)
        )
        assert "never-stored" not in repr(stored.wifi_last_scan)
        assert all(set(n) == {"ssid", "signal", "security", "known"} for n in stored.wifi_last_scan)
        audit = await session.scalar(
            select(AuditLog).where(AuditLog.action == "SCAN_WIFI_NETWORKS")
        )
        assert audit is not None


@pytest.mark.asyncio
async def test_failed_scan_keeps_previous_result(client, edge_device_identity, org_identities):
    device_id = edge_device_identity["id"]
    headers = auth_headers(org_identities["admin_a"])
    await _enable_scan(client, edge_device_identity)

    await client.post(_scan_path(device_id), headers=headers)
    command = await _pull_one(client, edge_device_identity)
    await _report(
        client,
        edge_device_identity,
        command["id"],
        status="applied",
        networks=[{"ssid": "Guest", "signal": 70, "security": "open", "known": False}],
    )
    first = (await client.get(_paths(device_id)["read"], headers=headers)).json()["last_scan"]

    await client.post(_scan_path(device_id), headers=headers)
    command = await _pull_one(client, edge_device_identity)
    failed = await _report(
        client, edge_device_identity, command["id"], status="failed", error="radio busy"
    )
    assert failed.status_code == 200
    view = (await client.get(_paths(device_id)["read"], headers=headers)).json()
    assert view["last_scan"] == first


@pytest.mark.asyncio
async def test_bad_scan_entries_are_rejected(client, edge_device_identity, org_identities):
    await _enable_scan(client, edge_device_identity)
    await client.post(
        _scan_path(edge_device_identity["id"]), headers=auth_headers(org_identities["admin_a"])
    )
    command = await _pull_one(client, edge_device_identity)
    response = await _report(
        client,
        edge_device_identity,
        command["id"],
        status="applied",
        networks=[{"ssid": "Guest", "signal": 150, "security": "open"}],
    )
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["counselor_a", "staff_a", "admin_b", "staff_b"])
async def test_scan_forbidden_for_other_roles_and_schools(
    client, edge_device_identity, org_identities, role
):
    await _enable_scan(client, edge_device_identity)
    response = await client.post(
        _scan_path(edge_device_identity["id"]), headers=auth_headers(org_identities[role])
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_last_scan_hidden_from_read_only_roles(client, edge_device_identity, org_identities):
    device_id = edge_device_identity["id"]
    await _enable_scan(client, edge_device_identity)
    await client.post(_scan_path(device_id), headers=auth_headers(org_identities["admin_a"]))
    command = await _pull_one(client, edge_device_identity)
    await _report(
        client,
        edge_device_identity,
        command["id"],
        status="applied",
        networks=[{"ssid": "Guest", "signal": 70, "security": "open"}],
    )
    view = (
        await client.get(_paths(device_id)["read"], headers=auth_headers(org_identities["staff_a"]))
    ).json()
    assert view["last_scan"] is None


@pytest.mark.asyncio
async def test_other_device_cannot_report_scan(client, edge_device_identity, org_identities):
    await _school_b_device(org_identities)
    await _enable_scan(client, edge_device_identity)
    await client.post(
        _scan_path(edge_device_identity["id"]), headers=auth_headers(org_identities["admin_a"])
    )
    command = await _pull_one(client, edge_device_identity)
    response = await client.post(
        f"/device/wifi/commands/{command['id']}/result",
        headers=_device_headers(OTHER_DEVICE_CODE),
        json={"status": "applied", "networks": []},
    )
    assert response.status_code == 404
