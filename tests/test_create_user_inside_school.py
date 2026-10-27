"""Regression: a super admin who entered a school and used People -> Add person.

The dashboard sends every request with ?school_id=<entered school>. A school admin
created there must belong to that school, must not be a super admin, must change the
temporary password, and must never reach platform routes or other schools' data.
"""

import pytest
from sqlalchemy import select, update

from app.database import AsyncSessionLocal
from app.models.user import User
from tests.conftest import auth_headers

CREATE_PATHS = ["/users/", "/auth/register"]
VALID_PASSWORD = "temporary-pass-1"


async def _create_in_school(client, identity, path, scope_school_id, **body):
    body.setdefault("password", VALID_PASSWORD)
    return await client.post(
        path,
        params={"school_id": str(scope_school_id)},
        json=body,
        headers=auth_headers(identity),
    )


async def _login(client, email):
    response = await client.post(
        "/auth/login", json={"email": email, "password": VALID_PASSWORD}
    )
    assert response.status_code == 200
    return {"token": response.json()["access_token"]}


async def _stored(email):
    async with AsyncSessionLocal() as session:
        return await session.scalar(select(User).where(User.email == email))


@pytest.mark.asyncio
@pytest.mark.parametrize("path", CREATE_PATHS)
@pytest.mark.parametrize("role", ["admin", "counselor", "staff"])
async def test_add_person_inside_a_school_creates_a_school_account(
    client, org_identities, path, role
):
    school_b = org_identities["school_b_id"]

    # Body school_id omitted, as the scope query alone must be enough.
    scoped_only = await _create_in_school(
        client, org_identities["super"], path, school_b,
        email=f"scoped-{role}@new.test", role=role,
    )
    # And the shape the dashboard sends: body school_id plus an explicit false.
    explicit = await _create_in_school(
        client, org_identities["super"], path, school_b,
        email=f"explicit-{role}@new.test", role=role,
        school_id=str(school_b), is_super_admin=False,
    )

    for response in (scoped_only, explicit):
        assert response.status_code == 201
        assert response.json()["school_id"] == str(school_b)
        assert response.json()["is_super_admin"] is False
        assert response.json()["must_change_password"] is True
        stored = await _stored(response.json()["email"])
        assert stored.school_id == school_b
        assert stored.is_super_admin is False
        assert stored.must_change_password is True


@pytest.mark.asyncio
@pytest.mark.parametrize("path", CREATE_PATHS)
@pytest.mark.parametrize("role", ["admin", "counselor", "staff"])
async def test_school_admin_adds_every_role_to_own_school(client, org_identities, path, role):
    response = await client.post(
        path,
        json={"email": f"own-{role}@new.test", "password": VALID_PASSWORD, "role": role},
        headers=auth_headers(org_identities["admin_a"]),
    )

    assert response.status_code == 201
    assert response.json()["school_id"] == str(org_identities["school_a_id"])
    assert response.json()["is_super_admin"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("path", CREATE_PATHS)
async def test_super_admin_cannot_be_created_inside_a_school(client, org_identities, path):
    in_scope = await _create_in_school(
        client, org_identities["super"], path, org_identities["school_b_id"],
        email="scoped-super@new.test", role="admin", is_super_admin=True,
    )
    with_school = await client.post(
        path,
        json={
            "email": "school-super@new.test",
            "password": VALID_PASSWORD,
            "role": "admin",
            "is_super_admin": True,
            "school_id": str(org_identities["school_b_id"]),
        },
        headers=auth_headers(org_identities["super"]),
    )

    assert in_scope.status_code == 422
    assert with_school.status_code == 422
    assert await _stored("scoped-super@new.test") is None
    assert await _stored("school-super@new.test") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("path", CREATE_PATHS)
@pytest.mark.parametrize("loose_true", ["true", 1, "on", "yes"])
async def test_only_a_json_true_requests_a_super_admin(client, org_identities, path, loose_true):
    response = await client.post(
        path,
        json={
            "email": "loose@new.test",
            "password": VALID_PASSWORD,
            "role": "admin",
            "is_super_admin": loose_true,
        },
        headers=auth_headers(org_identities["super"]),
    )

    assert response.status_code == 422
    assert await _stored("loose@new.test") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("path", CREATE_PATHS)
async def test_scope_and_body_school_must_agree(client, org_identities, path):
    response = await _create_in_school(
        client, org_identities["super"], path, org_identities["school_b_id"],
        email="mismatch@new.test", role="staff",
        school_id=str(org_identities["school_a_id"]),
    )

    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("path", CREATE_PATHS)
async def test_school_admin_cannot_use_a_foreign_scope(client, org_identities, path):
    response = await _create_in_school(
        client, org_identities["admin_a"], path, org_identities["school_b_id"],
        email="foreign@new.test", role="staff",
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_principal_created_in_a_school_cannot_reach_platform_data(client, org_identities):
    school_b = org_identities["school_b_id"]
    other_school_event = await _create_in_school(
        client, org_identities["super"], "/users/", org_identities["school_a_id"],
        email="teacher@school-a.test", role="staff",
    )
    assert other_school_event.status_code == 201
    created = await _create_in_school(
        client, org_identities["super"], "/users/", school_b,
        email="principal@school-b.test", role="admin", school_id=str(school_b),
        is_super_admin=False,
    )
    assert created.status_code == 201

    principal = await _login(client, "principal@school-b.test")
    forced = await client.get("/alerts/", headers=auth_headers(principal))
    assert forced.status_code == 403
    assert forced.json()["detail"] == "password_change_required"

    changed = await client.post(
        "/auth/change-password",
        json={"current_password": VALID_PASSWORD, "new_password": "principal-own-pass"},
        headers=auth_headers(principal),
    )
    assert changed.status_code == 200
    me = await client.get("/auth/me", headers=auth_headers(principal))
    assert me.json()["is_super_admin"] is False
    assert me.json()["school_id"] == str(school_b)

    for path in ("/schools", "/schools/summary", f"/schools/{school_b}"):
        response = await client.get(path, headers=auth_headers(principal))
        assert response.status_code == 403, path
    foreign_logs = await client.get(
        "/audit-logs",
        params={"school_id": str(org_identities["school_a_id"])},
        headers=auth_headers(principal),
    )
    assert foreign_logs.status_code == 403

    own_logs = await client.get("/audit-logs", headers=auth_headers(principal))
    assert own_logs.status_code == 200
    targets = {item.get("target") for item in own_logs.json()["items"]}
    assert "principal@school-b.test" in targets
    assert "teacher@school-a.test" not in targets

    users = await client.get("/users/", headers=auth_headers(principal))
    assert {user["school_id"] for user in users.json()} == {str(school_b)}


@pytest.mark.asyncio
async def test_revoking_super_admin_applies_to_existing_tokens(client, org_identities):
    """Access is re-read from the database on every request, not frozen at login."""

    super_headers = auth_headers(org_identities["super"])
    assert (await client.get("/schools/summary", headers=super_headers)).status_code == 200

    async with AsyncSessionLocal() as session:
        await session.execute(
            update(User)
            .where(User.id == org_identities["super"]["id"])
            .values(is_super_admin=False, school_id=org_identities["school_b_id"])
        )
        await session.commit()

    assert (await client.get("/schools/summary", headers=super_headers)).status_code == 403
    assert (await client.get("/schools", headers=super_headers)).status_code == 403
