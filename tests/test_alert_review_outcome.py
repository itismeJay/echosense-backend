"""Reviewers record an outcome (and optional note) when marking alerts reviewed or resolved."""

import pytest
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.alert import Alert
from app.models.audit_log import AuditLog
from tests.conftest import auth_headers
from tests.test_alert_review import _create_alert

OUTCOMES = ["confirmed_bullying", "false_alert", "joking_banter", "misheard"]


async def _review(client, alert_id, headers, **body):
    return await client.post(f"/alerts/{alert_id}/review", json=body, headers=headers)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", OUTCOMES)
async def test_outcome_and_note_are_stored_and_returned(client, org_identities, outcome):
    alert_id = await _create_alert(client)
    headers = auth_headers(org_identities["counselor_a"])

    response = await _review(
        client, alert_id, headers, status="resolved", outcome=outcome, note="  Spoke to class. "
    )

    assert response.status_code == 200, response.text
    assert response.json()["review_outcome"] == outcome
    assert response.json()["review_note"] == "Spoke to class."
    fetched = await client.get(f"/alerts/{alert_id}", headers=headers)
    assert fetched.json()["review_outcome"] == outcome
    listed = await client.get("/alerts/", headers=headers)
    assert listed.json()[0]["review_outcome"] == outcome


@pytest.mark.asyncio
async def test_outcome_is_optional_kept_on_status_change_and_cleared_on_active(
    client, org_identities
):
    alert_id = await _create_alert(client)
    headers = auth_headers(org_identities["staff_a"])

    plain = await _review(client, alert_id, headers, status="reviewed")
    assert plain.status_code == 200
    assert plain.json()["review_outcome"] is None

    await _review(client, alert_id, headers, status="reviewed", outcome="misheard", note="n")
    kept = await _review(client, alert_id, headers, status="resolved")
    assert kept.json()["review_outcome"] == "misheard"
    assert kept.json()["review_note"] == "n"

    reopened = await _review(client, alert_id, headers, status="active")
    assert reopened.json()["review_outcome"] is None
    assert reopened.json()["review_note"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"status": "reviewed", "outcome": "bullying"},
        {"status": "active", "outcome": "false_alert"},
        {"status": "active", "note": "x"},
        {"status": "reviewed", "note": "note without outcome"},
        {"status": "reviewed", "outcome": "false_alert", "note": "x" * 501},
    ],
)
async def test_invalid_outcome_requests_are_rejected(client, org_identities, body):
    alert_id = await _create_alert(client)

    response = await client.post(
        f"/alerts/{alert_id}/review", json=body, headers=auth_headers(org_identities["admin_a"])
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_other_school_cannot_set_an_outcome(client, org_identities):
    alert_id = await _create_alert(client)

    response = await _review(
        client,
        alert_id,
        auth_headers(org_identities["admin_b"]),
        status="resolved",
        outcome="false_alert",
    )

    assert response.status_code == 404
    async with AsyncSessionLocal() as session:
        assert (await session.get(Alert, alert_id)).review_outcome is None


@pytest.mark.asyncio
async def test_outcome_change_is_audited_without_the_note(client, org_identities):
    alert_id = await _create_alert(client)
    headers = auth_headers(org_identities["staff_a"])

    await _review(client, alert_id, headers, status="reviewed")
    await _review(
        client, alert_id, headers, status="reviewed", outcome="joking_banter", note="secret"
    )
    await _review(
        client, alert_id, headers, status="reviewed", outcome="joking_banter", note="secret"
    )

    async with AsyncSessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "REVIEW_ALERT").order_by(AuditLog.id)
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 2
    assert rows[1].metadata_json["new_outcome"] == "joking_banter"
    assert rows[1].metadata_json["has_note"] is True
    assert "secret" not in str(rows[1].metadata_json)
    assert rows[1].school_id == org_identities["school_a_id"]


@pytest.mark.asyncio
async def test_report_counts_alerts_per_outcome_within_school(client, org_identities):
    headers = auth_headers(org_identities["counselor_a"])
    ids = [await _create_alert(client) for _ in range(4)]
    await _review(client, ids[0], headers, status="resolved", outcome="false_alert")
    await _review(client, ids[1], headers, status="resolved", outcome="false_alert")
    await _review(client, ids[2], headers, status="reviewed", outcome="confirmed_bullying")
    body = {"date_from": "2000-01-01", "date_to": "2100-01-01"}

    report = await client.post("/reports/", json=body, headers=headers)
    other = await client.post(
        "/reports/", json=body, headers=auth_headers(org_identities["admin_b"])
    )

    assert report.status_code == 201, report.text
    assert report.json()["total_incidents"] == 4
    assert report.json()["outcome_counts"] == {
        "confirmed_bullying": 1,
        "false_alert": 2,
        "joking_banter": 0,
        "misheard": 0,
        "not_set": 1,
    }
    assert other.json()["outcome_counts"] == dict.fromkeys(OUTCOMES + ["not_set"], 0)
    listed = await client.get("/reports/", headers=headers)
    assert listed.json()[0]["outcome_counts"]["false_alert"] == 2
