"""Super admins can scope any school's data with school_id; nobody else can.

A school admin, counselor or staff member passing another school's id gets 403, and
passing nothing (or their own id) still shows only their own school.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.alert import Alert
from app.models.audit_log import AuditLog
from app.models.classroom import Classroom
from app.models.edge_device import EdgeDevice
from app.models.report import Report
from app.models.school import School
from app.routers.auth import pwd_context
from tests.conftest import auth_headers, finalized_alert_fields

SCHOOL_B_DEVICE_CODE = "school-b-pi"
SCHOOL_B_DEVICE_KEY = "synthetic-school-b-device-key"


def _alert_payload(severity: str = "high", device_code: str | None = None) -> dict:
    payload = finalized_alert_fields()
    payload.update(
        {
            "severity": severity,
            "confidence": 0.9,
            "duration": 1.0,
            "location": "Room",
            "transcribed_text": "Synthetic finalized transcript.",
        }
    )
    if device_code:
        payload["device_identifier"] = device_code
    return payload


@pytest.fixture
async def school_b_device(org_identities):
    async with AsyncSessionLocal() as session:
        classroom = Classroom(school_id=org_identities["school_b_id"], name="Room B")
        session.add(classroom)
        await session.flush()
        device = EdgeDevice(
            device_code=SCHOOL_B_DEVICE_CODE,
            display_name="School B device",
            school_id=org_identities["school_b_id"],
            classroom_id=classroom.id,
            api_key_hash=pwd_context.hash(SCHOOL_B_DEVICE_KEY),
        )
        session.add(device)
        await session.commit()
        return {
            "id": device.id,
            "headers": {
                "X-EchoSense-Device-Id": SCHOOL_B_DEVICE_CODE,
                "X-EchoSense-Device-Key": SCHOOL_B_DEVICE_KEY,
            },
        }


@pytest.fixture
async def two_school_alerts(client, org_identities, school_b_device):
    """One HIGH alert in school A and two (HIGH + LOW) in school B."""

    a = await client.post("/alerts/", json=_alert_payload("high"))
    b1 = await client.post(
        "/alerts/",
        json=_alert_payload("high", SCHOOL_B_DEVICE_CODE),
        headers=school_b_device["headers"],
    )
    b2 = await client.post(
        "/alerts/",
        json=_alert_payload("low", SCHOOL_B_DEVICE_CODE),
        headers=school_b_device["headers"],
    )
    for response in (a, b1, b2):
        assert response.status_code == 200, response.text
    return {"a": [a.json()["id"]], "b": sorted([b1.json()["id"], b2.json()["id"]])}


SCOPED_READS = [
    "/alerts/",
    "/logs/",
    "/logs/stats",
    "/reports",
    "/alerts/analytics/categories",
    "/alerts/analytics/summary",
    "/users/",
    "/audit-logs",
    "/audit-logs/export",
    "/system/logs",
    "/devices/status",
    "/classrooms",
    "/devices",
]

# Which roles may call each route at all (others already get 403 by role).
ROUTE_ROLES = {
    "/alerts/": {"admin_a", "staff_a", "counselor_a"},
    "/logs/": {"admin_a", "staff_a", "counselor_a"},
    "/logs/stats": {"admin_a", "staff_a", "counselor_a"},
    "/devices/status": {"admin_a", "staff_a", "counselor_a"},
    "/reports": {"admin_a", "counselor_a"},
    "/alerts/analytics/categories": {"admin_a", "counselor_a"},
    "/alerts/analytics/summary": {"admin_a", "counselor_a"},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("path", SCOPED_READS)
async def test_non_super_admins_get_403_for_another_schools_id(
    client, org_identities, two_school_alerts, path
):
    other_school = str(org_identities["school_b_id"])
    roles = ROUTE_ROLES.get(path, {"admin_a"})

    for role in roles:
        response = await client.get(
            path,
            params={"school_id": other_school},
            headers=auth_headers(org_identities[role]),
        )
        assert response.status_code == 403, (role, path, response.text)


@pytest.mark.asyncio
async def test_school_admin_passing_own_or_no_school_id_sees_only_their_school(
    client, org_identities, two_school_alerts
):
    headers = auth_headers(org_identities["admin_a"])
    own = str(org_identities["school_a_id"])

    no_id = await client.get("/alerts/", headers=headers)
    own_id = await client.get("/alerts/", params={"school_id": own}, headers=headers)
    stats = await client.get("/logs/stats", params={"school_id": own}, headers=headers)

    assert [alert["id"] for alert in no_id.json()] == two_school_alerts["a"]
    assert [alert["id"] for alert in own_id.json()] == two_school_alerts["a"]
    assert stats.json()["total_alerts"] == 1


@pytest.mark.asyncio
async def test_super_admin_sees_all_schools_or_exactly_one(
    client, org_identities, two_school_alerts
):
    headers = auth_headers(org_identities["super"])
    school_b = str(org_identities["school_b_id"])

    everything = await client.get("/alerts/", headers=headers)
    only_b = await client.get("/alerts/", params={"school_id": school_b}, headers=headers)
    logs_b = await client.get("/logs/", params={"school_id": school_b}, headers=headers)
    stats_b = await client.get("/logs/stats", params={"school_id": school_b}, headers=headers)

    assert len(everything.json()) == 3
    assert sorted(alert["id"] for alert in only_b.json()) == two_school_alerts["b"]
    assert sorted(alert["id"] for alert in logs_b.json()) == two_school_alerts["b"]
    assert stats_b.json()["total_alerts"] == 2
    assert stats_b.json()["high_severity"] == 1


@pytest.mark.asyncio
async def test_super_admin_analytics_scope_to_one_school(client, org_identities, two_school_alerts):
    headers = auth_headers(org_identities["super"])
    school_b = {"school_id": str(org_identities["school_b_id"])}

    categories = await client.get("/alerts/analytics/categories", params=school_b, headers=headers)
    summary = await client.get("/alerts/analytics/summary", params=school_b, headers=headers)
    all_categories = await client.get("/alerts/analytics/categories", headers=headers)

    assert categories.json()["total_alerts"] == 2
    assert categories.json()["by_severity"] == {"HIGH": 1, "LOW": 1}
    assert summary.json()["all_time"]["total"] == 2
    assert all_categories.json()["total_alerts"] == 3


@pytest.mark.asyncio
async def test_super_admin_reports_are_generated_and_listed_per_school(
    client, org_identities, two_school_alerts
):
    headers = auth_headers(org_identities["super"])
    school_b = {"school_id": str(org_identities["school_b_id"])}
    today = datetime.now(timezone.utc).date()
    body = {
        "date_from": (today - timedelta(days=1)).isoformat(),
        "date_to": (today + timedelta(days=1)).isoformat(),
    }

    created = await client.post("/reports", json=body, params=school_b, headers=headers)
    platform = await client.post("/reports", json=body, headers=headers)
    listed_b = await client.get("/reports", params=school_b, headers=headers)
    listed_a = await client.get(
        "/reports",
        params={"school_id": str(org_identities["school_a_id"])},
        headers=headers,
    )
    counselor_a = await client.get("/reports", headers=auth_headers(org_identities["counselor_a"]))

    assert created.status_code == 201
    assert created.json()["total_incidents"] == 2
    assert platform.json()["total_incidents"] == 3
    assert [report["report_id"] for report in listed_b.json()] == [created.json()["report_id"]]
    assert listed_a.json() == []
    assert counselor_a.json() == []
    async with AsyncSessionLocal() as session:
        report = await session.get(Report, created.json()["report_id"])
        assert report.school_id == org_identities["school_b_id"]


@pytest.mark.asyncio
async def test_school_admin_cannot_generate_a_report_for_another_school(
    client, org_identities, two_school_alerts
):
    today = datetime.now(timezone.utc).date().isoformat()

    response = await client.post(
        "/reports",
        json={"date_from": today, "date_to": today},
        params={"school_id": str(org_identities["school_b_id"])},
        headers=auth_headers(org_identities["admin_a"]),
    )

    assert response.status_code == 403
    async with AsyncSessionLocal() as session:
        assert (await session.execute(select(Report))).scalars().all() == []


@pytest.mark.asyncio
async def test_super_admin_lists_one_schools_users(client, org_identities):
    headers = auth_headers(org_identities["super"])

    school_b = await client.get(
        "/users/", params={"school_id": str(org_identities["school_b_id"])}, headers=headers
    )
    everyone = await client.get("/users/", headers=headers)
    admin_a = await client.get("/users/", headers=auth_headers(org_identities["admin_a"]))

    assert sorted(user["email"] for user in school_b.json()) == [
        "admin_b@org.test",
        "staff_b@org.test",
    ]
    assert len(everyone.json()) == 6
    assert {user["school_id"] for user in admin_a.json()} == {str(org_identities["school_a_id"])}


@pytest.mark.asyncio
async def test_super_admin_filters_audit_history_by_school(client, org_identities):
    async with AsyncSessionLocal() as session:
        session.add_all(
            [
                AuditLog(
                    action="UPDATE_CLASSROOM",
                    resource="Classroom",
                    status="SUCCESS",
                    actor_email="admin_a@org.test",
                    school_id=org_identities["school_a_id"],
                ),
                AuditLog(
                    action="UPDATE_CLASSROOM",
                    resource="Classroom",
                    status="SUCCESS",
                    actor_email="admin_b@org.test",
                    school_id=org_identities["school_b_id"],
                ),
            ]
        )
        await session.commit()
    headers = auth_headers(org_identities["super"])

    school_b = await client.get(
        "/audit-logs",
        params={"school_id": str(org_identities["school_b_id"]), "action": "UPDATE_CLASSROOM"},
        headers=headers,
    )
    everything = await client.get(
        "/audit-logs", params={"action": "UPDATE_CLASSROOM"}, headers=headers
    )

    assert [item["actor_email"] for item in school_b.json()["items"]] == ["admin_b@org.test"]
    assert everything.json()["total"] == 2


@pytest.mark.asyncio
async def test_school_summary_counts_per_school(
    client, org_identities, two_school_alerts, school_b_device
):
    async with AsyncSessionLocal() as session:
        # School B's device checked in just now; school A's device ten minutes ago.
        device = await session.get(EdgeDevice, school_b_device["id"])
        device.last_seen_at = datetime.now(timezone.utc)
        device_a = await session.scalar(
            select(EdgeDevice).where(EdgeDevice.school_id == org_identities["school_a_id"])
        )
        device_a.last_seen_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        # One of school B's alerts was already reviewed.
        reviewed = await session.get(Alert, two_school_alerts["b"][0])
        reviewed.status = "reviewed"
        await session.commit()

    response = await client.get(
        "/schools/summary",
        params={"tz": "Asia/Manila"},
        headers=auth_headers(org_identities["super"]),
    )

    assert response.status_code == 200
    by_name = {school["name"]: school for school in response.json()}
    school_a = by_name["Synthetic Test School"]
    school_b = by_name["Synthetic Second School"]
    assert (
        school_a["alerts_needing_review"],
        school_a["high_today"],
        school_a["alerts_this_week"],
    ) == (1, 1, 1)
    assert (
        school_b["alerts_needing_review"],
        school_b["high_today"],
        school_b["alerts_this_week"],
    ) == (1, 1, 2)
    assert (school_a["devices_online"], school_a["devices_total"]) == (0, 1)
    assert (school_b["devices_online"], school_b["devices_total"]) == (1, 1)
    assert school_a["users"] == {"admin": 1, "counselor": 1, "staff": 1}
    assert school_b["users"] == {"admin": 1, "counselor": 0, "staff": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin_a", "counselor_a", "staff_a"])
async def test_school_summary_is_super_admin_only(client, org_identities, role):
    response = await client.get("/schools/summary", headers=auth_headers(org_identities[role]))

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_school_summary_rejects_unknown_time_zones(client, org_identities):
    response = await client.get(
        "/schools/summary",
        params={"tz": "Mars/Olympus"},
        headers=auth_headers(org_identities["super"]),
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_inactive_schools_still_appear_in_the_summary(client, org_identities):
    async with AsyncSessionLocal() as session:
        school = await session.get(School, org_identities["school_b_id"])
        school.is_active = False
        await session.commit()

    response = await client.get("/schools/summary", headers=auth_headers(org_identities["super"]))

    by_name = {school["name"]: school for school in response.json()}
    assert by_name["Synthetic Second School"]["is_active"] is False
