"""Every route a role must not use returns 403 for that role's token.

Mirrors the role matrix: super admin sees everything; a school admin manages one school but
not schools, global settings or monitored terms; counselors get alerts, history, the
counselor overview and reports; staff get alerts and history only.
"""

from uuid import uuid4

import pytest

from tests.conftest import auth_headers

ANY_UUID = str(uuid4())

SUPER_ADMIN_ONLY = [
    ("GET", "/schools"),
    ("POST", "/schools"),
    ("GET", f"/schools/{ANY_UUID}"),
    ("PATCH", f"/schools/{ANY_UUID}"),
    ("PUT", "/system-settings/"),
    ("POST", "/system-settings/ota-push"),
    ("POST", "/dictionary/"),
    ("DELETE", "/dictionary/1"),
]

ADMIN_ONLY = [
    ("GET", "/classrooms"),
    ("POST", "/classrooms"),
    ("GET", "/devices"),
    ("POST", "/devices"),
    ("POST", f"/devices/{ANY_UUID}/rotate-key"),
    ("GET", "/users/"),
    ("POST", "/users/"),
    ("DELETE", "/users/1"),
    ("GET", "/system-settings/"),
    ("GET", "/dictionary/"),
    ("GET", "/audit-logs"),
    ("GET", "/audit-logs/export"),
    ("GET", "/system/logs"),
]

REPORTS = [
    ("GET", "/reports"),
    ("POST", "/reports"),
    ("GET", "/alerts/analytics/categories"),
    ("GET", "/alerts/analytics/summary"),
]

EVERYONE = [
    ("GET", "/alerts/"),
    ("GET", "/logs/"),
    ("GET", "/logs/stats"),
    ("GET", "/system-settings/heartbeat"),
    ("GET", "/auth/me"),
]

FORBIDDEN = {
    "admin_a": SUPER_ADMIN_ONLY,
    "counselor_a": SUPER_ADMIN_ONLY + ADMIN_ONLY,
    "staff_a": SUPER_ADMIN_ONLY + ADMIN_ONLY + REPORTS,
}

ALLOWED_READS = {
    "super": [route for route in SUPER_ADMIN_ONLY + ADMIN_ONLY + REPORTS + EVERYONE
              if route[0] == "GET" and "/schools/" not in route[1]],
    "admin_a": [route for route in ADMIN_ONLY + REPORTS + EVERYONE if route[0] == "GET"],
    "counselor_a": [route for route in REPORTS + EVERYONE if route[0] == "GET"],
    "staff_a": EVERYONE,
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("identity", "method", "path"),
    [(identity, method, path) for identity, routes in FORBIDDEN.items() for method, path in routes],
)
async def test_forbidden_routes_return_403(client, org_identities, identity, method, path):
    response = await client.request(
        method, path, json={}, headers=auth_headers(org_identities[identity])
    )

    assert response.status_code == 403, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("identity", "path"),
    [(identity, path) for identity, routes in ALLOWED_READS.items() for _, path in routes],
)
async def test_allowed_reads_succeed(client, org_identities, identity, path):
    response = await client.get(path, headers=auth_headers(org_identities[identity]))

    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_school_admin_cannot_create_super_admin_or_user_in_other_school(
    client, org_identities
):
    headers = auth_headers(org_identities["admin_a"])
    base = {"password": "temporary-pass-1", "role": "staff"}

    super_admin = await client.post(
        "/users/",
        json={**base, "email": "new-super@org.test", "role": "admin", "is_super_admin": True},
        headers=headers,
    )
    other_school = await client.post(
        "/users/",
        json={
            **base,
            "email": "other-school@org.test",
            "school_id": str(org_identities["school_b_id"]),
        },
        headers=headers,
    )

    assert super_admin.status_code == 403
    assert other_school.status_code == 403


@pytest.mark.asyncio
async def test_school_admin_user_list_is_own_school_only(client, org_identities):
    response = await client.get("/users/", headers=auth_headers(org_identities["admin_a"]))

    assert response.status_code == 200
    emails = {user["email"] for user in response.json()}
    assert "admin_b@org.test" not in emails
    assert "staff_b@org.test" not in emails
    assert "super@org.test" not in emails
    assert "staff_a@org.test" in emails


@pytest.mark.asyncio
async def test_edge_dictionary_sync_without_credentials_still_works(client):
    response = await client.get("/dictionary/")

    assert response.status_code == 200
