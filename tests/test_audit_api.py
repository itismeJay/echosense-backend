import csv
import io
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.audit_log import AuditLog
from app.models.user import User
from app.services.audit import (
    AuditAction,
    AuditResource,
    AuditStatus,
    record_audit_event,
)
from tests.conftest import auth_headers


async def _audit_events() -> list[AuditLog]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(AuditLog).order_by(AuditLog.id))
        return list(result.scalars().all())


@pytest.mark.asyncio
async def test_list_authorization_and_request_id(client, identities):
    unauthenticated = await client.get("/audit-logs")
    assert unauthenticated.status_code == 401

    staff = await client.get("/audit-logs", headers=auth_headers(identities["staff"]))
    counselor = await client.get(
        "/audit-logs",
        headers=auth_headers(identities["counselor"]),
    )
    assert staff.status_code == 403
    assert counselor.status_code == 403

    admin = await client.get("/audit-logs", headers=auth_headers(identities["admin"]))
    assert admin.status_code == 200
    assert admin.headers["x-request-id"]
    body = admin.json()
    assert body["page"] == 1
    assert body["page_size"] == 25
    assert body["total"] == 2
    assert {item["action"] for item in body["items"]} == {"PERMISSION_DENIED"}


@pytest.mark.asyncio
async def test_export_authorization_and_csv_contract(client, identities):
    unauthenticated = await client.get("/audit-logs/export")
    assert unauthenticated.status_code == 401

    staff = await client.get(
        "/audit-logs/export",
        headers=auth_headers(identities["staff"]),
    )
    counselor = await client.get(
        "/audit-logs/export",
        headers=auth_headers(identities["counselor"]),
    )
    assert staff.status_code == 403
    assert counselor.status_code == 403

    admin = await client.get(
        "/audit-logs/export",
        headers=auth_headers(identities["admin"]),
    )
    assert admin.status_code == 200
    assert admin.headers["content-type"].startswith("text/csv")
    assert "UTC.csv" in admin.headers["content-disposition"]

    rows = list(csv.reader(io.StringIO(admin.text)))
    assert rows[0] == [
        "occurred_at",
        "actor_email",
        "actor_role",
        "action",
        "resource",
        "resource_id",
        "target",
        "status",
        "description",
        "ip_address",
        "user_agent",
        "request_id",
    ]
    assert len(rows) == 3

    events = await _audit_events()
    assert [event.action for event in events].count("EXPORT_AUDIT_LOGS") == 1


@pytest.mark.asyncio
async def test_successful_and_failed_login_create_exactly_one_safe_event(
    client,
    identities,
):
    admin = identities["admin"]
    successful = await client.post(
        "/auth/login",
        json={"email": admin["email"], "password": admin["password"]},
    )
    assert successful.status_code == 200

    submitted_password = "wrong-password-that-must-not-be-stored"
    failed = await client.post(
        "/auth/login",
        json={"email": admin["email"], "password": submitted_password},
    )
    assert failed.status_code == 401
    assert failed.json()["detail"] == "Invalid credentials"

    events = await _audit_events()
    assert [event.action for event in events].count("LOGIN") == 1
    assert [event.action for event in events].count("LOGIN_FAILED") == 1
    failed_event = next(event for event in events if event.action == "LOGIN_FAILED")
    assert failed_event.actor_email == admin["email"]
    assert failed_event.actor_user_id is None
    assert failed_event.description == "Login attempt failed."

    serialised = json.dumps(
        [
            {
                "description": event.description,
                "target": event.target,
                "metadata": event.metadata_json,
            }
            for event in events
        ]
    )
    assert submitted_password not in serialised
    assert admin["password"] not in serialised


@pytest.mark.asyncio
async def test_user_create_delete_are_atomic_and_actor_snapshot_survives(
    client,
    identities,
):
    admin_headers = auth_headers(identities["admin"])
    created = await client.post(
        "/users/",
        headers=admin_headers,
        json={
            "email": "temporary.teacher@school.test",
            "password": "temporary-password",
            "role": "staff",
        },
    )
    assert created.status_code == 201
    user_id = created.json()["id"]

    deleted = await client.delete(f"/users/{user_id}", headers=admin_headers)
    assert deleted.status_code == 204

    events = await _audit_events()
    assert [event.action for event in events] == ["CREATE_USER", "DELETE_USER"]
    assert all(event.actor_user_id == identities["admin"]["id"] for event in events)
    assert all(event.actor_email == identities["admin"]["email"] for event in events)
    assert events[0].resource_id == user_id
    assert events[0].metadata_json == {"role": "staff"}
    assert events[1].target == "temporary.teacher@school.test"


@pytest.mark.asyncio
async def test_deleted_actor_foreign_key_is_set_null_but_snapshot_remains(
    client,
    identities,
):
    staff = identities["staff"]
    login = await client.post(
        "/auth/login",
        json={"email": staff["email"], "password": staff["password"]},
    )
    assert login.status_code == 200

    deleted = await client.delete(
        f"/users/{staff['id']}",
        headers=auth_headers(identities["admin"]),
    )
    assert deleted.status_code == 204

    events = await _audit_events()
    login_event = next(event for event in events if event.action == "LOGIN")
    assert login_event.actor_user_id is None
    assert login_event.actor_email == staff["email"]
    assert login_event.actor_role == "staff"


@pytest.mark.asyncio
async def test_monitored_term_and_settings_changes_are_audited_without_heartbeats(
    client,
    identities,
):
    headers = auth_headers(identities["admin"])
    created = await client.post(
        "/dictionary/",
        headers=headers,
        json={"slur_text": "temporary-term", "language": "ceb", "severity_weight": 0.7},
    )
    assert created.status_code == 201
    term_id = created.json()["term_id"]

    deleted = await client.delete(f"/dictionary/{term_id}", headers=headers)
    assert deleted.status_code == 200

    no_change = await client.put(
        "/system-settings/",
        headers=headers,
        json={"confidence_threshold": 0.55},
    )
    assert no_change.status_code == 200

    changed = await client.put(
        "/system-settings/",
        headers=headers,
        json={"confidence_threshold": 0.85},
    )
    assert changed.status_code == 200

    heartbeat = await client.post("/system-settings/heartbeat")
    assert heartbeat.status_code == 200

    events = await _audit_events()
    assert [event.action for event in events] == [
        "ADD_MONITORED_TERM",
        "DELETE_MONITORED_TERM",
        "UPDATE_SETTINGS",
    ]
    settings_event = events[-1]
    assert settings_event.metadata_json == {
        "changed_fields": ["confidence_threshold"],
        "new_values": {"confidence_threshold": 0.85},
        "previous_values": {"confidence_threshold": 0.55},
    }


@pytest.mark.asyncio
async def test_report_generation_is_audited(client, identities):
    generated = await client.post(
        "/reports/",
        headers=auth_headers(identities["counselor"]),
        json={"date_from": "2026-07-01", "date_to": "2026-07-27"},
    )
    assert generated.status_code == 201

    events = await _audit_events()
    assert len(events) == 1
    assert events[0].action == "GENERATE_REPORT"
    assert events[0].actor_role == "counselor"
    assert events[0].metadata_json["total_incidents"] == 0


async def _seed_filter_events() -> list[datetime]:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    moments = [now - timedelta(days=3), now - timedelta(days=2), now - timedelta(days=1), now]

    async with AsyncSessionLocal() as session:
        admin = (
            await session.execute(select(User).where(User.email == "admin@school.test"))
        ).scalar_one()
        await record_audit_event(
            session,
            None,
            AuditAction.LOGIN,
            AuditResource.AUTHENTICATION,
            AuditStatus.SUCCESS,
            actor=admin,
            target="first",
            description="User signed in successfully.",
            occurred_at=moments[0],
        )
        await record_audit_event(
            session,
            None,
            AuditAction.ADD_MONITORED_TERM,
            AuditResource.MONITORED_TERM,
            AuditStatus.SUCCESS,
            actor=admin,
            target="term alpha",
            description="Administrator added a monitored term.",
            occurred_at=moments[1],
        )
        await record_audit_event(
            session,
            None,
            AuditAction.UPDATE_SETTINGS,
            AuditResource.SETTINGS,
            AuditStatus.SUCCESS,
            actor=admin,
            target="100% safe",
            description="Administrator updated detection settings.",
            occurred_at=moments[2],
        )
        await record_audit_event(
            session,
            None,
            AuditAction.PERMISSION_DENIED,
            AuditResource.SECURITY,
            AuditStatus.FAILURE,
            actor=admin,
            target="/restricted",
            description="User was denied access.",
            occurred_at=moments[3],
        )
        await session.commit()
    return moments


@pytest.mark.asyncio
async def test_sorting_pagination_search_and_field_filters(client, identities):
    await _seed_filter_events()
    headers = auth_headers(identities["admin"])

    newest = await client.get("/audit-logs?page=1&page_size=2", headers=headers)
    assert newest.status_code == 200
    assert newest.json()["total"] == 4
    assert newest.json()["total_pages"] == 2
    assert [item["action"] for item in newest.json()["items"]] == [
        "PERMISSION_DENIED",
        "UPDATE_SETTINGS",
    ]

    ascending = await client.get(
        "/audit-logs?sort_order=asc",
        headers=headers,
    )
    assert [item["action"] for item in ascending.json()["items"]] == [
        "LOGIN",
        "ADD_MONITORED_TERM",
        "UPDATE_SETTINGS",
        "PERMISSION_DENIED",
    ]

    second_page = await client.get("/audit-logs?page=2&page_size=2", headers=headers)
    assert [item["action"] for item in second_page.json()["items"]] == [
        "ADD_MONITORED_TERM",
        "LOGIN",
    ]

    cases = [
        ("search=term%20alpha", "ADD_MONITORED_TERM"),
        ("search=%25", "UPDATE_SETTINGS"),
        ("actor_email=admin%40school.test", None),
        ("actor_role=admin", None),
        ("action=LOGIN", "LOGIN"),
        ("resource=Security", "PERMISSION_DENIED"),
        ("status=FAILURE", "PERMISSION_DENIED"),
    ]
    for query, expected_action in cases:
        response = await client.get(f"/audit-logs?{query}", headers=headers)
        assert response.status_code == 200
        assert response.json()["total"] >= 1
        if expected_action:
            assert {item["action"] for item in response.json()["items"]} == {expected_action}


@pytest.mark.asyncio
async def test_date_filters_and_invalid_query_values(client, identities):
    moments = await _seed_filter_events()
    headers = auth_headers(identities["admin"])
    date_from = moments[1].isoformat().replace("+00:00", "Z")
    date_to = moments[2].isoformat().replace("+00:00", "Z")

    filtered = await client.get(
        f"/audit-logs?date_from={date_from}&date_to={date_to}",
        headers=headers,
    )
    assert filtered.status_code == 200
    assert [item["action"] for item in filtered.json()["items"]] == [
        "UPDATE_SETTINGS",
        "ADD_MONITORED_TERM",
    ]

    invalid_queries = [
        "date_from=not-a-date",
        "date_from=2026-07-28&date_to=2026-07-27",
        "page=0",
        "page_size=0",
        "page_size=101",
        "sort_order=sideways",
        "status=MAYBE",
    ]
    for query in invalid_queries:
        response = await client.get(f"/audit-logs?{query}", headers=headers)
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_api_response_uses_contract_and_audit_records_are_not_mutable(
    client,
    identities,
):
    await _seed_filter_events()
    headers = auth_headers(identities["admin"])
    listed = await client.get("/audit-logs?action=LOGIN", headers=headers)
    assert listed.status_code == 200
    item = listed.json()["items"][0]
    assert list(item) == [
        "id",
        "occurred_at",
        "actor_user_id",
        "actor_email",
        "actor_role",
        "action",
        "resource",
        "resource_id",
        "target",
        "status",
        "description",
        "ip_address",
        "user_agent",
        "request_id",
        "metadata",
        "created_at",
    ]
    assert item["id"].isdigit()
    assert item["actor_user_id"].isdigit()
    assert item["occurred_at"].endswith("Z")
    assert item["created_at"].endswith("Z")
    assert item["metadata"] == {}

    update = await client.put(
        f"/audit-logs/{item['id']}",
        headers=headers,
        json={"status": "FAILURE"},
    )
    delete = await client.delete(f"/audit-logs/{item['id']}", headers=headers)
    assert update.status_code in {404, 405}
    assert delete.status_code in {404, 405}


@pytest.mark.asyncio
async def test_csv_escaping_and_export_filters(client, identities):
    async with AsyncSessionLocal() as session:
        admin = (
            await session.execute(select(User).where(User.email == "admin@school.test"))
        ).scalar_one()
        await record_audit_event(
            session,
            None,
            AuditAction.LOGIN,
            AuditResource.AUTHENTICATION,
            AuditStatus.SUCCESS,
            actor=admin,
            target="=SUM(1,1)",
            description='Description with a "quote", comma, and newline\nremoved.',
        )
        await record_audit_event(
            session,
            None,
            AuditAction.UPDATE_SETTINGS,
            AuditResource.SETTINGS,
            AuditStatus.SUCCESS,
            actor=admin,
        )
        await session.commit()

    exported = await client.get(
        "/audit-logs/export?action=LOGIN",
        headers=auth_headers(identities["admin"]),
    )
    assert exported.status_code == 200
    rows = list(csv.DictReader(io.StringIO(exported.text)))
    assert len(rows) == 1
    assert rows[0]["action"] == "LOGIN"
    assert rows[0]["target"] == "'=SUM(1,1)"
    assert rows[0]["description"] == 'Description with a "quote", comma, and newline removed.'
    assert "metadata" not in rows[0]
