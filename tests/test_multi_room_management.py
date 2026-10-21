import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from app.database import AsyncSessionLocal
from app.models.alert import Alert
from app.models.classroom import Classroom
from app.models.edge_device import EdgeDevice
from app.models.school import School
from app.models.user import User
from app.models.audit_log import AuditLog
from app.routers.auth import create_token, pwd_context
from app.schemas.edge_device import EdgeDeviceKeyResponse
from app.services.device_auth import verify_device_key
from app.services.notification_recipients import resolve_notification_recipients
from tests.conftest import auth_headers, finalized_alert_fields


def _alert_payload(device_code: str, event_id=None, **overrides) -> dict:
    payload = finalized_alert_fields(event_id)
    payload.update(
        {
            "device_identifier": device_code,
            "severity": "MEDIUM",
            "confidence": 0.8,
            "duration": 1.0,
            "location": "Untrusted Room",
            "transcribed_text": "Synthetic multi-room transcript.",
            "language": "en",
            "yamnet_ran": False,
            "yamnet_class": "NotRun",
            "yamnet_score": 0.0,
        }
    )
    payload.update(overrides)
    return payload


async def _create_school_context() -> dict:
    async with AsyncSessionLocal() as session:
        alpha = School(name="School Alpha")
        beta = School(name="School Beta")
        session.add_all([alpha, beta])
        await session.flush()
        a101 = Classroom(school_id=alpha.id, name="Classroom A101")
        a102 = Classroom(school_id=alpha.id, name="Classroom A102")
        b201 = Classroom(school_id=beta.id, name="Classroom B201")
        session.add_all([a101, a102, b201])
        await session.flush()
        users = {
            "alpha_admin": User(
                email="alpha-admin@school.test",
                hashed_password=pwd_context.hash("synthetic-password"),
                role="admin",
                school_id=alpha.id,
            ),
            "beta_admin": User(
                email="beta-admin@school.test",
                hashed_password=pwd_context.hash("synthetic-password"),
                role="admin",
                school_id=beta.id,
            ),
            "alpha_staff": User(
                email="alpha-staff@school.test",
                hashed_password=pwd_context.hash("synthetic-password"),
                role="staff",
                school_id=alpha.id,
            ),
            "beta_staff": User(
                email="beta-staff@school.test",
                hashed_password=pwd_context.hash("synthetic-password"),
                role="staff",
                school_id=beta.id,
            ),
            "alpha_counselor": User(
                email="alpha-counselor@school.test",
                hashed_password=pwd_context.hash("synthetic-password"),
                role="counselor",
                school_id=alpha.id,
            ),
            "beta_counselor": User(
                email="beta-counselor@school.test",
                hashed_password=pwd_context.hash("synthetic-password"),
                role="counselor",
                school_id=beta.id,
            ),
        }
        session.add_all(users.values())
        await session.commit()
        return {
            "alpha": alpha.id,
            "beta": beta.id,
            "a101": a101.id,
            "a102": a102.id,
            "b201": b201.id,
            **{name: {"token": create_token(user), "id": user.id} for name, user in users.items()},
        }


@pytest.fixture
async def rooms():
    return await _create_school_context()


@pytest.mark.asyncio
async def test_classroom_crud_uniqueness_lifecycle_and_detail(client, identities, rooms):
    global_headers = auth_headers(identities["admin"])
    created = await client.post(
        "/classrooms",
        json={"school_id": str(rooms["alpha"]), "name": "Classroom A103"},
        headers=global_headers,
    )
    assert created.status_code == 201
    classroom_id = created.json()["id"]

    duplicate = await client.post(
        "/classrooms",
        json={"school_id": str(rooms["alpha"]), "name": "  classroom   a103  "},
        headers=global_headers,
    )
    same_name_other_school = await client.post(
        "/classrooms",
        json={"school_id": str(rooms["beta"]), "name": "Classroom A103"},
        headers=global_headers,
    )
    renamed = await client.patch(
        f"/classrooms/{classroom_id}",
        json={"name": "Classroom A104", "is_active": False},
        headers=global_headers,
    )
    reactivated = await client.patch(
        f"/classrooms/{classroom_id}",
        json={"is_active": True},
        headers=global_headers,
    )
    listed = await client.get(f"/classrooms?school_id={rooms['alpha']}", headers=global_headers)
    detail = await client.get(f"/classrooms/{classroom_id}", headers=global_headers)

    assert duplicate.status_code == 409
    assert same_name_other_school.status_code == 201
    assert renamed.status_code == reactivated.status_code == 200
    assert renamed.json()["is_active"] is False
    assert reactivated.json()["is_active"] is True
    assert {item["id"] for item in listed.json()} >= {classroom_id}
    assert detail.json()["school_name"] == "School Alpha"
    assert detail.json()["devices"] == []


@pytest.mark.asyncio
async def test_classroom_management_is_admin_only_and_school_scoped(client, rooms):
    alpha_headers = auth_headers(rooms["alpha_admin"])
    beta_headers = auth_headers(rooms["beta_admin"])
    staff_headers = auth_headers(rooms["alpha_staff"])

    alpha_list = await client.get("/classrooms", headers=alpha_headers)
    beta_list = await client.get("/classrooms", headers=beta_headers)
    cross_read = await client.get(f"/classrooms/{rooms['b201']}", headers=alpha_headers)
    cross_create = await client.post(
        "/classrooms",
        json={"school_id": str(rooms["beta"]), "name": "Forbidden Room"},
        headers=alpha_headers,
    )
    staff_create = await client.post(
        "/classrooms",
        json={"school_id": str(rooms["alpha"]), "name": "Staff Room"},
        headers=staff_headers,
    )

    assert {item["school_id"] for item in alpha_list.json()} == {str(rooms["alpha"])}
    assert {item["school_id"] for item in beta_list.json()} == {str(rooms["beta"])}
    assert cross_read.status_code == cross_create.status_code == staff_create.status_code == 403


@pytest.mark.asyncio
async def test_device_registration_unassigned_secret_and_filters(client, rooms):
    headers = auth_headers(rooms["alpha_admin"])
    created = await client.post(
        "/devices",
        json={
            "device_code": "device-alpha-01",
            "display_name": "Alpha Provisioning Device",
            "school_id": str(rooms["alpha"]),
        },
        headers=headers,
    )
    duplicate = await client.post(
        "/devices",
        json={
            "device_code": "device-alpha-01",
            "display_name": "Duplicate",
            "school_id": str(rooms["alpha"]),
        },
        headers=headers,
    )
    assert created.status_code == 201
    assert duplicate.status_code == 409
    body = created.json()
    secret = body["device_key"]
    device_id = body["device"]["id"]
    assert body["device"]["assignment_state"] == "unassigned"
    assert secret.startswith("edk_")
    assert EdgeDeviceKeyResponse.model_fields["device_key"].repr is False

    detail = await client.get(f"/devices/{device_id}", headers=headers)
    listed = await client.get("/devices?unassigned=true&is_active=true", headers=headers)
    assert detail.status_code == listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [device_id]
    assert "device_key" not in detail.text and "api_key_hash" not in detail.text
    assert "device_key" not in listed.text and "api_key_hash" not in listed.text

    async with AsyncSessionLocal() as session:
        stored = await session.get(EdgeDevice, UUID(device_id))
        assert stored.api_key_hash != secret
        assert verify_device_key(secret, stored.api_key_hash)


@pytest.mark.asyncio
async def test_assignment_validation_unassign_and_concurrency_guard(client, rooms):
    headers = auth_headers(rooms["alpha_admin"])
    created = await client.post(
        "/devices",
        json={
            "device_code": "device-alpha-02",
            "display_name": "Alpha Device 2",
            "school_id": str(rooms["alpha"]),
        },
        headers=headers,
    )
    device_id = created.json()["device"]["id"]
    assigned = await client.post(
        f"/devices/{device_id}/assign",
        json={"classroom_id": str(rooms["a101"])},
        headers=headers,
    )
    stale = await client.post(
        f"/devices/{device_id}/assign",
        json={
            "classroom_id": str(rooms["a102"]),
            "expected_current_classroom_id": str(rooms["a102"]),
        },
        headers=headers,
    )
    cross_school = await client.post(
        f"/devices/{device_id}/assign",
        json={"classroom_id": str(rooms["b201"])},
        headers=headers,
    )
    missing = await client.post(
        f"/devices/{device_id}/assign",
        json={"classroom_id": str(uuid4())},
        headers=headers,
    )
    await client.patch(
        f"/classrooms/{rooms['a102']}",
        json={"is_active": False},
        headers=headers,
    )
    inactive = await client.post(
        f"/devices/{device_id}/assign",
        json={"classroom_id": str(rooms["a102"])},
        headers=headers,
    )
    unassigned = await client.post(f"/devices/{device_id}/unassign", headers=headers)
    detail = await client.get(f"/classrooms/{rooms['a101']}", headers=headers)

    assert assigned.status_code == 200
    assert assigned.json()["classroom_id"] == str(rooms["a101"])
    assert stale.status_code == 409
    assert cross_school.status_code == 403
    assert missing.status_code == 404
    assert inactive.status_code == 409
    assert unassigned.status_code == 200
    assert unassigned.json()["classroom_id"] is None
    assert unassigned.json()["school_id"] == str(rooms["alpha"])
    assert all(item["id"] != device_id for item in detail.json()["devices"])


@pytest.mark.asyncio
async def test_unassigned_and_inactive_classroom_devices_cannot_submit_alerts(
    client,
    unauthenticated_edge_client,
    rooms,
):
    headers = auth_headers(rooms["alpha_admin"])
    created = await client.post(
        "/devices",
        json={
            "device_code": "device-alpha-unassigned",
            "display_name": "Unassigned Device",
            "school_id": str(rooms["alpha"]),
        },
        headers=headers,
    )
    device = created.json()["device"]
    secret = created.json()["device_key"]
    edge_headers = {
        "X-EchoSense-Device-Id": device["device_code"],
        "X-EchoSense-Device-Key": secret,
    }
    rejected = await unauthenticated_edge_client.post(
        "/alerts/",
        json=_alert_payload(device["device_code"]),
        headers=edge_headers,
    )
    await client.post(
        f"/devices/{device['id']}/assign",
        json={"classroom_id": str(rooms["a101"])},
        headers=headers,
    )
    await client.patch(f"/classrooms/{rooms['a101']}", json={"is_active": False}, headers=headers)
    inactive = await unauthenticated_edge_client.post(
        "/alerts/",
        json=_alert_payload(device["device_code"]),
        headers=edge_headers,
    )

    assert rejected.status_code == inactive.status_code == 409
    assert rejected.json()["detail"] == "Device is not assigned to a classroom"
    assert inactive.json()["detail"] == "Device classroom is inactive"


@pytest.mark.asyncio
async def test_disable_enable_and_rotation_preserve_assignment_and_security(
    client,
    unauthenticated_edge_client,
    rooms,
    caplog,
):
    admin_headers = auth_headers(rooms["alpha_admin"])
    created = await client.post(
        "/devices",
        json={
            "device_code": "device-alpha-secure",
            "display_name": "Secure Device",
            "classroom_id": str(rooms["a101"]),
        },
        headers=admin_headers,
    )
    device = created.json()["device"]
    old_key = created.json()["device_key"]
    old_headers = {
        "X-EchoSense-Device-Id": device["device_code"],
        "X-EchoSense-Device-Key": old_key,
    }
    first = await unauthenticated_edge_client.post(
        "/alerts/", json=_alert_payload(device["device_code"]), headers=old_headers
    )
    disabled = await client.post(f"/devices/{device['id']}/disable", headers=admin_headers)
    blocked = await unauthenticated_edge_client.post(
        "/alerts/", json=_alert_payload(device["device_code"]), headers=old_headers
    )
    enabled = await client.post(f"/devices/{device['id']}/enable", headers=admin_headers)
    with caplog.at_level(logging.DEBUG):
        rotated = await client.post(f"/devices/{device['id']}/rotate-key", headers=admin_headers)
    new_key = rotated.json()["device_key"]
    old_rejected = await unauthenticated_edge_client.post(
        "/alerts/", json=_alert_payload(device["device_code"]), headers=old_headers
    )
    new_accepted = await unauthenticated_edge_client.post(
        "/alerts/",
        json=_alert_payload(device["device_code"]),
        headers={
            "X-EchoSense-Device-Id": device["device_code"],
            "X-EchoSense-Device-Key": new_key,
        },
    )

    assert first.status_code == new_accepted.status_code == 200
    assert disabled.json()["is_active"] is False
    assert blocked.status_code == 403
    assert enabled.json()["is_active"] is True
    assert old_rejected.status_code == 401
    assert rotated.json()["device"]["id"] == device["id"]
    assert rotated.json()["device"]["classroom_id"] == str(rooms["a101"])
    assert old_key not in caplog.text and new_key not in caplog.text
    async with AsyncSessionLocal() as session:
        assert await session.get(Alert, first.json()["id"]) is not None


@pytest.mark.asyncio
async def test_historical_attribution_and_idempotent_replay_survive_move(
    client,
    unauthenticated_edge_client,
    rooms,
    prevent_external_notifications,
):
    admin_headers = auth_headers(rooms["alpha_admin"])
    created = await client.post(
        "/devices",
        json={
            "device_code": "device-alpha-history",
            "display_name": "Historical Device",
            "classroom_id": str(rooms["a101"]),
        },
        headers=admin_headers,
    )
    device = created.json()["device"]
    edge_headers = {
        "X-EchoSense-Device-Id": device["device_code"],
        "X-EchoSense-Device-Key": created.json()["device_key"],
    }
    event_one = uuid4()
    payload_one = _alert_payload(device["device_code"], event_one)
    first = await unauthenticated_edge_client.post(
        "/alerts/", json=payload_one, headers=edge_headers
    )
    moved = await client.post(
        f"/devices/{device['id']}/assign",
        json={
            "classroom_id": str(rooms["a102"]),
            "expected_current_classroom_id": str(rooms["a101"]),
        },
        headers=admin_headers,
    )
    second = await unauthenticated_edge_client.post(
        "/alerts/", json=_alert_payload(device["device_code"]), headers=edge_headers
    )
    replay = await unauthenticated_edge_client.post(
        "/alerts/", json=payload_one, headers=edge_headers
    )
    conflict_payload = dict(payload_one)
    conflict_payload["severity"] = "HIGH"
    conflict = await unauthenticated_edge_client.post(
        "/alerts/", json=conflict_payload, headers=edge_headers
    )
    detail = await client.get(
        f"/alerts/{first.json()['id']}", headers=auth_headers(rooms["alpha_staff"])
    )

    assert first.status_code == moved.status_code == second.status_code == replay.status_code == 200
    assert first.json()["classroom_id"] == str(rooms["a101"])
    assert first.json()["classroom_name"] == "Classroom A101"
    assert second.json()["classroom_id"] == str(rooms["a102"])
    assert replay.json()["id"] == first.json()["id"]
    assert replay.json()["classroom_id"] == str(rooms["a101"])
    assert detail.json()["classroom_id"] == str(rooms["a101"])
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "Event payload conflict"
    prevent_external_notifications.assert_awaited()
    assert prevent_external_notifications.await_count == 2


@pytest.mark.asyncio
async def test_alert_spoofing_filters_and_school_isolation(
    client,
    unauthenticated_edge_client,
    rooms,
):
    async def register_and_alert(code: str, classroom_id: UUID, identity: dict):
        created = await client.post(
            "/devices",
            json={
                "device_code": code,
                "display_name": code,
                "classroom_id": str(classroom_id),
            },
            headers=auth_headers(identity),
        )
        edge_headers = {
            "X-EchoSense-Device-Id": code,
            "X-EchoSense-Device-Key": created.json()["device_key"],
        }
        alert = await unauthenticated_edge_client.post(
            "/alerts/", json=_alert_payload(code), headers=edge_headers
        )
        return created.json()["device"], alert, edge_headers

    alpha_device, alpha_alert, alpha_headers = await register_and_alert(
        "device-alpha-filter", rooms["a101"], rooms["alpha_admin"]
    )
    _, beta_alert, _ = await register_and_alert(
        "device-beta-filter", rooms["b201"], rooms["beta_admin"]
    )
    spoofed = await unauthenticated_edge_client.post(
        "/alerts/",
        json=_alert_payload(
            alpha_device["device_code"],
            classroom_id=str(rooms["a102"]),
            school_id=str(rooms["beta"]),
        ),
        headers=alpha_headers,
    )
    alpha_staff_headers = auth_headers(rooms["alpha_staff"])
    beta_staff_headers = auth_headers(rooms["beta_staff"])
    by_classroom = await client.get(
        f"/alerts/?classroom_id={rooms['a101']}", headers=alpha_staff_headers
    )
    by_device = await client.get(
        f"/alerts/?device_id={alpha_device['id']}&severity=MEDIUM",
        headers=alpha_staff_headers,
    )
    all_alpha = await client.get("/alerts/", headers=alpha_staff_headers)
    all_beta = await client.get("/alerts/", headers=beta_staff_headers)
    alpha_summary = await client.get("/alerts/analytics/summary", headers=alpha_staff_headers)
    beta_summary = await client.get("/alerts/analytics/summary", headers=beta_staff_headers)
    cross_school_filter = await client.get(
        f"/alerts/?school_id={rooms['beta']}", headers=alpha_staff_headers
    )

    assert alpha_alert.status_code == beta_alert.status_code == 200
    assert spoofed.status_code == 422
    assert {item["id"] for item in by_classroom.json()} == {alpha_alert.json()["id"]}
    assert {item["id"] for item in by_device.json()} == {alpha_alert.json()["id"]}
    assert {item["school_id"] for item in all_alpha.json()} == {str(rooms["alpha"])}
    assert {item["school_id"] for item in all_beta.json()} == {str(rooms["beta"])}
    assert alpha_summary.json()["all_time"]["total"] == 1
    assert beta_summary.json()["all_time"]["total"] == 1
    assert cross_school_filter.status_code == 403


@pytest.mark.asyncio
async def test_scoped_admin_cannot_manage_other_school_device(client, identities, rooms):
    beta_created = await client.post(
        "/devices",
        json={
            "device_code": "device-beta-private",
            "display_name": "Beta Private Device",
            "classroom_id": str(rooms["b201"]),
        },
        headers=auth_headers(rooms["beta_admin"]),
    )
    device_id = beta_created.json()["device"]["id"]
    alpha_headers = auth_headers(rooms["alpha_admin"])

    read = await client.get(f"/devices/{device_id}", headers=alpha_headers)
    disable = await client.post(f"/devices/{device_id}/disable", headers=alpha_headers)
    rotate = await client.post(f"/devices/{device_id}/rotate-key", headers=alpha_headers)
    cross_list = await client.get(f"/devices?school_id={rooms['beta']}", headers=alpha_headers)
    privileged_move = await client.post(
        f"/devices/{device_id}/assign",
        json={
            "classroom_id": str(rooms["a101"]),
            "expected_current_classroom_id": str(rooms["b201"]),
        },
        headers=auth_headers(identities["admin"]),
    )

    assert (
        read.status_code
        == disable.status_code
        == rotate.status_code
        == cross_list.status_code
        == 403
    )
    assert privileged_move.status_code == 200
    assert privileged_move.json()["school_id"] == str(rooms["alpha"])


@pytest.mark.asyncio
async def test_alert_derived_routes_hide_other_schools_and_unattributed_rows(
    client, identities, rooms
):
    async with AsyncSessionLocal() as session:
        session.add_all(
            [
                Alert(
                    school_id=rooms["alpha"],
                    classroom_id=rooms["a101"],
                    severity="HIGH",
                    confidence=0.9,
                    duration=1.0,
                    language="en",
                    detected_words='["alpha-word"]',
                    classroom_name_snapshot="Classroom A101",
                    school_name_snapshot="School Alpha",
                ),
                Alert(
                    school_id=rooms["beta"],
                    classroom_id=rooms["b201"],
                    severity="LOW",
                    confidence=0.3,
                    duration=1.0,
                    language="en",
                    detected_words='["beta-word"]',
                    classroom_name_snapshot="Classroom B201",
                    school_name_snapshot="School Beta",
                ),
                Alert(
                    severity="MEDIUM",
                    confidence=0.5,
                    duration=1.0,
                    language="en",
                    detected_words='["legacy-word"]',
                ),
                AuditLog(
                    school_id=rooms["alpha"],
                    action="UPDATE_CLASSROOM",
                    resource="Classroom",
                    status="SUCCESS",
                    target="Classroom A101",
                ),
                AuditLog(
                    school_id=rooms["beta"],
                    action="UPDATE_CLASSROOM",
                    resource="Classroom",
                    status="SUCCESS",
                    target="Classroom B201",
                ),
            ]
        )
        await session.commit()

    alpha_staff = auth_headers(rooms["alpha_staff"])
    alpha_counselor = auth_headers(rooms["alpha_counselor"])
    beta_staff = auth_headers(rooms["beta_staff"])
    super_headers = auth_headers(identities["admin"])

    alpha_alerts = await client.get("/alerts/", headers=alpha_staff)
    beta_alerts = await client.get("/alerts/", headers=beta_staff)
    alpha_logs = await client.get("/logs/", headers=alpha_staff)
    alpha_stats = await client.get("/logs/stats", headers=alpha_staff)
    super_alerts = await client.get("/alerts/", headers=super_headers)
    alpha_report = await client.post(
        "/reports/",
        json={
            "date_from": str(datetime.now(timezone.utc).date()),
            "date_to": str(datetime.now(timezone.utc).date()),
        },
        headers=alpha_counselor,
    )
    alpha_reports = await client.get("/reports/", headers=alpha_counselor)
    alpha_audit = await client.get("/audit-logs", headers=auth_headers(rooms["alpha_admin"]))
    beta_audit = await client.get("/audit-logs", headers=auth_headers(rooms["beta_admin"]))
    super_audit = await client.get("/audit-logs", headers=super_headers)

    assert {item["school_id"] for item in alpha_alerts.json()} == {str(rooms["alpha"])}
    assert {item["school_id"] for item in beta_alerts.json()} == {str(rooms["beta"])}
    assert {item["school_id"] for item in alpha_logs.json()} == {str(rooms["alpha"])}
    assert alpha_stats.json()["total_alerts"] == 1
    assert alpha_stats.json()["top_detected_words"] == ["alpha-word"]
    assert {item["school_id"] for item in super_alerts.json()} == {
        str(rooms["alpha"]),
        str(rooms["beta"]),
        None,
    }
    assert alpha_report.status_code == 201
    assert alpha_report.json()["total_incidents"] == 1
    assert [item["total_incidents"] for item in alpha_reports.json()] == [1]
    alpha_targets = {item["target"] for item in alpha_audit.json()["items"]}
    beta_targets = {item["target"] for item in beta_audit.json()["items"]}
    super_targets = {item["target"] for item in super_audit.json()["items"]}
    assert "Classroom A101" in alpha_targets and "Classroom B201" not in alpha_targets
    assert "Classroom B201" in beta_targets and "Classroom A101" not in beta_targets
    assert {"Classroom A101", "Classroom B201"} <= super_targets


@pytest.mark.asyncio
async def test_user_deletion_and_creation_are_target_school_scoped(client, identities, rooms):
    async with AsyncSessionLocal() as session:
        local_target = User(
            email="alpha-delete-target@school.test",
            hashed_password=pwd_context.hash("synthetic-password"),
            role="staff",
            school_id=rooms["alpha"],
        )
        session.add(local_target)
        await session.commit()
        await session.refresh(local_target)
        local_target_id = local_target.id

    alpha_admin = auth_headers(rooms["alpha_admin"])
    same_school = await client.delete(f"/users/{local_target_id}", headers=alpha_admin)
    cross_staff = await client.delete(f"/users/{rooms['beta_staff']['id']}", headers=alpha_admin)
    cross_admin = await client.delete(f"/users/{rooms['beta_admin']['id']}", headers=alpha_admin)
    super_target = await client.delete(f"/users/{identities['admin']['id']}", headers=alpha_admin)
    staff_delete = await client.delete(
        f"/users/{rooms['beta_staff']['id']}", headers=auth_headers(rooms["alpha_staff"])
    )
    counselor_delete = await client.delete(
        f"/users/{rooms['beta_staff']['id']}",
        headers=auth_headers(rooms["alpha_counselor"]),
    )
    cross_create = await client.post(
        "/users/",
        json={
            "email": "forbidden-beta@school.test",
            "password": "synthetic-password",
            "role": "staff",
            "school_id": str(rooms["beta"]),
        },
        headers=alpha_admin,
    )
    escalate = await client.post(
        "/users/",
        json={
            "email": "forbidden-super@school.test",
            "password": "synthetic-password",
            "role": "admin",
            "is_super_admin": True,
        },
        headers=alpha_admin,
    )
    super_delete = await client.delete(
        f"/users/{rooms['beta_counselor']['id']}", headers=auth_headers(identities["admin"])
    )

    assert same_school.status_code == 204
    assert cross_staff.status_code == cross_admin.status_code == super_target.status_code == 403
    assert staff_delete.status_code == counselor_delete.status_code == 403
    assert cross_create.status_code == escalate.status_code == 403
    assert super_delete.status_code == 204
    async with AsyncSessionLocal() as session:
        assert await session.get(User, local_target_id) is None
        assert await session.get(User, rooms["beta_staff"]["id"]) is not None
        assert await session.get(User, rooms["beta_admin"]["id"]) is not None
        assert await session.get(User, identities["admin"]["id"]) is not None


@pytest.mark.asyncio
async def test_notification_recipient_selection_is_school_scoped(rooms):
    tokens = {
        "alpha_admin": "ExponentPushToken[alpha-admin-token]",
        "alpha_staff": "ExponentPushToken[alpha-staff-token]",
        "beta_admin": "ExponentPushToken[beta-admin-token]",
        "beta_staff": "ExponentPushToken[beta-staff-token]",
    }
    async with AsyncSessionLocal() as session:
        for name, token in tokens.items():
            user = await session.get(User, rooms[name]["id"])
            user.push_token = token
        await session.commit()
        alpha = await resolve_notification_recipients(session, school_id=rooms["alpha"])
        beta = await resolve_notification_recipients(session, school_id=rooms["beta"])

    assert set(alpha.tokens) == {tokens["alpha_admin"], tokens["alpha_staff"]}
    assert set(beta.tokens) == {tokens["beta_admin"], tokens["beta_staff"]}
    assert set(alpha.tokens).isdisjoint({tokens["beta_admin"], tokens["beta_staff"]})
