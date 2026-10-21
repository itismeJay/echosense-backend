import pytest
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.school import School
from app.models.user import User
from app.routers.auth import pwd_context
from tests.conftest import auth_headers

CREATE_PATHS = ["/users/", "/auth/register"]
VALID_PASSWORD = "temporary-pass-1"


async def _create(client, identity, path, **body):
    body.setdefault("password", VALID_PASSWORD)
    return await client.post(path, json=body, headers=auth_headers(identity))


@pytest.mark.asyncio
@pytest.mark.parametrize("path", CREATE_PATHS)
@pytest.mark.parametrize("role", ["admin", "staff", "counselor"])
async def test_super_admin_must_choose_a_school_for_school_roles(
    client, org_identities, path, role
):
    missing = await _create(
        client, org_identities["super"], path, email=f"{role}@new.test", role=role
    )
    chosen = await _create(
        client,
        org_identities["super"],
        path,
        email=f"{role}@new.test",
        role=role,
        school_id=str(org_identities["school_b_id"]),
    )

    assert missing.status_code == 422
    assert chosen.status_code == 201
    assert chosen.json()["school_id"] == str(org_identities["school_b_id"])


@pytest.mark.asyncio
@pytest.mark.parametrize("path", CREATE_PATHS)
async def test_super_admin_may_create_a_school_less_super_admin(client, org_identities, path):
    response = await _create(
        client,
        org_identities["super"],
        path,
        email="second-super@new.test",
        role="admin",
        is_super_admin=True,
    )

    assert response.status_code == 201
    assert response.json()["school_id"] is None
    assert response.json()["is_super_admin"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("path", CREATE_PATHS)
async def test_school_admin_creates_users_only_in_own_school(client, org_identities, path):
    auto_filled = await _create(
        client, org_identities["admin_a"], path, email="auto@new.test", role="staff"
    )
    explicit_own = await _create(
        client,
        org_identities["admin_a"],
        path,
        email="own@new.test",
        school_id=str(org_identities["school_a_id"]),
    )
    other_school = await _create(
        client,
        org_identities["admin_a"],
        path,
        email="other@new.test",
        school_id=str(org_identities["school_b_id"]),
    )
    super_admin = await _create(
        client,
        org_identities["admin_b"],
        path,
        email="escalate@new.test",
        role="admin",
        is_super_admin=True,
    )

    assert auto_filled.status_code == 201
    assert auto_filled.json()["school_id"] == str(org_identities["school_a_id"])
    assert explicit_own.status_code == 201
    assert other_school.status_code == 403
    assert super_admin.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("path", CREATE_PATHS)
@pytest.mark.parametrize("identity", ["staff_a", "counselor_a"])
async def test_non_admins_cannot_create_users(client, org_identities, path, identity):
    response = await _create(client, org_identities[identity], path, email="x@new.test")

    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("path", CREATE_PATHS)
async def test_users_cannot_be_created_in_inactive_or_unknown_schools(client, org_identities, path):
    async with AsyncSessionLocal() as session:
        school = await session.get(School, org_identities["school_b_id"])
        school.is_active = False
        await session.commit()

    inactive_by_super = await _create(
        client,
        org_identities["super"],
        path,
        email="inactive@new.test",
        school_id=str(org_identities["school_b_id"]),
    )
    inactive_by_own_admin = await _create(
        client, org_identities["admin_b"], path, email="inactive2@new.test"
    )
    unknown = await _create(
        client,
        org_identities["super"],
        path,
        email="unknown@new.test",
        school_id="00000000-0000-0000-0000-000000000000",
    )

    assert inactive_by_super.status_code == 409
    assert inactive_by_own_admin.status_code == 409
    assert unknown.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("path", CREATE_PATHS)
@pytest.mark.parametrize(
    "email",
    ["not-an-email", "", "a@b", "two@@x.test", "spa ce@x.test", "@x.test", "a@.test"],
)
async def test_invalid_email_is_rejected(client, org_identities, path, email):
    response = await _create(client, org_identities["admin_a"], path, email=email)

    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("path", CREATE_PATHS)
@pytest.mark.parametrize("password", ["", "short", "1234567", "é" * 37])
async def test_weak_or_overlong_password_is_rejected(client, org_identities, path, password):
    response = await _create(
        client, org_identities["admin_a"], path, email="pw@new.test", password=password
    )

    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("path", CREATE_PATHS)
async def test_email_is_normalized_and_unique_case_insensitively(client, org_identities, path):
    created = await _create(client, org_identities["admin_a"], path, email="  Mixed.Case@New.TEST ")
    duplicate = await _create(client, org_identities["admin_b"], path, email="MIXED.case@new.test")
    existing_fixture_user = await _create(
        client,
        org_identities["super"],
        path,
        email="STAFF_A@ORG.TEST",
        school_id=str(org_identities["school_a_id"]),
    )

    assert created.status_code == 201
    assert created.json()["email"] == "mixed.case@new.test"
    assert duplicate.status_code == 409
    assert existing_fixture_user.status_code == 409


@pytest.mark.asyncio
async def test_login_is_case_insensitive_for_normalized_accounts(client, org_identities):
    response = await client.post(
        "/auth/login", json={"email": " Staff_A@Org.Test ", "password": "staff_a-password"}
    )

    assert response.status_code == 200
    assert response.json()["user"]["email"] == "staff_a@org.test"


@pytest.mark.asyncio
async def test_login_prefers_exact_match_and_refuses_ambiguous_legacy_case_duplicates(client):
    async with AsyncSessionLocal() as session:
        session.add_all(
            [
                User(
                    email="Legacy@Old.test",
                    hashed_password=pwd_context.hash("legacy-upper"),
                    role="staff",
                ),
                User(
                    email="legacy@old.test",
                    hashed_password=pwd_context.hash("legacy-lower"),
                    role="staff",
                ),
            ]
        )
        await session.commit()

    exact_upper = await client.post(
        "/auth/login", json={"email": "Legacy@Old.test", "password": "legacy-upper"}
    )
    exact_lower = await client.post(
        "/auth/login", json={"email": "legacy@old.test", "password": "legacy-lower"}
    )
    ambiguous = await client.post(
        "/auth/login", json={"email": "LEGACY@OLD.TEST", "password": "legacy-lower"}
    )

    assert exact_upper.status_code == 200
    assert exact_lower.status_code == 200
    assert ambiguous.status_code == 401


@pytest.mark.asyncio
async def test_created_user_is_stored_with_normalized_email(client, org_identities):
    await _create(client, org_identities["admin_a"], "/users/", email="Stored@New.Test")

    async with AsyncSessionLocal() as session:
        stored = await session.scalar(select(User.email).where(User.email.ilike("stored@new.test")))

    assert stored == "stored@new.test"
