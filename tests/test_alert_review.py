"""Staff mark alerts reviewed or resolved, within their own school only."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.alert import Alert
from app.models.audit_log import AuditLog
from tests.conftest import auth_headers, finalized_alert_fields


def _alert_payload() -> dict:
    payload = finalized_alert_fields()
    payload.update(
        {
            "severity": "high",
            "confidence": 0.9,
            "duration": 1.0,
            "location": "Room 101",
            "transcribed_text": "Synthetic finalized transcript.",
        }
    )
    return payload


async def _create_alert(client) -> int:
    response = await client.post("/alerts/", json=_alert_payload())
    assert response.status_code == 200, response.text
    return response.json()["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["staff_a", "counselor_a", "admin_a", "super"])
async def test_any_reviewer_of_the_school_can_mark_an_alert_reviewed(client, org_identities, role):
    alert_id = await _create_alert(client)

    response = await client.post(
        f"/alerts/{alert_id}/review",
        json={"status": "reviewed"},
        headers=auth_headers(org_identities[role]),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "reviewed"
    assert response.json()["id"] == alert_id


@pytest.mark.asyncio
async def test_review_moves_between_needs_review_reviewed_and_resolved(client, org_identities):
    alert_id = await _create_alert(client)
    headers = auth_headers(org_identities["counselor_a"])

    statuses = []
    for status in ("reviewed", "resolved", "active"):
        response = await client.post(
            f"/alerts/{alert_id}/review", json={"status": status}, headers=headers
        )
        assert response.status_code == 200
        statuses.append(response.json()["status"])

    assert statuses == ["reviewed", "resolved", "active"]


@pytest.mark.asyncio
async def test_another_schools_staff_cannot_review_or_see_the_alert(client, org_identities):
    alert_id = await _create_alert(client)

    for role in ("staff_b", "admin_b"):
        response = await client.post(
            f"/alerts/{alert_id}/review",
            json={"status": "resolved"},
            headers=auth_headers(org_identities[role]),
        )
        assert response.status_code == 404, role

    async with AsyncSessionLocal() as session:
        alert = await session.get(Alert, alert_id)
        assert alert.status == "active"


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"status": "deleted"}, {"status": ""}, {}, {"status": "reviewed", "x": 1}])
async def test_review_rejects_unknown_statuses_and_fields(client, org_identities, body):
    alert_id = await _create_alert(client)

    response = await client.post(
        f"/alerts/{alert_id}/review", json=body, headers=auth_headers(org_identities["staff_a"])
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_review_requires_sign_in_and_a_known_alert(client, unauthenticated_edge_client, org_identities):
    alert_id = await _create_alert(client)

    anonymous = await unauthenticated_edge_client.post(
        f"/alerts/{alert_id}/review", json={"status": "reviewed"}
    )
    missing = await client.post(
        "/alerts/999999/review",
        json={"status": "reviewed"},
        headers=auth_headers(org_identities["admin_a"]),
    )

    assert anonymous.status_code == 401
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_review_is_audited_once_per_change_with_the_school(client, org_identities):
    alert_id = await _create_alert(client)
    headers = auth_headers(org_identities["staff_a"])

    await client.post(f"/alerts/{alert_id}/review", json={"status": "reviewed"}, headers=headers)
    await client.post(f"/alerts/{alert_id}/review", json={"status": "reviewed"}, headers=headers)

    async with AsyncSessionLocal() as session:
        rows = (
            (await session.execute(select(AuditLog).where(AuditLog.action == "REVIEW_ALERT")))
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].actor_email == "staff_a@org.test"
    assert rows[0].school_id == org_identities["school_a_id"]
    assert rows[0].resource_id == str(alert_id)


@pytest.mark.asyncio
async def test_alert_list_filters_by_review_status(client, org_identities):
    first = await _create_alert(client)
    second = await _create_alert(client)
    headers = auth_headers(org_identities["admin_a"])
    await client.post(f"/alerts/{first}/review", json={"status": "resolved"}, headers=headers)

    needs_review = await client.get("/alerts/?status=active", headers=headers)
    resolved = await client.get("/alerts/?status=resolved", headers=headers)
    invalid = await client.get("/alerts/?status=closed", headers=headers)

    assert [alert["id"] for alert in needs_review.json()] == [second]
    assert [alert["id"] for alert in resolved.json()] == [first]
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_alert_list_filters_by_date_range(client, org_identities):
    old = await _create_alert(client)
    recent = await _create_alert(client)
    async with AsyncSessionLocal() as session:
        alert = await session.get(Alert, old)
        alert.created_at = datetime.now(timezone.utc) - timedelta(days=10)
        await session.commit()
    headers = auth_headers(org_identities["admin_a"])
    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    until = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()

    after = await client.get("/alerts/", params={"date_from": since}, headers=headers)
    before = await client.get("/alerts/", params={"date_to": until}, headers=headers)

    assert [alert["id"] for alert in after.json()] == [recent]
    assert [alert["id"] for alert in before.json()] == [old]
