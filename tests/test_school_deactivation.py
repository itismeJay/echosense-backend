import pytest

from tests.conftest import auth_headers, finalized_alert_fields


async def _set_active(client, org_identities, school_key: str, is_active: bool) -> None:
    response = await client.patch(
        f"/schools/{org_identities[school_key]}",
        json={"is_active": is_active},
        headers=auth_headers(org_identities["super"]),
    )
    assert response.status_code == 200


async def _login(client, key: str, password: str | None = None):
    return await client.post(
        "/auth/login",
        json={"email": f"{key}@org.test", "password": password or f"{key}-password"},
    )


def _alert_payload() -> dict:
    payload = finalized_alert_fields()
    payload.update(
        {
            "severity": "medium",
            "confidence": 0.9,
            "duration": 1.0,
            "location": "Room 101",
            "transcribed_text": "Synthetic finalized transcript.",
        }
    )
    return payload


@pytest.mark.asyncio
async def test_inactive_school_blocks_login_for_its_users_only(client, org_identities):
    await _set_active(client, org_identities, "school_b_id", False)

    blocked_staff = await _login(client, "staff_b")
    blocked_admin = await _login(client, "admin_b")
    wrong_password = await _login(client, "staff_b", "not-the-password")
    other_school = await _login(client, "staff_a")
    super_admin = await _login(client, "super")

    assert blocked_staff.status_code == 403
    assert blocked_staff.json()["detail"] == "school_inactive"
    assert blocked_admin.status_code == 403
    assert wrong_password.status_code == 401
    assert other_school.status_code == 200
    assert super_admin.status_code == 200


@pytest.mark.asyncio
async def test_inactive_school_revokes_existing_tokens(client, org_identities):
    before = await client.get("/alerts/", headers=auth_headers(org_identities["staff_b"]))
    await _set_active(client, org_identities, "school_b_id", False)

    staff = await client.get("/alerts/", headers=auth_headers(org_identities["staff_b"]))
    admin = await client.get("/users/", headers=auth_headers(org_identities["admin_b"]))
    me = await client.get("/auth/me", headers=auth_headers(org_identities["admin_b"]))
    other_school = await client.get("/alerts/", headers=auth_headers(org_identities["staff_a"]))

    assert before.status_code == 200
    assert [staff.status_code, admin.status_code, me.status_code] == [403, 403, 403]
    assert {staff.json()["detail"], me.json()["detail"]} == {"school_inactive"}
    assert other_school.status_code == 200


@pytest.mark.asyncio
async def test_reactivating_a_school_restores_access(client, org_identities):
    await _set_active(client, org_identities, "school_b_id", False)
    await _set_active(client, org_identities, "school_b_id", True)

    login = await _login(client, "staff_b")

    assert login.status_code == 200


@pytest.mark.asyncio
async def test_super_admin_assigned_to_inactive_school_is_unaffected(
    client, identities, org_identities
):
    # The legacy fixture super admin carries school A as its school_id.
    await _set_active(client, org_identities, "school_a_id", False)

    response = await client.get("/schools", headers=auth_headers(identities["admin"]))

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_inactive_school_rejects_alert_ingest_from_its_devices(client, org_identities):
    # The fixture device belongs to school A.
    await _set_active(client, org_identities, "school_a_id", False)
    rejected = await client.post("/alerts/", json=_alert_payload())

    await _set_active(client, org_identities, "school_a_id", True)
    accepted = await client.post("/alerts/", json=_alert_payload())

    assert rejected.status_code == 403
    assert rejected.json()["detail"] == "school_inactive"
    assert accepted.status_code == 200


@pytest.mark.asyncio
async def test_inactive_school_still_hides_device_state_from_wrong_keys(
    client, org_identities, edge_device_identity
):
    await _set_active(client, org_identities, "school_a_id", False)

    response = await client.post(
        "/alerts/",
        json=_alert_payload(),
        headers={
            "X-EchoSense-Device-Id": edge_device_identity["device_code"],
            "X-EchoSense-Device-Key": "wrong-device-key",
        },
    )

    assert response.status_code == 401
