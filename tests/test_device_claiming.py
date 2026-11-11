# ruff: noqa: F811  (pytest fixtures imported from other modules)
"""QR device setup: provision -> claim -> activate, plus move, remove and reset."""

from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.audit_log import AuditLog
from app.models.edge_device import EdgeDevice
from app.models.school import School
from app.models.user import User
from app.services.device_claims import normalize_claim_code
from tests.conftest import auth_headers
from tests.test_multi_room_management import _alert_payload, rooms  # noqa: F401
from tests.test_room_alerts import school  # noqa: F401


async def _provision(client, super_headers, count=1) -> list[dict]:
    response = await client.post("/devices/provision", json={"count": count}, headers=super_headers)
    assert response.status_code == 201, response.text
    return response.json()["boxes"]


async def _claim(client, headers, classroom_id, box, **overrides):
    body = {"device_code": box["device_code"], "claim_code": box["claim_code"], **overrides}
    return await client.post(
        f"/classrooms/{classroom_id}/devices/claim", json=body, headers=headers
    )


async def _activate(edge_client, box, **overrides):
    body = {
        "device_code": box["device_code"],
        "claim_code": box["claim_code"],
        "box_secret": box["box_secret"],
        **overrides,
    }
    return await edge_client.post("/devices/activate", json=body)


def _key_headers(device_code: str, key: str) -> dict[str, str]:
    return {"X-EchoSense-Device-Id": device_code, "X-EchoSense-Device-Key": key}


async def _audit_actions(device_code: str) -> list[tuple[str, str, object]]:
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(AuditLog).where(AuditLog.target == device_code).order_by(AuditLog.id)
            )
        ).scalars()
        return [(row.action, row.status, row.school_id) for row in rows]


async def _stored(device_code: str) -> EdgeDevice:
    async with AsyncSessionLocal() as session:
        return await session.scalar(select(EdgeDevice).where(EdgeDevice.device_code == device_code))


@pytest.fixture
def super_headers(identities):
    return auth_headers(identities["admin"])


@pytest.mark.asyncio
async def test_provision_creates_unclaimed_boxes_and_stores_only_hashes(
    client, super_headers, rooms
):
    boxes = await _provision(client, super_headers, count=3)

    assert len({box["device_code"] for box in boxes}) == 3
    for box in boxes:
        assert box["device_code"].startswith("es-") and len(box["device_code"]) == 7
        normalized = normalize_claim_code(box["claim_code"])
        assert normalized is not None and len(normalized) >= 10
        query = parse_qs(urlparse(box["qr_payload"]).query)
        assert box["qr_payload"].startswith("echosense://claim?")
        assert query == {"d": [box["device_code"]], "c": [box["claim_code"]]}
        assert box["device"]["status"] == "unclaimed"
        assert box["device"]["school_id"] is None and box["device"]["classroom_id"] is None
        stored = await _stored(box["device_code"])
        assert stored.api_key_hash is None
        assert stored.claim_code_hash and normalized not in stored.claim_code_hash
        assert stored.box_secret_hash and box["box_secret"] not in stored.box_secret_hash
        assert await _audit_actions(box["device_code"]) == [
            ("PROVISION_EDGE_DEVICE", "SUCCESS", None)
        ]

    # Unclaimed boxes belong to no school: a school admin never sees them.
    school_list = await client.get("/devices", headers=auth_headers(rooms["alpha_admin"]))
    assert all(
        item["device_code"] not in {b["device_code"] for b in boxes} for item in school_list.json()
    )


@pytest.mark.asyncio
async def test_only_super_admins_provision_and_reset(client, super_headers, rooms):
    for role in ("alpha_admin", "alpha_staff", "alpha_counselor"):
        refused = await client.post(
            "/devices/provision", json={"count": 1}, headers=auth_headers(rooms[role])
        )
        assert refused.status_code == 403
    box = (await _provision(client, super_headers))[0]
    reset = await client.post(
        f"/devices/{box['device']['id']}/reset", headers=auth_headers(rooms["alpha_admin"])
    )
    assert reset.status_code == 403
    too_many = await client.post("/devices/provision", json={"count": 51}, headers=super_headers)
    assert too_many.status_code == 422


@pytest.mark.asyncio
async def test_full_flow_claim_then_activate_returns_key_once(
    client, unauthenticated_edge_client, super_headers, rooms
):
    box = (await _provision(client, super_headers))[0]
    admin = auth_headers(rooms["alpha_admin"])

    waiting = await _activate(unauthenticated_edge_client, box)
    assert waiting.status_code == 202
    assert waiting.json()["status"] == "waiting" and waiting.json()["device_key"] is None

    # Typed by hand: lowercase, spaces instead of hyphens.
    typed = box["claim_code"].replace("-", " ").lower()
    claimed = await _claim(client, admin, rooms["a101"], {**box, "claim_code": typed})
    assert claimed.status_code == 201, claimed.text
    device = claimed.json()
    assert device["status"] == "claimed"
    assert device["classroom_id"] == str(rooms["a101"]) and device["school_id"] == str(
        rooms["alpha"]
    )
    assert "device_key" not in device and box["claim_code"] not in claimed.text

    # No key exists yet, so nothing can authenticate as the box.
    no_key = await unauthenticated_edge_client.get(
        "/devices/me/config", headers=_key_headers(box["device_code"], "edk_guess")
    )
    assert no_key.status_code == 401

    activated = await _activate(unauthenticated_edge_client, box)
    assert activated.status_code == 200, activated.text
    body = activated.json()
    assert body["status"] == "active" and body["device_key"].startswith("edk_")
    assert body["classroom_name"] == "Classroom A101" and body["school_name"] == "School Alpha"

    config = await unauthenticated_edge_client.get(
        "/devices/me/config", headers=_key_headers(box["device_code"], body["device_key"])
    )
    assert config.status_code == 200 and config.json()["school_id"] == str(rooms["alpha"])

    # One-time: the codes are spent and the key is never handed out again.
    again = await _activate(unauthenticated_edge_client, box)
    assert again.status_code == 401
    reclaim = await _claim(client, admin, rooms["a102"], box)
    assert reclaim.status_code == 400

    stored = await _stored(box["device_code"])
    assert stored.claim_state == "active" and stored.claim_code_hash is None
    assert stored.box_secret_hash is None and stored.claimed_by == rooms["alpha_admin"]["id"]
    assert stored.activated_at is not None and stored.claimed_at is not None

    actions = await _audit_actions(box["device_code"])
    assert ("CLAIM_EDGE_DEVICE", "SUCCESS", rooms["alpha"]) in actions
    assert ("ACTIVATE_EDGE_DEVICE", "SUCCESS", rooms["alpha"]) in actions


@pytest.mark.asyncio
async def test_activation_needs_the_box_secret_not_just_the_sticker(
    client, unauthenticated_edge_client, super_headers, rooms
):
    box = (await _provision(client, super_headers))[0]
    assert (
        await _claim(client, auth_headers(rooms["alpha_admin"]), rooms["a101"], box)
    ).status_code == 201

    sticker_only = await _activate(
        unauthenticated_edge_client, box, box_secret="ebs_photo-of-sticker"
    )
    wrong_code = await _activate(unauthenticated_edge_client, box, claim_code="AAAA-BBBB-CCCC")
    unknown = await _activate(unauthenticated_edge_client, box, device_code="es-zzzz")
    assert sticker_only.status_code == wrong_code.status_code == unknown.status_code == 401
    assert sticker_only.json() == wrong_code.json() == unknown.json()

    real = await _activate(unauthenticated_edge_client, box)
    assert real.status_code == 200
    failures = [a for a in await _audit_actions(box["device_code"]) if a[1] == "FAILURE"]
    assert failures and all(action == "ACTIVATE_EDGE_DEVICE" for action, _, _ in failures)


@pytest.mark.asyncio
async def test_wrong_and_unknown_codes_get_the_same_answer_then_rate_limit(
    client, super_headers, rooms
):
    box = (await _provision(client, super_headers))[0]
    admin = auth_headers(rooms["alpha_admin"])

    wrong = await _claim(client, admin, rooms["a101"], {**box, "claim_code": "AAAA-BBBB-CCCC"})
    unknown = await _claim(client, admin, rooms["a101"], {**box, "device_code": "es-zzzz"})
    garbage = await _claim(client, admin, rooms["a101"], {**box, "claim_code": "not a code!"})
    assert wrong.status_code == unknown.status_code == garbage.status_code == 400
    assert wrong.json() == unknown.json() == garbage.json()

    for _ in range(3):
        await _claim(client, admin, rooms["a101"], {**box, "claim_code": "AAAA-BBBB-CCCC"})
    # Five failures against this box: even the right code now waits.
    blocked = await _claim(client, admin, rooms["a101"], box)
    assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) > 0
    stored = await _stored(box["device_code"])
    assert stored.claim_state == "unclaimed"
    failures = [a for a in await _audit_actions(box["device_code"]) if a[0] == "CLAIM_EDGE_DEVICE"]
    assert failures and all(status == "FAILURE" for _, status, _ in failures)


@pytest.mark.asyncio
async def test_activate_is_rate_limited_but_waiting_polls_are_not(
    client, unauthenticated_edge_client, super_headers
):
    box = (await _provision(client, super_headers))[0]
    for _ in range(8):
        assert (await _activate(unauthenticated_edge_client, box)).status_code == 202
    for _ in range(5):
        await _activate(unauthenticated_edge_client, box, box_secret="ebs_wrong")
    blocked = await _activate(unauthenticated_edge_client, box)
    assert blocked.status_code == 429


@pytest.mark.asyncio
async def test_claim_permissions_and_school_isolation(client, super_headers, rooms):
    boxes = await _provision(client, super_headers, count=3)
    for role in ("alpha_staff", "alpha_counselor"):
        refused = await _claim(client, auth_headers(rooms[role]), rooms["a101"], boxes[0])
        assert refused.status_code == 403
    # Another school's admin cannot claim into school Alpha's room.
    foreign = await _claim(client, auth_headers(rooms["beta_admin"]), rooms["a101"], boxes[0])
    assert foreign.status_code == 403
    assert (await _stored(boxes[0]["device_code"])).claim_state == "unclaimed"

    # A super admin may claim inside a school.
    by_super = await _claim(client, super_headers, rooms["b201"], boxes[1])
    assert by_super.status_code == 201 and by_super.json()["school_id"] == str(rooms["beta"])

    # A box already in school Beta can't be taken by school Alpha, even with its code.
    taken = await _claim(client, auth_headers(rooms["alpha_admin"]), rooms["a101"], boxes[1])
    assert taken.status_code == 409 and "another school" in taken.json()["detail"]
    same_school = await _claim(client, auth_headers(rooms["beta_admin"]), rooms["b201"], boxes[1])
    assert same_school.status_code == 409 and "Move" in same_school.json()["detail"]
    stored = await _stored(boxes[1]["device_code"])
    assert stored.school_id == rooms["beta"] and stored.classroom_id == rooms["b201"]


@pytest.mark.asyncio
async def test_claim_refused_for_inactive_room_and_forced_password_change(
    client, super_headers, rooms
):
    box = (await _provision(client, super_headers))[0]
    admin = auth_headers(rooms["alpha_admin"])
    closed = await client.patch(
        f"/classrooms/{rooms['a102']}", json={"is_active": False}, headers=admin
    )
    assert closed.status_code == 200
    assert (await _claim(client, admin, rooms["a102"], box)).status_code == 409

    async with AsyncSessionLocal() as session:
        user = await session.get(User, rooms["alpha_admin"]["id"])
        user.must_change_password = True
        await session.commit()
    forced = await _claim(client, admin, rooms["a101"], box)
    assert forced.status_code == 403 and forced.json()["detail"] == "password_change_required"


@pytest.mark.asyncio
async def test_activation_refused_for_disabled_box_and_inactive_school(
    client, unauthenticated_edge_client, super_headers, rooms
):
    boxes = await _provision(client, super_headers, count=2)
    admin = auth_headers(rooms["alpha_admin"])
    for box in boxes:
        assert (await _claim(client, admin, rooms["a101"], box)).status_code == 201

    disabled = await client.post(f"/devices/{boxes[0]['device']['id']}/disable", headers=admin)
    assert disabled.status_code == 200 and disabled.json()["status"] == "disabled"
    assert (await _activate(unauthenticated_edge_client, boxes[0])).status_code == 403

    async with AsyncSessionLocal() as session:
        alpha = await session.get(School, rooms["alpha"])
        alpha.is_active = False
        await session.commit()
    refused = await _activate(unauthenticated_edge_client, boxes[1])
    assert refused.status_code == 403 and refused.json()["detail"] == "school_inactive"
    assert (await _stored(boxes[1]["device_code"])).api_key_hash is None


@pytest.mark.asyncio
async def test_move_within_school_only(client, unauthenticated_edge_client, super_headers, rooms):
    box = (await _provision(client, super_headers))[0]
    admin = auth_headers(rooms["alpha_admin"])
    device_id = (await _claim(client, admin, rooms["a101"], box)).json()["id"]
    key = (await _activate(unauthenticated_edge_client, box)).json()["device_key"]

    moved = await client.post(
        f"/devices/{device_id}/move", json={"classroom_id": str(rooms["a102"])}, headers=admin
    )
    assert moved.status_code == 200 and moved.json()["classroom_id"] == str(rooms["a102"])
    assert moved.json()["status"] == "active"

    cross = await client.post(
        f"/devices/{device_id}/move", json={"classroom_id": str(rooms["b201"])}, headers=admin
    )
    assert cross.status_code == 403
    cross_super = await client.post(
        f"/devices/{device_id}/move",
        json={"classroom_id": str(rooms["b201"])},
        headers=super_headers,
    )
    assert cross_super.status_code == 409
    foreign = await client.post(
        f"/devices/{device_id}/move",
        json={"classroom_id": str(rooms["b201"])},
        headers=auth_headers(rooms["beta_admin"]),
    )
    assert foreign.status_code == 403
    teacher = await client.post(
        f"/devices/{device_id}/move",
        json={"classroom_id": str(rooms["a101"])},
        headers=auth_headers(rooms["alpha_staff"]),
    )
    assert teacher.status_code == 403

    # The key keeps working after a move.
    config = await unauthenticated_edge_client.get(
        "/devices/me/config", headers=_key_headers(box["device_code"], key)
    )
    assert config.status_code == 200
    assert ("MOVE_EDGE_DEVICE", "SUCCESS", rooms["alpha"]) in await _audit_actions(
        box["device_code"]
    )


@pytest.mark.asyncio
async def test_remove_revokes_key_and_reset_makes_box_claimable_again(
    client, unauthenticated_edge_client, super_headers, rooms
):
    box = (await _provision(client, super_headers))[0]
    admin = auth_headers(rooms["alpha_admin"])
    device_id = (await _claim(client, admin, rooms["a101"], box)).json()["id"]
    key = (await _activate(unauthenticated_edge_client, box)).json()["device_key"]

    other_school = await client.post(
        f"/devices/{device_id}/remove", headers=auth_headers(rooms["beta_admin"])
    )
    assert other_school.status_code == 403
    removed = await client.post(f"/devices/{device_id}/remove", headers=admin)
    assert removed.status_code == 200 and removed.json()["status"] == "removed"

    revoked = await unauthenticated_edge_client.get(
        "/devices/me/config", headers=_key_headers(box["device_code"], key)
    )
    assert revoked.status_code == 401
    assert (await client.get(f"/devices/{device_id}", headers=admin)).status_code == 403
    listed = await client.get(f"/devices/{device_id}", headers=super_headers)
    assert listed.json()["status"] == "removed" and listed.json()["school_id"] is None
    # The old sticker no longer works; only a super-admin reset makes it claimable.
    assert (await _claim(client, admin, rooms["a101"], box)).status_code == 400

    reset = await client.post(f"/devices/{device_id}/reset", headers=super_headers)
    assert reset.status_code == 200
    fresh = reset.json()
    assert fresh["claim_code"] != box["claim_code"] and fresh["box_secret"] != box["box_secret"]
    assert fresh["device"]["status"] == "unclaimed"
    assert (
        await _claim(client, auth_headers(rooms["beta_admin"]), rooms["b201"], fresh)
    ).status_code == 201

    actions = await _audit_actions(box["device_code"])
    assert ("REMOVE_EDGE_DEVICE", "SUCCESS", rooms["alpha"]) in actions
    assert ("RESET_EDGE_DEVICE", "SUCCESS", None) in actions


@pytest.mark.asyncio
async def test_reset_of_active_box_revokes_its_key_and_detaches(
    client, unauthenticated_edge_client, super_headers, rooms
):
    box = (await _provision(client, super_headers))[0]
    device_id = (
        await _claim(client, auth_headers(rooms["alpha_admin"]), rooms["a101"], box)
    ).json()["id"]
    key = (await _activate(unauthenticated_edge_client, box)).json()["device_key"]

    reset = await client.post(f"/devices/{device_id}/reset", headers=super_headers)
    assert reset.status_code == 200
    assert reset.json()["device"]["classroom_id"] is None
    revoked = await unauthenticated_edge_client.get(
        "/devices/me/config", headers=_key_headers(box["device_code"], key)
    )
    assert revoked.status_code == 401
    # The reset is logged against the school the box came from.
    assert ("RESET_EDGE_DEVICE", "SUCCESS", rooms["alpha"]) in await _audit_actions(
        box["device_code"]
    )


@pytest.mark.asyncio
async def test_manual_key_rotation_respects_lifecycle(client, super_headers, rooms):
    boxes = await _provision(client, super_headers, count=2)
    unclaimed = await client.post(
        f"/devices/{boxes[0]['device']['id']}/rotate-key", headers=super_headers
    )
    assert unclaimed.status_code == 409

    admin = auth_headers(rooms["alpha_admin"])
    device_id = (await _claim(client, admin, rooms["a101"], boxes[1])).json()["id"]
    advanced = await client.post(f"/devices/{device_id}/rotate-key", headers=admin)
    assert advanced.status_code == 200 and advanced.json()["device"]["status"] == "active"
    stored = await _stored(boxes[1]["device_code"])
    assert stored.claim_code_hash is None and stored.box_secret_hash is None


@pytest.mark.asyncio
async def test_status_lists_waiting_box_for_school(
    client, unauthenticated_edge_client, super_headers, rooms
):
    box = (await _provision(client, super_headers))[0]
    admin = auth_headers(rooms["alpha_admin"])
    await _claim(client, admin, rooms["a101"], box)
    status = await client.get("/devices/status", headers=admin)
    item = next(d for d in status.json()["devices"] if d["device_code"] == box["device_code"])
    assert item["status"] == "claimed" and item["online"] is False

    await _activate(unauthenticated_edge_client, box)
    status = await client.get("/devices/status", headers=admin)
    item = next(d for d in status.json()["devices"] if d["device_code"] == box["device_code"])
    assert item["status"] == "active" and item["online"] is True


@pytest.mark.asyncio
async def test_alert_from_claimed_box_lands_in_its_room_and_pushes_only_room_teachers(
    client,
    unauthenticated_edge_client,
    super_headers,
    school,  # noqa: F811
    prevent_external_notifications,
):
    box = (await _provision(client, super_headers))[0]
    claimed = await _claim(client, auth_headers(school["alpha_admin"]), school["a102"], box)
    assert claimed.status_code == 201
    key = (await _activate(unauthenticated_edge_client, box)).json()["device_key"]

    prevent_external_notifications.reset_mock()
    alert = await unauthenticated_edge_client.post(
        "/alerts/",
        json=_alert_payload(box["device_code"]),
        headers=_key_headers(box["device_code"], key),
    )
    assert alert.status_code == 200, alert.text
    assert alert.json()["classroom_id"] == str(school["a102"])
    pushed = set(prevent_external_notifications.call_args.args[0])
    assert "ExponentPushToken[room-a102-teacher]" in pushed
    assert "ExponentPushToken[room-a101-teacher]" not in pushed
    assert "ExponentPushToken[room-unassigned]" not in pushed
