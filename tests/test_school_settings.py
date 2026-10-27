"""Per-school settings: access rules, audit, push gating and the edge's /devices/me/config."""

from datetime import datetime, time, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.alert import Alert
from app.models.audit_log import AuditLog
from app.models.edge_device import EdgeDevice
from app.models.school_settings import SchoolSettings
from app.models.slur import SlurEntry
from app.services.school_settings import decide_push, is_within_school_hours
from tests.conftest import (
    TEST_DEVICE_CODE,
    TEST_DEVICE_KEY,
    auth_headers,
    finalized_alert_fields,
)

DEFAULT_HOURS = {
    "enabled": False,
    "weekdays": [1, 2, 3, 4, 5],
    "start": "07:00:00",
    "end": "17:00:00",
    "timezone": "Asia/Manila",
}
SCHOOL_HOURS_ON = {
    "enabled": True,
    "weekdays": [1, 2, 3, 4, 5],
    "start": "07:00",
    "end": "17:00",
    "timezone": "Asia/Manila",
}


def _scope(identities, key="school_a_id"):
    return {"school_id": str(identities[key])}


async def _seed_global_terms(*texts: str) -> list[int]:
    async with AsyncSessionLocal() as session:
        entries = [
            SlurEntry(slur_text=text, language="en", severity_weight=0.8) for text in texts
        ]
        session.add_all(entries)
        await session.commit()
        return [entry.term_id for entry in entries]


async def _audit(action: str) -> list[AuditLog]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id)
        )
        return list(result.scalars().all())


# --- settings read/update and access -------------------------------------------------


@pytest.mark.asyncio
async def test_school_admin_reads_defaults_for_own_school(client, org_identities):
    response = await client.get(
        "/school-settings", headers=auth_headers(org_identities["admin_a"])
    )

    assert response.status_code == 200
    body = response.json()
    assert body["school_id"] == str(org_identities["school_a_id"])
    assert body["push_levels"] == ["HIGH", "MEDIUM"]
    assert body["school_hours"] == DEFAULT_HOURS
    assert body["sensitivity"] == "normal"


@pytest.mark.asyncio
async def test_new_school_is_created_with_a_settings_row(client, org_identities):
    created = await client.post(
        "/schools", json={"name": "Fresh School"}, headers=auth_headers(org_identities["super"])
    )
    assert created.status_code == 201

    async with AsyncSessionLocal() as session:
        row = await session.get(SchoolSettings, created.json()["id"])
    assert row is not None
    assert row.push_levels == ["HIGH", "MEDIUM"]
    assert row.sensitivity == "normal"
    assert row.school_hours_enabled is False


@pytest.mark.asyncio
async def test_school_admin_edits_only_own_school_and_it_is_audited(client, org_identities):
    headers = auth_headers(org_identities["admin_a"])
    update = {
        "push_levels": ["LOW", "HIGH"],
        "school_hours": SCHOOL_HOURS_ON,
        "sensitivity": "strict",
    }

    own = await client.patch("/school-settings", json=update, headers=headers)
    foreign = await client.patch(
        "/school-settings",
        params=_scope(org_identities, "school_b_id"),
        json={"sensitivity": "strict"},
        headers=headers,
    )
    other_school_view = await client.get(
        "/school-settings", headers=auth_headers(org_identities["admin_b"])
    )

    assert own.status_code == 200
    assert own.json()["push_levels"] == ["HIGH", "LOW"]
    assert own.json()["school_hours"]["enabled"] is True
    assert own.json()["school_hours"]["start"] == "07:00:00"
    assert own.json()["sensitivity"] == "strict"
    assert foreign.status_code == 403
    assert other_school_view.json()["sensitivity"] == "normal"

    [event] = await _audit("UPDATE_SCHOOL_SETTINGS")
    assert event.school_id == org_identities["school_a_id"]
    assert event.actor_email == "admin_a@org.test"
    assert event.metadata_json["changed_fields"] == ["push_levels", "school_hours", "sensitivity"]
    assert event.metadata_json["previous_values"]["push_levels"] == ["HIGH", "MEDIUM"]
    assert event.metadata_json["new_values"]["sensitivity"] == "strict"


@pytest.mark.asyncio
async def test_unchanged_update_writes_no_audit_event(client, org_identities):
    response = await client.patch(
        "/school-settings",
        json={"push_levels": ["MEDIUM", "HIGH"], "sensitivity": "normal"},
        headers=auth_headers(org_identities["admin_a"]),
    )

    assert response.status_code == 200
    assert await _audit("UPDATE_SCHOOL_SETTINGS") == []


@pytest.mark.asyncio
async def test_super_admin_edits_the_school_they_entered(client, org_identities):
    headers = auth_headers(org_identities["super"])

    outside = await client.get("/school-settings", headers=headers)
    inside = await client.patch(
        "/school-settings",
        params=_scope(org_identities, "school_b_id"),
        json={"sensitivity": "strict"},
        headers=headers,
    )
    unknown = await client.get(
        "/school-settings", params={"school_id": str(uuid4())}, headers=headers
    )

    assert outside.status_code == 422
    assert inside.status_code == 200
    assert inside.json()["school_id"] == str(org_identities["school_b_id"])
    assert unknown.status_code == 404
    [event] = await _audit("UPDATE_SCHOOL_SETTINGS")
    assert event.school_id == org_identities["school_b_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ["counselor_a", "staff_a"])
async def test_counselors_and_staff_cannot_read_or_edit(client, org_identities, identity):
    headers = auth_headers(org_identities[identity])

    read = await client.get("/school-settings", headers=headers)
    edit = await client.patch("/school-settings", json={"sensitivity": "strict"}, headers=headers)
    terms = await client.get("/school-settings/terms", headers=headers)

    assert (read.status_code, edit.status_code, terms.status_code) == (403, 403, 403)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"push_levels": ["URGENT"]},
        {"sensitivity": "relaxed"},
        {"school_hours": {**SCHOOL_HOURS_ON, "weekdays": [0, 8]}},
        {"school_hours": {**SCHOOL_HOURS_ON, "timezone": "Mars/Olympus"}},
        {"school_hours": {**SCHOOL_HOURS_ON, "enabled": "yes"}},
        {"confidence_threshold": 0.9},
    ],
)
async def test_invalid_settings_are_rejected(client, org_identities, body):
    response = await client.patch(
        "/school-settings", json=body, headers=auth_headers(org_identities["admin_a"])
    )

    assert response.status_code == 422


# --- monitored words -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_school_words_and_disabled_global_words_are_per_school(client, org_identities):
    [kept_id, off_id] = await _seed_global_terms("global kept", "global off")
    a = auth_headers(org_identities["admin_a"])

    added = await client.post(
        "/school-settings/terms",
        json={"text": "  local   word ", "language": "ceb", "severity_weight": 0.7},
        headers=a,
    )
    turned_off = await client.put(
        f"/school-settings/global-terms/{off_id}", json={"enabled": False}, headers=a
    )
    a_terms = await client.get("/school-settings/terms", headers=a)
    b_terms = await client.get(
        "/school-settings/terms", headers=auth_headers(org_identities["admin_b"])
    )

    assert added.status_code == 201
    assert added.json()["text"] == "local word"
    assert turned_off.json()["enabled_for_school"] is False
    enabled = {term["term_id"]: term["enabled_for_school"] for term in a_terms.json()["global_terms"]}
    assert enabled == {kept_id: True, off_id: False}
    assert [term["text"] for term in a_terms.json()["school_terms"]] == ["local word"]
    assert all(term["enabled_for_school"] for term in b_terms.json()["global_terms"])
    assert b_terms.json()["school_terms"] == []

    assert [event.target for event in await _audit("ADD_SCHOOL_TERM")] == ["local word"]
    [disabled_event] = await _audit("DISABLE_GLOBAL_TERM")
    assert disabled_event.school_id == org_identities["school_a_id"]

    back_on = await client.put(
        f"/school-settings/global-terms/{off_id}", json={"enabled": True}, headers=a
    )
    assert back_on.json()["enabled_for_school"] is True
    assert len(await _audit("ENABLE_GLOBAL_TERM")) == 1


@pytest.mark.asyncio
async def test_duplicate_words_are_rejected(client, org_identities):
    await _seed_global_terms("already global")
    a = auth_headers(org_identities["admin_a"])
    first = await client.post(
        "/school-settings/terms", json={"text": "ours", "language": "en"}, headers=a
    )

    global_dup = await client.post(
        "/school-settings/terms", json={"text": "Already  Global", "language": "en"}, headers=a
    )
    school_dup = await client.post(
        "/school-settings/terms", json={"text": "OURS", "language": "en"}, headers=a
    )
    other_school_same_word = await client.post(
        "/school-settings/terms",
        json={"text": "ours", "language": "en"},
        headers=auth_headers(org_identities["admin_b"]),
    )

    assert first.status_code == 201
    assert global_dup.status_code == 409
    assert school_dup.status_code == 409
    assert other_school_same_word.status_code == 201


@pytest.mark.asyncio
async def test_a_school_cannot_delete_another_schools_word(client, org_identities):
    created = await client.post(
        "/school-settings/terms",
        json={"text": "a only", "language": "en"},
        headers=auth_headers(org_identities["admin_a"]),
    )
    term_id = created.json()["id"]

    foreign = await client.delete(
        f"/school-settings/terms/{term_id}", headers=auth_headers(org_identities["admin_b"])
    )
    own = await client.delete(
        f"/school-settings/terms/{term_id}", headers=auth_headers(org_identities["admin_a"])
    )

    assert foreign.status_code == 404
    assert own.status_code == 204
    assert [event.target for event in await _audit("DELETE_SCHOOL_TERM")] == ["a only"]


# --- push decision -------------------------------------------------------------------


def _settings(**overrides):
    values = {
        "push_levels": ["HIGH", "MEDIUM"],
        "school_hours_enabled": True,
        "school_days": [1, 2, 3, 4, 5],
        "school_start_time": time(7, 0),
        "school_end_time": time(17, 0),
        "timezone": "Asia/Manila",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


# 2026-08-04 is a Tuesday. Manila is UTC+8.
TUESDAY_0800_MANILA = datetime(2026, 8, 4, 0, 0, tzinfo=timezone.utc)
TUESDAY_1700_MANILA = datetime(2026, 8, 4, 9, 0, tzinfo=timezone.utc)
TUESDAY_0659_MANILA = datetime(2026, 8, 3, 22, 59, tzinfo=timezone.utc)
SATURDAY_1000_MANILA = datetime(2026, 8, 8, 2, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (TUESDAY_0800_MANILA, True),
        (TUESDAY_1700_MANILA, False),
        (TUESDAY_0659_MANILA, False),
        (SATURDAY_1000_MANILA, False),
    ],
)
def test_school_hours_use_the_school_timezone(moment, expected):
    assert is_within_school_hours(_settings(), moment) is expected


def test_school_hours_off_means_always_within():
    assert is_within_school_hours(_settings(school_hours_enabled=False), SATURDAY_1000_MANILA)


def test_overnight_hours_belong_to_the_day_they_start():
    night = _settings(
        school_start_time=time(22, 0), school_end_time=time(6, 0), school_days=[5]
    )
    friday_2300 = datetime(2026, 8, 7, 15, 0, tzinfo=timezone.utc)
    saturday_0500 = datetime(2026, 8, 7, 21, 0, tzinfo=timezone.utc)
    saturday_2300 = datetime(2026, 8, 8, 15, 0, tzinfo=timezone.utc)

    assert is_within_school_hours(night, friday_2300)
    assert is_within_school_hours(night, saturday_0500)
    assert not is_within_school_hours(night, saturday_2300)


@pytest.mark.parametrize(
    ("severity", "moment", "send", "outside", "reason"),
    [
        ("HIGH", TUESDAY_0800_MANILA, True, False, None),
        ("medium", TUESDAY_0800_MANILA, True, False, None),
        ("LOW", TUESDAY_0800_MANILA, False, False, "severity_push_disabled"),
        ("HIGH", SATURDAY_1000_MANILA, False, True, "outside_school_hours"),
    ],
)
def test_push_decision(severity, moment, send, outside, reason):
    decision = decide_push(_settings(), severity, moment)

    assert (decision.send, decision.outside_school_hours, decision.reason) == (
        send,
        outside,
        reason,
    )


def _alert(severity: str, *, start="2026-08-04T00:00:00Z") -> dict:
    payload = finalized_alert_fields()
    payload.update(
        {
            "severity": severity,
            "confidence": 0.8,
            "duration": 1.0,
            "location": "Synthetic Test Classroom",
            "transcribed_text": "synthetic",
            "language": "en",
            "yamnet_ran": False,
            "yamnet_class": "NotRun",
            "yamnet_score": 0.0,
            "event_start_timestamp": start,
            "event_end_timestamp": start.replace(":00Z", ":01Z"),
        }
    )
    return payload


async def _stored_alert(alert_id: int) -> Alert:
    async with AsyncSessionLocal() as session:
        return await session.get(Alert, alert_id)


async def _set_school_settings(**values) -> None:
    async with AsyncSessionLocal() as session:
        school_id = await session.scalar(
            select(EdgeDevice.school_id).where(EdgeDevice.device_code == TEST_DEVICE_CODE)
        )
        session.add(SchoolSettings(school_id=school_id, **values))
        await session.commit()


@pytest.mark.asyncio
async def test_default_push_levels_skip_low_but_store_it(client, prevent_external_notifications):
    low = await client.post("/alerts/", json=_alert("low"))
    high = await client.post("/alerts/", json=_alert("high"))

    assert low.status_code == 200 and high.status_code == 200
    assert low.json()["push_status"] == "skipped"
    assert low.json()["outside_school_hours"] is False
    stored_low = await _stored_alert(low.json()["id"])
    assert stored_low.push_last_error == "severity_push_disabled"
    pushed_severities = [call.args[2] for call in prevent_external_notifications.await_args_list]
    assert pushed_severities == ["HIGH"]


@pytest.mark.asyncio
async def test_school_can_turn_on_low_pushes(client, prevent_external_notifications):
    await _set_school_settings(push_levels=["LOW"])

    low = await client.post("/alerts/", json=_alert("low"))
    high = await client.post("/alerts/", json=_alert("high"))

    assert [call.args[2] for call in prevent_external_notifications.await_args_list] == ["LOW"]
    assert high.json()["push_status"] == "skipped"
    assert low.json()["push_status"] != "skipped"


@pytest.mark.asyncio
async def test_alert_outside_school_hours_is_stored_without_push(
    client, prevent_external_notifications
):
    await _set_school_settings(school_hours_enabled=True)

    # Saturday 10:00 in Manila.
    weekend = await client.post("/alerts/", json=_alert("high", start="2026-08-08T02:00:00Z"))
    weekday = await client.post("/alerts/", json=_alert("high"))

    assert weekend.status_code == 200
    assert weekend.json()["outside_school_hours"] is True
    assert weekend.json()["push_status"] == "skipped"
    stored = await _stored_alert(weekend.json()["id"])
    assert stored.outside_school_hours is True
    assert stored.push_last_error == "outside_school_hours"
    assert weekday.json()["outside_school_hours"] is False
    assert prevent_external_notifications.await_count == 1


@pytest.mark.asyncio
async def test_outside_hours_flag_is_visible_to_reviewers(client, identities):
    await _set_school_settings(school_hours_enabled=True)
    created = await client.post("/alerts/", json=_alert("high", start="2026-08-08T02:00:00Z"))

    listed = await client.get("/alerts/", headers=auth_headers(identities["staff"]))

    [alert] = [item for item in listed.json() if item["id"] == created.json()["id"]]
    assert alert["outside_school_hours"] is True


# --- /devices/me/config --------------------------------------------------------------


@pytest.mark.asyncio
async def test_device_config_requires_device_credentials(unauthenticated_edge_client, identities):
    anonymous = await unauthenticated_edge_client.get("/devices/me/config")
    wrong_key = await unauthenticated_edge_client.get(
        "/devices/me/config",
        headers={"X-EchoSense-Device-Id": TEST_DEVICE_CODE, "X-EchoSense-Device-Key": "nope"},
    )
    user_token = await unauthenticated_edge_client.get(
        "/devices/me/config", headers=auth_headers(identities["admin"])
    )

    assert (anonymous.status_code, wrong_key.status_code, user_token.status_code) == (
        401,
        401,
        401,
    )


@pytest.mark.asyncio
async def test_device_config_returns_the_schools_effective_words(client, identities):
    [kept_id, off_id] = await _seed_global_terms("global kept", "global off")
    admin = auth_headers(identities["admin"])
    scope = {"school_id": str(identities["admin"]["school_id"])}

    before = await client.get("/devices/me/config")
    await client.put(
        f"/school-settings/global-terms/{off_id}",
        params=scope,
        json={"enabled": False},
        headers=admin,
    )
    added = await client.post(
        "/school-settings/terms",
        params=scope,
        json={"text": "school word", "language": "fil", "severity_weight": 0.6},
        headers=admin,
    )
    await client.patch(
        "/school-settings", params=scope, json={"sensitivity": "strict"}, headers=admin
    )
    after = await client.get("/devices/me/config")

    assert before.status_code == 200
    assert before.json()["sensitivity"] == "normal"
    assert [term["text"] for term in before.json()["terms"]] == ["global kept", "global off"]

    body = after.json()
    assert body["schema_version"] == 1
    assert body["device_code"] == TEST_DEVICE_CODE
    assert body["school_id"] == scope["school_id"]
    assert body["sensitivity"] == "strict"
    assert body["terms"] == [
        {
            "text": "global kept",
            "language": "en",
            "severity_weight": 0.8,
            "source": "global",
            "term_id": kept_id,
        },
        {
            "text": "school word",
            "language": "fil",
            "severity_weight": 0.6,
            "source": "school",
            "term_id": added.json()["id"],
        },
    ]
    assert len(body["config_version"]) == 64
    assert body["config_version"] != before.json()["config_version"]
    again = await client.get("/devices/me/config")
    assert again.json()["config_version"] == body["config_version"]


@pytest.mark.asyncio
async def test_device_config_never_includes_another_schools_words(client, org_identities):
    await client.post(
        "/school-settings/terms",
        params=_scope(org_identities, "school_b_id"),
        json={"text": "school b only", "language": "en"},
        headers=auth_headers(org_identities["super"]),
    )

    # The test device belongs to school A.
    config = await client.get("/devices/me/config")

    assert config.json()["school_id"] == str(org_identities["school_a_id"])
    assert "school b only" not in {term["text"] for term in config.json()["terms"]}


@pytest.mark.asyncio
async def test_unassigned_device_gets_409(client):
    async with AsyncSessionLocal() as session:
        device = await session.scalar(
            select(EdgeDevice).where(EdgeDevice.device_code == TEST_DEVICE_CODE)
        )
        device.school_id = None
        device.classroom_id = None
        await session.commit()

    response = await client.get(
        "/devices/me/config",
        headers={"X-EchoSense-Device-Id": TEST_DEVICE_CODE, "X-EchoSense-Device-Key": TEST_DEVICE_KEY},
    )

    assert response.status_code == 409
