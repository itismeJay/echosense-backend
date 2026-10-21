import pytest
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.audit_log import AuditLog
from tests.conftest import auth_headers

NON_SUPER_ADMINS = ["admin_a", "admin_b", "staff_a", "counselor_a"]


@pytest.mark.asyncio
async def test_super_admin_creates_lists_reads_and_updates_schools(client, org_identities):
    headers = auth_headers(org_identities["super"])

    created = await client.post("/schools", json={"name": "  New   Campus "}, headers=headers)
    assert created.status_code == 201
    school = created.json()
    assert school["name"] == "New Campus"
    assert school["is_active"] is True

    listed = await client.get("/schools/", headers=headers)
    assert listed.status_code == 200
    assert {item["name"] for item in listed.json()} == {
        "New Campus",
        "Synthetic Second School",
        "Synthetic Test School",
    }

    read = await client.get(f"/schools/{school['id']}", headers=headers)
    assert read.status_code == 200
    assert read.json()["id"] == school["id"]

    renamed = await client.patch(
        f"/schools/{school['id']}", json={"name": "Renamed Campus"}, headers=headers
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Renamed Campus"

    deactivated = await client.patch(
        f"/schools/{school['id']}", json={"is_active": False}, headers=headers
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False

    inactive_only = await client.get("/schools?is_active=false", headers=headers)
    assert [item["id"] for item in inactive_only.json()] == [school["id"]]

    async with AsyncSessionLocal() as session:
        actions = (
            (await session.execute(select(AuditLog.action).where(AuditLog.resource == "School")))
            .scalars()
            .all()
        )
    assert sorted(actions) == ["CREATE_SCHOOL", "UPDATE_SCHOOL", "UPDATE_SCHOOL"]


@pytest.mark.asyncio
async def test_duplicate_school_names_conflict_on_create_and_rename(client, org_identities):
    headers = auth_headers(org_identities["super"])

    duplicate = await client.post(
        "/schools", json={"name": "synthetic test school"}, headers=headers
    )
    renamed = await client.patch(
        f"/schools/{org_identities['school_b_id']}",
        json={"name": "SYNTHETIC TEST SCHOOL"},
        headers=headers,
    )

    assert duplicate.status_code == 409
    assert renamed.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [{}, {"name": "   "}, {"name": None}, {"is_active": None}, {"unknown": 1}],
)
async def test_school_update_rejects_invalid_bodies(client, org_identities, body):
    response = await client.patch(
        f"/schools/{org_identities['school_b_id']}",
        json=body,
        headers=auth_headers(org_identities["super"]),
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_unknown_school_is_not_found(client, org_identities):
    response = await client.get(
        "/schools/00000000-0000-0000-0000-000000000000",
        headers=auth_headers(org_identities["super"]),
    )

    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", NON_SUPER_ADMINS)
async def test_non_super_admins_cannot_use_schools_api(client, org_identities, identity):
    headers = auth_headers(org_identities[identity])
    own_school = org_identities["school_a_id"]

    responses = [
        await client.post("/schools", json={"name": "Forbidden"}, headers=headers),
        await client.get("/schools", headers=headers),
        await client.get(f"/schools/{own_school}", headers=headers),
        await client.patch(f"/schools/{own_school}", json={"name": "Hijack"}, headers=headers),
    ]

    assert [response.status_code for response in responses] == [403, 403, 403, 403]


@pytest.mark.asyncio
async def test_schools_api_requires_authentication(client):
    response = await client.get("/schools")

    assert response.status_code == 401
