"""Per-room alerts: teachers see and are pushed only their classrooms' alerts."""

from uuid import UUID

import pytest
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.audit_log import AuditLog
from app.models.user import User
from app.routers.auth import create_token, pwd_context
from tests.conftest import auth_headers
from tests.test_multi_room_management import _alert_payload, rooms  # noqa: F401

TOKENS = {
    "alpha_admin": "ExponentPushToken[room-admin]",
    "alpha_counselor": "ExponentPushToken[room-counselor]",
    "alpha_staff": "ExponentPushToken[room-a101-teacher]",
    "room_b_teacher": "ExponentPushToken[room-a102-teacher]",
    "unassigned_teacher": "ExponentPushToken[room-unassigned]",
}


async def _add_teacher(context, key: str, email: str) -> None:
    async with AsyncSessionLocal() as session:
        user = User(
            email=email,
            hashed_password=pwd_context.hash("synthetic-password"),
            role="staff",
            school_id=context["alpha"],
        )
        session.add(user)
        await session.commit()
        context[key] = {"token": create_token(user), "id": user.id}


async def _set_push_tokens(context) -> None:
    async with AsyncSessionLocal() as session:
        for key, token in TOKENS.items():
            user = await session.get(User, context[key]["id"])
            user.push_token = token
        await session.commit()


@pytest.fixture
async def school(rooms, client):  # noqa: F811
    """School Alpha with a teacher in A101 (alpha_staff), one in A102, one unassigned."""

    await _add_teacher(rooms, "room_b_teacher", "a102-teacher@school.test")
    await _add_teacher(rooms, "unassigned_teacher", "no-room-teacher@school.test")
    assigned = await client.put(
        f"/users/{rooms['room_b_teacher']['id']}/classrooms",
        json={"classroom_ids": [str(rooms["a102"])]},
        headers=auth_headers(rooms["alpha_admin"]),
    )
    assert assigned.status_code == 200, assigned.text
    await _set_push_tokens(rooms)
    return rooms


async def _alert_in(client, edge_client, school, code: str, classroom_id: UUID, push_mock):
    created = await client.post(
        "/devices",
        json={"device_code": code, "display_name": code, "classroom_id": str(classroom_id)},
        headers=auth_headers(school["alpha_admin"]),
    )
    assert created.status_code == 201, created.text
    push_mock.reset_mock()
    response = await edge_client.post(
        "/alerts/",
        json=_alert_payload(code),
        headers={
            "X-EchoSense-Device-Id": code,
            "X-EchoSense-Device-Key": created.json()["device_key"],
        },
    )
    assert response.status_code == 200, response.text
    push_mock.assert_called_once()
    return response.json()["id"], set(push_mock.call_args.args[0])


@pytest.fixture
async def two_alerts(client, unauthenticated_edge_client, school, prevent_external_notifications):
    room_a, pushed_a = await _alert_in(
        client,
        unauthenticated_edge_client,
        school,
        "pi-a101",
        school["a101"],
        prevent_external_notifications,
    )
    room_b, pushed_b = await _alert_in(
        client,
        unauthenticated_edge_client,
        school,
        "pi-a102",
        school["a102"],
        prevent_external_notifications,
    )
    return {"a": room_a, "b": room_b, "pushed_a": pushed_a, "pushed_b": pushed_b}


async def _visible_ids(client, identity) -> set[int]:
    response = await client.get("/alerts/", headers=auth_headers(identity))
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()}


# --- visibility ----------------------------------------------------------------------


async def test_a_teacher_sees_only_their_rooms_alerts(client, school, two_alerts):
    assert await _visible_ids(client, school["alpha_staff"]) == {two_alerts["a"]}
    assert await _visible_ids(client, school["room_b_teacher"]) == {two_alerts["b"]}


async def test_a_teacher_cannot_open_or_review_another_rooms_alert(client, school, two_alerts):
    headers = auth_headers(school["alpha_staff"])

    detail = await client.get(f"/alerts/{two_alerts['b']}", headers=headers)
    review = await client.post(
        f"/alerts/{two_alerts['b']}/review", json={"status": "reviewed"}, headers=headers
    )
    own = await client.get(f"/alerts/{two_alerts['a']}", headers=headers)

    assert (detail.status_code, review.status_code, own.status_code) == (404, 404, 200)


async def test_a_teachers_counts_cover_only_their_rooms(client, school, two_alerts):
    stats = await client.get("/logs/stats", headers=auth_headers(school["alpha_staff"]))

    assert stats.status_code == 200, stats.text
    assert stats.json()["total_alerts"] == 1


async def test_a_teacher_with_no_room_sees_no_alerts(client, school, two_alerts):
    assert await _visible_ids(client, school["unassigned_teacher"]) == set()


async def test_admins_and_counselors_still_see_the_whole_school(client, school, two_alerts):
    both = {two_alerts["a"], two_alerts["b"]}

    assert await _visible_ids(client, school["alpha_admin"]) == both
    assert await _visible_ids(client, school["alpha_counselor"]) == both


async def test_a_super_admin_inside_the_school_sees_every_room(client, school, two_alerts):
    async with AsyncSessionLocal() as session:
        super_admin = User(
            email="room-super@school.test",
            hashed_password=pwd_context.hash("synthetic-password"),
            role="admin",
            is_super_admin=True,
        )
        session.add(super_admin)
        await session.commit()
        token = create_token(super_admin)

    response = await client.get(
        "/alerts/",
        params={"school_id": str(school["alpha"])},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert {item["id"] for item in response.json()} == {two_alerts["a"], two_alerts["b"]}


# --- push ----------------------------------------------------------------------------


async def test_push_goes_to_the_rooms_teachers_plus_admins_and_counselors(school, two_alerts):
    school_wide = {TOKENS["alpha_admin"], TOKENS["alpha_counselor"]}

    assert two_alerts["pushed_a"] == school_wide | {TOKENS["alpha_staff"]}
    assert two_alerts["pushed_b"] == school_wide | {TOKENS["room_b_teacher"]}


async def test_a_room_a_teacher_is_never_pushed_a_room_b_alert(school, two_alerts):
    assert TOKENS["alpha_staff"] not in two_alerts["pushed_b"]
    assert TOKENS["room_b_teacher"] not in two_alerts["pushed_a"]
    assert TOKENS["unassigned_teacher"] not in two_alerts["pushed_a"] | two_alerts["pushed_b"]


# --- assigning rooms -----------------------------------------------------------------


async def _assign(client, actor, user_id, classroom_ids):
    return await client.put(
        f"/users/{user_id}/classrooms",
        json={"classroom_ids": [str(item) for item in classroom_ids]},
        headers=auth_headers(actor),
    )


async def test_assigning_rooms_is_listed_and_audited(client, school):
    teacher = school["unassigned_teacher"]["id"]

    response = await _assign(
        client, school["alpha_admin"], teacher, [school["a101"], school["a102"]]
    )
    again = await _assign(client, school["alpha_admin"], teacher, [school["a102"], school["a101"]])
    people = await client.get("/users/", headers=auth_headers(school["alpha_admin"]))
    classrooms = await client.get("/classrooms", headers=auth_headers(school["alpha_admin"]))

    assert response.status_code == 200, response.text
    assert set(response.json()["classroom_ids"]) == {str(school["a101"]), str(school["a102"])}
    assert again.status_code == 200
    listed = {item["id"]: item for item in people.json()}
    assert set(listed[str(teacher)]["classroom_ids"]) == {str(school["a101"]), str(school["a102"])}
    assert listed[str(school["alpha_admin"]["id"])]["classroom_ids"] == []
    teachers_by_room = {
        item["id"]: {teacher["email"] for teacher in item["teachers"]} for item in classrooms.json()
    }
    assert teachers_by_room[str(school["a101"])] == {
        "alpha-staff@school.test",
        "no-room-teacher@school.test",
    }
    assert teachers_by_room[str(school["a102"])] == {
        "a102-teacher@school.test",
        "no-room-teacher@school.test",
    }

    async with AsyncSessionLocal() as session:
        events = (
            await session.scalars(
                select(AuditLog)
                .where(AuditLog.action == "ASSIGN_TEACHER_CLASSROOMS")
                .where(AuditLog.target == "no-room-teacher@school.test")
            )
        ).all()
    # The second call changed nothing, so it wrote no audit event.
    assert len(events) == 1


async def test_removing_every_room_hides_the_teachers_alerts(client, school, two_alerts):
    teacher = school["alpha_staff"]

    response = await _assign(client, school["alpha_admin"], teacher["id"], [])

    assert response.status_code == 200
    assert response.json()["classroom_ids"] == []
    assert await _visible_ids(client, teacher) == set()


async def test_rooms_must_belong_to_the_teachers_school(client, school):
    response = await _assign(
        client, school["alpha_admin"], school["unassigned_teacher"]["id"], [school["b201"]]
    )

    assert response.status_code == 422


async def test_only_teachers_get_rooms(client, school):
    response = await _assign(
        client, school["alpha_admin"], school["alpha_counselor"]["id"], [school["a101"]]
    )

    assert response.status_code == 422


async def test_only_this_schools_admins_assign_rooms(client, school):
    teacher = school["unassigned_teacher"]["id"]

    by_teacher = await _assign(client, school["alpha_staff"], teacher, [school["a101"]])
    by_counselor = await _assign(client, school["alpha_counselor"], teacher, [school["a101"]])
    by_other_school = await _assign(client, school["beta_admin"], teacher, [school["a101"]])

    assert (by_teacher.status_code, by_counselor.status_code, by_other_school.status_code) == (
        403,
        403,
        403,
    )
