import pytest
from sqlalchemy import select, text

from app.database import AsyncSessionLocal, engine
from app.models.audit_log import AuditLog
from app.models.user import User
from app.routers.auth import pwd_context
from tests.conftest import auth_headers

TEMPORARY_PASSWORD = "temporary-pass-1"
NEW_PASSWORD = "my-own-password-2"


async def _admin_created_user(client, org_identities) -> dict:
    created = await client.post(
        "/users/",
        json={"email": "new.teacher@org.test", "password": TEMPORARY_PASSWORD, "role": "staff"},
        headers=auth_headers(org_identities["admin_a"]),
    )
    assert created.status_code == 201
    login = await client.post(
        "/auth/login", json={"email": "new.teacher@org.test", "password": TEMPORARY_PASSWORD}
    )
    assert login.status_code == 200
    return {"created": created.json(), "login": login.json()}


@pytest.mark.asyncio
async def test_admin_created_user_must_change_temporary_password(client, org_identities):
    result = await _admin_created_user(client, org_identities)

    assert result["created"]["must_change_password"] is True
    assert result["login"]["must_change_password"] is True
    assert result["login"]["user"]["must_change_password"] is True


@pytest.mark.asyncio
async def test_existing_users_are_not_flagged_and_login_reports_false(client, org_identities):
    login = await client.post(
        "/auth/login", json={"email": "staff_a@org.test", "password": "staff_a-password"}
    )

    assert login.status_code == 200
    assert login.json()["must_change_password"] is False
    assert login.json()["user"]["must_change_password"] is False


@pytest.mark.asyncio
async def test_flag_column_defaults_false_for_rows_inserted_without_it():
    # Simulates the currently deployed code, whose INSERTs do not name the new column.
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO users (email, hashed_password, role) "
                "VALUES ('legacy-insert@org.test', 'not-a-real-hash', 'staff')"
            )
        )
    async with AsyncSessionLocal() as session:
        flag = await session.scalar(
            select(User.must_change_password).where(User.email == "legacy-insert@org.test")
        )

    assert flag is False


@pytest.mark.asyncio
async def test_flagged_user_is_blocked_everywhere_except_exempt_routes(client, org_identities):
    result = await _admin_created_user(client, org_identities)
    headers = {"Authorization": f"Bearer {result['login']['access_token']}"}

    blocked = [
        await client.get("/alerts/", headers=headers),
        await client.get("/logs/", headers=headers),
        await client.get("/logs/stats", headers=headers),
        await client.get("/alerts/analytics/summary", headers=headers),
        await client.get("/reports/", headers=headers),
        await client.get("/users/", headers=headers),
        await client.post("/users/push-token", json={"token": ""}, headers=headers),
        await client.get("/system-settings/", headers=headers),
        await client.get("/classrooms", headers=headers),
        await client.get("/devices", headers=headers),
        await client.get("/audit-logs", headers=headers),
        await client.get("/system/logs", headers=headers),
        await client.get("/schools", headers=headers),
    ]
    me = await client.get("/auth/me", headers=headers)

    assert {response.status_code for response in blocked} == {403}
    assert {response.json()["detail"] for response in blocked} == {"password_change_required"}
    assert me.status_code == 200
    assert me.json()["must_change_password"] is True


@pytest.mark.asyncio
async def test_flagged_admin_cannot_use_admin_routes(client, org_identities):
    async with AsyncSessionLocal() as session:
        admin = await session.get(User, org_identities["admin_a"]["id"])
        admin.must_change_password = True
        await session.commit()

    response = await client.post(
        "/users/",
        json={"email": "blocked@org.test", "password": TEMPORARY_PASSWORD},
        headers=auth_headers(org_identities["admin_a"]),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "password_change_required"


@pytest.mark.asyncio
async def test_change_password_clears_flag_and_unblocks_routes(client, org_identities):
    result = await _admin_created_user(client, org_identities)
    headers = {"Authorization": f"Bearer {result['login']['access_token']}"}

    changed = await client.post(
        "/auth/change-password",
        json={"current_password": TEMPORARY_PASSWORD, "new_password": NEW_PASSWORD},
        headers=headers,
    )
    alerts = await client.get("/alerts/", headers=headers)
    old_login = await client.post(
        "/auth/login", json={"email": "new.teacher@org.test", "password": TEMPORARY_PASSWORD}
    )
    new_login = await client.post(
        "/auth/login", json={"email": "new.teacher@org.test", "password": NEW_PASSWORD}
    )

    assert changed.status_code == 200
    assert changed.json()["must_change_password"] is False
    assert alerts.status_code == 200
    assert old_login.status_code == 401
    assert new_login.status_code == 200
    assert new_login.json()["must_change_password"] is False

    async with AsyncSessionLocal() as session:
        event = await session.scalar(select(AuditLog).where(AuditLog.action == "CHANGE_PASSWORD"))
    assert event.metadata_json == {"was_temporary": True}
    assert NEW_PASSWORD not in str(event.metadata_json)


@pytest.mark.asyncio
async def test_unflagged_user_can_also_change_password(client, org_identities):
    response = await client.post(
        "/auth/change-password",
        json={"current_password": "staff_b-password", "new_password": NEW_PASSWORD},
        headers=auth_headers(org_identities["staff_b"]),
    )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_change_password_rejects_wrong_current_password(client, org_identities):
    result = await _admin_created_user(client, org_identities)
    headers = {"Authorization": f"Bearer {result['login']['access_token']}"}

    response = await client.post(
        "/auth/change-password",
        json={"current_password": "wrong-password", "new_password": NEW_PASSWORD},
        headers=headers,
    )

    assert response.status_code == 400
    async with AsyncSessionLocal() as session:
        user = await session.scalar(select(User).where(User.email == "new.teacher@org.test"))
        failures = await session.scalar(
            select(AuditLog).where(AuditLog.action == "CHANGE_PASSWORD_FAILED")
        )
    assert user.must_change_password is True
    assert pwd_context.verify(TEMPORARY_PASSWORD, user.hashed_password)
    assert failures is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"current_password": TEMPORARY_PASSWORD, "new_password": "short"},
        {"current_password": TEMPORARY_PASSWORD, "new_password": TEMPORARY_PASSWORD},
        {"current_password": TEMPORARY_PASSWORD},
        {"new_password": NEW_PASSWORD},
    ],
    ids=["too-short", "unchanged", "missing-new", "missing-current"],
)
async def test_change_password_validates_body(client, org_identities, body):
    result = await _admin_created_user(client, org_identities)
    headers = {"Authorization": f"Bearer {result['login']['access_token']}"}

    response = await client.post("/auth/change-password", json=body, headers=headers)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_change_password_requires_authentication(client):
    response = await client.post(
        "/auth/change-password",
        json={"current_password": TEMPORARY_PASSWORD, "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_device_ingest_is_not_affected_by_user_password_state(client, org_identities):
    async with AsyncSessionLocal() as session:
        for user in (await session.execute(select(User))).scalars():
            user.must_change_password = True
        await session.commit()

    heartbeat = await client.post("/system-settings/heartbeat")
    dictionary = await client.get("/dictionary/")

    assert heartbeat.status_code == 200
    assert dictionary.status_code == 200
