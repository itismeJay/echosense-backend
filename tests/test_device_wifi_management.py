"""Dashboard-managed device Wi-Fi: roles, school isolation, secrecy and the edge queue."""

import httpx
import pytest
from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.main import app
from app.models.audit_log import AuditLog
from app.models.device_wifi import DeviceWifiCommand, DeviceWifiNetwork
from app.models.edge_device import EdgeDevice
from app.models.classroom import Classroom
from tests.conftest import TEST_DEVICE_KEY, TEST_DEVICE_KEY_HASH, auth_headers

SECRET = "Sup3r-Secret-WiFi-Pass"
SECOND_SECRET = "An0ther-Secret-Pass!"
OTHER_DEVICE_CODE = "school-b-test-pi"


def _paths(device_id):
    base = f"/devices/{device_id}/wifi"
    return {
        "read": base,
        "networks": f"{base}/networks",
        "network": lambda ssid: f"{base}/networks/{ssid}",
        "connect": f"{base}/connect",
    }


async def _add(client, identity, device_id, **body):
    body.setdefault("ssid", "School Staff")
    return await client.post(
        _paths(device_id)["networks"], headers=auth_headers(identity), json=body
    )


async def _school_b_device(org_identities):
    async with AsyncSessionLocal() as session:
        classroom = Classroom(school_id=org_identities["school_b_id"], name="Room B")
        session.add(classroom)
        await session.flush()
        device = EdgeDevice(
            device_code=OTHER_DEVICE_CODE,
            display_name="School B Pi",
            school_id=org_identities["school_b_id"],
            classroom_id=classroom.id,
            api_key_hash=TEST_DEVICE_KEY_HASH,
        )
        session.add(device)
        await session.commit()
        return device.id


def _device_headers(code):
    return {"X-EchoSense-Device-Id": code, "X-EchoSense-Device-Key": TEST_DEVICE_KEY}


@pytest.mark.asyncio
async def test_admin_flow_queues_commands_and_device_applies_them(
    client, edge_device_identity, org_identities
):
    device_id = edge_device_identity["id"]
    admin = org_identities["admin_a"]

    added = await _add(client, admin, device_id, password=SECRET, priority=10)
    assert added.status_code == 202
    assert added.json()["action"] == "add" and added.json()["status"] == "pending"

    view = await client.get(_paths(device_id)["read"], headers=auth_headers(admin))
    assert view.status_code == 200
    data = view.json()
    assert data["can_manage"] is True
    assert data["networks"] == [
        {
            "ssid": "School Staff",
            "priority": 10,
            "has_password": True,
            "last_status": "pending",
            "last_error": None,
            "updated_at": data["networks"][0]["updated_at"],
        }
    ]
    assert [c["action"] for c in data["pending_commands"]] == ["add"]

    pulled = await client.get("/device/wifi/commands", headers=edge_device_identity["headers"])
    assert pulled.status_code == 200
    assert pulled.headers["cache-control"] == "no-store"
    [command] = pulled.json()["commands"]
    assert command["password"] == SECRET and command["password_withheld"] is False
    assert command["priority"] == 10

    result = await client.post(
        f"/device/wifi/commands/{command['id']}/result",
        headers=edge_device_identity["headers"],
        json={"status": "applied", "current_ssid": "School Staff"},
    )
    assert result.status_code == 200 and result.json()["status"] == "applied"

    again = await client.post(
        f"/device/wifi/commands/{command['id']}/result",
        headers=edge_device_identity["headers"],
        json={"status": "applied"},
    )
    assert again.status_code == 409

    view = (await client.get(_paths(device_id)["read"], headers=auth_headers(admin))).json()
    assert view["networks"][0]["last_status"] == "applied"
    assert view["pending_commands"] == []
    assert view["current"]["ssid"] == "School Staff"
    assert view["current"]["wifi_status"] == "connected"
    assert (
        await client.get("/device/wifi/commands", headers=edge_device_identity["headers"])
    ).json() == {"commands": []}

    async with AsyncSessionLocal() as session:
        stored = await session.scalar(select(DeviceWifiCommand))
        assert stored.password_encrypted is None  # cleared once delivered
        network = await session.scalar(select(DeviceWifiNetwork))
        assert network.password_encrypted and SECRET not in network.password_encrypted


@pytest.mark.asyncio
async def test_update_priority_remove_and_connect_create_commands(
    client, edge_device_identity, org_identities
):
    device_id = edge_device_identity["id"]
    admin = org_identities["super"]
    paths = _paths(device_id)
    await _add(client, admin, device_id, password=SECRET)
    await _add(client, admin, device_id, ssid="Guest/2.4GHz")

    priority = await client.patch(
        paths["network"]("Guest/2.4GHz"), headers=auth_headers(admin), json={"priority": 50}
    )
    password = await client.patch(
        paths["network"]("School Staff"),
        headers=auth_headers(admin),
        json={"password": SECOND_SECRET},
    )
    connect = await client.post(
        paths["connect"], headers=auth_headers(admin), json={"ssid": "School Staff"}
    )
    removed = await client.delete(paths["network"]("Guest/2.4GHz"), headers=auth_headers(admin))
    missing = await client.delete(paths["network"]("Nope"), headers=auth_headers(admin))
    empty = await client.patch(
        paths["network"]("School Staff"), headers=auth_headers(admin), json={}
    )

    assert [r.status_code for r in (priority, password, connect, removed)] == [202] * 4
    assert priority.json()["action"] == "set_priority" and priority.json()["priority"] == 50
    assert password.json()["action"] == "update"
    assert connect.json()["action"] == "connect_now"
    assert removed.json()["action"] == "remove"
    assert missing.status_code == 404
    assert empty.status_code == 422

    commands = (
        await client.get("/device/wifi/commands", headers=edge_device_identity["headers"])
    ).json()["commands"]
    assert [(c["action"], c["ssid"]) for c in commands] == [
        ("add", "School Staff"),
        ("add", "Guest/2.4GHz"),
        ("set_priority", "Guest/2.4GHz"),
        ("update", "School Staff"),
        ("connect_now", "School Staff"),
        ("remove", "Guest/2.4GHz"),
    ]
    assert [c["password"] for c in commands] == [SECRET, None, None, SECOND_SECRET, None, None]

    failed = await client.post(
        f"/device/wifi/commands/{commands[3]['id']}/result",
        headers=edge_device_identity["headers"],
        json={"status": "failed", "error": "auth timeout", "current_ssid": None},
    )
    assert failed.status_code == 200
    view = (await client.get(paths["read"], headers=auth_headers(admin))).json()
    assert [n["ssid"] for n in view["networks"]] == ["School Staff"]
    assert view["networks"][0]["last_status"] == "failed"
    assert view["networks"][0]["last_error"] == "auth timeout"
    assert view["current"]["wifi_status"] == "disconnected"

    dup = await _add(client, admin, device_id)
    assert dup.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["counselor_a", "staff_a"])
async def test_counselor_and_teacher_get_read_only_status(
    client, edge_device_identity, org_identities, role
):
    device_id = edge_device_identity["id"]
    paths = _paths(device_id)
    await _add(client, org_identities["admin_a"], device_id, password=SECRET)
    await client.post(
        "/system-settings/heartbeat",
        headers=edge_device_identity["headers"],
        json={"ssid": "School Staff", "wifi_status": "connected", "wifi_signal": 72},
    )
    headers = auth_headers(org_identities[role])

    view = await client.get(paths["read"], headers=headers)
    assert view.status_code == 200
    data = view.json()
    assert data["can_manage"] is False
    assert data["networks"] == [] and data["pending_commands"] == []
    assert data["current"]["ssid"] == "School Staff"
    assert data["current"]["signal"] == 72
    assert data["current"]["online"] is True

    writes = [
        await client.post(paths["networks"], headers=headers, json={"ssid": "X"}),
        await client.patch(paths["network"]("School Staff"), headers=headers, json={"priority": 1}),
        await client.delete(paths["network"]("School Staff"), headers=headers),
        await client.post(paths["connect"], headers=headers, json={"ssid": "School Staff"}),
    ]
    assert [w.status_code for w in writes] == [403] * 4


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin_b", "staff_b"])
async def test_other_school_cannot_read_or_change_wifi(
    client, edge_device_identity, org_identities, role
):
    device_id = edge_device_identity["id"]
    await _add(client, org_identities["admin_a"], device_id, password=SECRET)
    headers = auth_headers(org_identities[role])
    paths = _paths(device_id)

    responses = [
        await client.get(paths["read"], headers=headers),
        await client.post(paths["networks"], headers=headers, json={"ssid": "Evil"}),
        await client.patch(paths["network"]("School Staff"), headers=headers, json={"priority": 1}),
        await client.delete(paths["network"]("School Staff"), headers=headers),
        await client.post(paths["connect"], headers=headers, json={"ssid": "School Staff"}),
    ]
    assert [r.status_code for r in responses] == [403] * 5
    assert all("School Staff" not in r.text for r in responses)


@pytest.mark.asyncio
async def test_device_sees_and_reports_only_its_own_commands(
    client, edge_device_identity, org_identities
):
    other_id = await _school_b_device(org_identities)
    await _add(client, org_identities["admin_a"], edge_device_identity["id"], ssid="A-Net")
    await _add(client, org_identities["admin_b"], other_id, ssid="B-Net", password=SECRET)

    mine = (
        await client.get("/device/wifi/commands", headers=edge_device_identity["headers"])
    ).json()["commands"]
    theirs = (
        await client.get("/device/wifi/commands", headers=_device_headers(OTHER_DEVICE_CODE))
    ).json()["commands"]
    assert [c["ssid"] for c in mine] == ["A-Net"]
    assert [c["ssid"] for c in theirs] == ["B-Net"]

    stolen = await client.post(
        f"/device/wifi/commands/{theirs[0]['id']}/result",
        headers=edge_device_identity["headers"],
        json={"status": "failed", "error": "x"},
    )
    assert stolen.status_code == 404
    async with AsyncSessionLocal() as session:
        command = await session.get(DeviceWifiCommand, theirs[0]["id"])
        assert command.status == "pending"

    no_key = await client.get(
        "/device/wifi/commands",
        headers={"X-EchoSense-Device-Id": OTHER_DEVICE_CODE, "X-EchoSense-Device-Key": "wrong-key"},
    )
    assert no_key.status_code == 401


@pytest.mark.asyncio
async def test_password_never_returned_logged_or_audited(
    client, edge_device_identity, org_identities
):
    device_id = edge_device_identity["id"]
    admin = org_identities["admin_a"]
    paths = _paths(device_id)
    texts = []

    texts.append((await _add(client, admin, device_id, password=SECRET)).text)
    texts.append(
        (
            await client.patch(
                paths["network"]("School Staff"),
                headers=auth_headers(admin),
                json={"password": SECOND_SECRET, "priority": 3},
            )
        ).text
    )
    bad = await _add(client, admin, device_id, ssid="Other", password="short\x01" + SECRET[:3])
    assert bad.status_code == 422
    texts.append(bad.text)
    bad_ssid = await _add(client, admin, device_id, ssid="", password=SECRET)
    assert bad_ssid.status_code == 422
    texts.append(bad_ssid.text)
    texts.append((await client.get(paths["read"], headers=auth_headers(admin))).text)
    texts.append((await client.get(f"/devices/{device_id}", headers=auth_headers(admin))).text)
    texts.append((await client.get("/devices", headers=auth_headers(admin))).text)
    texts.append(
        (await client.get("/audit-logs", headers=auth_headers(org_identities["super"]))).text
    )

    for text in texts:
        assert SECRET not in text and SECOND_SECRET not in text
        assert "password_encrypted" not in text

    async with AsyncSessionLocal() as session:
        events = (
            await session.scalars(
                select(AuditLog).where(AuditLog.action.like("%WIFI%")).order_by(AuditLog.id)
            )
        ).all()
    assert [e.action for e in events] == ["ADD_WIFI_NETWORK", "UPDATE_WIFI_NETWORK"]
    assert events[0].metadata_json["ssid"] == "School Staff"
    assert events[1].metadata_json["passphrase_changed"] is True
    assert events[0].actor_email == admin["email"]
    assert events[0].school_id == org_identities["school_a_id"]
    for event in events:
        dumped = repr(event.metadata_json) + (event.description or "") + (event.target or "")
        assert SECRET not in dumped and SECOND_SECRET not in dumped


@pytest.mark.asyncio
async def test_password_withheld_over_untrusted_plain_http(
    client, edge_device_identity, org_identities
):
    await _add(client, org_identities["admin_a"], edge_device_identity["id"], password=SECRET)
    transport = httpx.ASGITransport(app=app, client=("93.184.216.34", 4000))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as remote:
        plain = await remote.get("/device/wifi/commands", headers=edge_device_identity["headers"])
        tls = await remote.get(
            "/device/wifi/commands",
            headers={**edge_device_identity["headers"], "X-Forwarded-Proto": "https"},
        )
    [command] = plain.json()["commands"]
    assert command["password"] is None and command["password_withheld"] is True
    assert SECRET not in plain.text
    assert tls.json()["commands"][0]["password"] == SECRET


@pytest.mark.asyncio
async def test_missing_encryption_key_refuses_passwords_but_allows_open_networks(
    client, edge_device_identity, org_identities, monkeypatch
):
    monkeypatch.setattr(settings, "ECHOSENSE_WIFI_SECRET_KEY", None)
    admin = org_identities["admin_a"]
    secured = await _add(client, admin, edge_device_identity["id"], password=SECRET)
    open_net = await _add(client, admin, edge_device_identity["id"], ssid="Open Lab")

    assert secured.status_code == 503
    assert secured.json()["detail"] == "wifi_secret_key_not_configured"
    assert open_net.status_code == 202
    async with AsyncSessionLocal() as session:
        ssids = (await session.scalars(select(DeviceWifiNetwork.ssid))).all()
    assert ssids == ["Open Lab"]
