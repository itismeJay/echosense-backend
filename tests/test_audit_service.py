import json
from datetime import datetime, timezone

import pytest
from fastapi import Request
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError

from app.database import AsyncSessionLocal
from app.models.audit_log import AuditLog
from app.services.audit import (
    AuditAction,
    AuditResource,
    AuditStatus,
    record_audit_event,
    sanitise_metadata,
)


def _request(headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/test",
            "raw_path": b"/test",
            "query_string": b"",
            "headers": headers or [],
            "client": ("127.0.0.1", 4321),
            "server": ("testserver", 80),
        }
    )
    request.state.request_id = "e184bc9e-4ea6-45a5-a060-b47378842003"
    return request


def test_sensitive_metadata_is_removed_recursively():
    metadata = {
        "changed_fields": ["role"],
        "password": "plain-text-password",
        "accessToken": "token-value",
        "nested": {
            "safe": "kept",
            "secret_key": "secret-value",
            "items": [
                {"authorization": "Bearer hidden", "value": 1},
                {"sessionCookie": "hidden-cookie", "value": 2},
            ],
        },
    }

    clean = sanitise_metadata(metadata)
    encoded = json.dumps(clean)

    assert clean["changed_fields"] == ["role"]
    assert clean["nested"]["safe"] == "kept"
    assert clean["nested"]["items"] == [{"value": 1}, {"value": 2}]
    for forbidden in (
        "plain-text-password",
        "token-value",
        "secret-value",
        "Bearer hidden",
        "hidden-cookie",
    ):
        assert forbidden not in encoded


@pytest.mark.asyncio
async def test_service_records_safe_context_and_timezone_aware_timestamps():
    request = _request(
        [
            (b"user-agent", b"Audit Service Test"),
            (b"x-forwarded-for", b"203.0.113.10"),
        ]
    )

    async with AsyncSessionLocal() as session:
        event = await record_audit_event(
            session,
            request,
            AuditAction.LOGIN_FAILED,
            AuditResource.AUTHENTICATION,
            AuditStatus.FAILURE,
            actor_email="attempt@example.test",
            description="Login attempt failed.",
            metadata={"safe": True, "password": "must-not-survive"},
        )
        await session.commit()
        event_id = event.id

    async with AsyncSessionLocal() as session:
        event = (
            await session.execute(select(AuditLog).where(AuditLog.id == event_id))
        ).scalar_one()

    assert event.occurred_at.tzinfo is not None
    assert event.created_at.tzinfo is not None
    assert event.occurred_at.utcoffset() == timezone.utc.utcoffset(datetime.now(timezone.utc))
    assert event.created_at.utcoffset() == timezone.utc.utcoffset(datetime.now(timezone.utc))
    assert event.ip_address == "127.0.0.1"
    assert event.user_agent == "Audit Service Test"
    assert event.request_id == "e184bc9e-4ea6-45a5-a060-b47378842003"
    assert event.metadata_json == {"safe": True}


@pytest.mark.asyncio
async def test_invalid_action_or_status_is_rejected_before_insert():
    async with AsyncSessionLocal() as session:
        with pytest.raises(ValueError, match="machine-readable"):
            await record_audit_event(
                session,
                None,
                "free form action",
                AuditResource.SECURITY,
                AuditStatus.FAILURE,
            )
        with pytest.raises(ValueError, match="SUCCESS or FAILURE"):
            await record_audit_event(
                session,
                None,
                AuditAction.LOGIN,
                AuditResource.AUTHENTICATION,
                "MAYBE",
            )

        count = (await session.execute(select(AuditLog))).scalars().all()
        assert count == []


@pytest.mark.asyncio
async def test_database_rejects_audit_update_delete_and_truncate():
    async with AsyncSessionLocal() as session:
        event = await record_audit_event(
            session,
            None,
            AuditAction.LOGIN_FAILED,
            AuditResource.AUTHENTICATION,
            AuditStatus.FAILURE,
            actor_email="append-only@example.test",
        )
        await session.commit()
        event_id = event.id

    statements = [
        update(AuditLog).where(AuditLog.id == event_id).values(status="SUCCESS"),
        delete(AuditLog).where(AuditLog.id == event_id),
        text("TRUNCATE TABLE audit_logs"),
    ]
    for statement in statements:
        async with AsyncSessionLocal() as session:
            with pytest.raises(DBAPIError, match="audit logs are append-only"):
                await session.execute(statement)
                await session.commit()
            await session.rollback()

    async with AsyncSessionLocal() as session:
        event = (
            await session.execute(select(AuditLog).where(AuditLog.id == event_id))
        ).scalar_one()
        assert event.status == "FAILURE"
