"""Admin password reset: a temporary password that must be changed at next sign-in."""

import re

import pytest
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.audit_log import AuditLog
from app.services.temporary_password import generate_temporary_password
from tests.conftest import auth_headers

TEMPORARY_PATTERN = re.compile(r"^[A-Za-z2-9]{4}-[A-Za-z2-9]{4}-[A-Za-z2-9]{4}$")


async def _reset(client, actor, user_id, params=None):
    return await client.post(
        f"/users/{user_id}/reset-password", params=params, headers=auth_headers(actor)
    )


async def _login(client, email, password):
    return await client.post("/auth/login", json={"email": email, "password": password})


@pytest.mark.asyncio
async def test_school_admin_resets_a_password_and_it_must_be_changed(client, org_identities):
    staff = org_identities["staff_a"]

    response = await _reset(client, org_identities["admin_a"], staff["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    temporary = body["temporary_password"]
    assert TEMPORARY_PATTERN.match(temporary)
    assert body["user"]["must_change_password"] is True

    old = await _login(client, staff["email"], staff["password"])
    new = await _login(client, staff["email"], temporary)
    assert old.status_code == 401
    assert new.status_code == 200
    assert new.json()["must_change_password"] is True

    # Until they change it, the temporary password opens nothing but Change password.
    token = new.json()["access_token"]
    blocked = await client.get("/alerts/", headers={"Authorization": f"Bearer {token}"})
    assert blocked.status_code == 403

    changed = await client.post(
        "/auth/change-password",
        json={"current_password": temporary, "new_password": "my-own-password-9"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert changed.status_code == 200, changed.text
    allowed = await client.get("/alerts/", headers={"Authorization": f"Bearer {token}"})
    assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_a_reset_is_audited_without_the_password(client, org_identities):
    response = await _reset(client, org_identities["admin_a"], org_identities["staff_a"]["id"])
    temporary = response.json()["temporary_password"]

    async with AsyncSessionLocal() as session:
        [event] = (
            await session.scalars(select(AuditLog).where(AuditLog.action == "RESET_PASSWORD"))
        ).all()
    assert event.actor_email == org_identities["admin_a"]["email"]
    assert event.target == org_identities["staff_a"]["email"]
    assert event.school_id == org_identities["school_a_id"]
    stored = " ".join(
        str(value) for value in (event.description, event.metadata_json, event.target)
    )
    assert temporary not in stored


@pytest.mark.asyncio
async def test_super_admin_inside_a_school_resets_its_admin(client, org_identities):
    response = await _reset(
        client,
        org_identities["super"],
        org_identities["admin_a"]["id"],
        params={"school_id": str(org_identities["school_a_id"])},
    )

    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_nobody_resets_a_super_admins_password_here(client, org_identities):
    response = await _reset(client, org_identities["admin_a"], org_identities["super"]["id"])

    assert response.status_code == 403
    still_works = await _login(
        client, org_identities["super"]["email"], org_identities["super"]["password"]
    )
    assert still_works.status_code == 200


@pytest.mark.asyncio
async def test_an_admin_cannot_reset_another_schools_user(client, org_identities):
    response = await _reset(client, org_identities["admin_a"], org_identities["staff_b"]["id"])

    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", ["staff_a", "counselor_a"])
async def test_only_admins_reset_passwords(client, org_identities, actor):
    response = await _reset(client, org_identities[actor], org_identities["staff_a"]["id"])

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_an_admin_uses_change_password_for_their_own_account(client, org_identities):
    response = await _reset(client, org_identities["admin_a"], org_identities["admin_a"]["id"])

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_unknown_user_is_404(client, org_identities):
    response = await _reset(client, org_identities["admin_a"], 999999)

    assert response.status_code == 404


def test_temporary_passwords_are_readable_and_random():
    passwords = {generate_temporary_password() for _ in range(200)}

    assert len(passwords) == 200
    assert all(TEMPORARY_PATTERN.match(item) for item in passwords)
    assert not any(char in "".join(passwords) for char in "0O1lI")
