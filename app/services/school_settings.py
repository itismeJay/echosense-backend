"""Per-school settings: lookup with defaults, the push decision, and the effective word list."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.school import School
from app.models.school_settings import SchoolDisabledTerm, SchoolSettings, SchoolTerm
from app.models.slur import SlurEntry
from app.models.user import User
from app.services.school_access import is_global_admin, resolve_school_scope

SCHOOL_REQUIRED = "Enter a school to manage its settings"

# PushDecision.reason values, stored in alerts.push_last_error when a push is skipped.
OUTSIDE_SCHOOL_HOURS = "outside_school_hours"
SEVERITY_PUSH_DISABLED = "severity_push_disabled"


async def get_school_settings(db: AsyncSession, school_id: UUID) -> SchoolSettings:
    """The school's settings row, created with the defaults if it does not exist yet."""

    row = await db.get(SchoolSettings, school_id)
    if row is not None:
        return row
    await db.execute(
        insert(SchoolSettings)
        .values(school_id=school_id)
        .on_conflict_do_nothing(index_elements=["school_id"])
    )
    await db.flush()
    return await db.get(SchoolSettings, school_id, populate_existing=True)


async def resolve_settings_school(db: AsyncSession, user: User, school_id: UUID | None) -> UUID:
    """The one school a settings request acts on.

    School admins always act on their own school (a foreign school_id is a 403). A super
    admin acts on the school they entered, which the dashboard sends as ?school_id.
    """

    scoped = resolve_school_scope(user, school_id)
    if scoped is None:
        if not is_global_admin(user):
            raise HTTPException(status_code=403, detail="Administrator is not assigned to a school")
        raise HTTPException(status_code=422, detail=SCHOOL_REQUIRED)
    if await db.get(School, scoped) is None:
        raise HTTPException(status_code=404, detail="School not found")
    return scoped


def school_zone(settings: SchoolSettings) -> ZoneInfo:
    try:
        return ZoneInfo(settings.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Asia/Manila")


def is_within_school_hours(settings: SchoolSettings, moment: datetime) -> bool:
    """True when school hours are off, or `moment` falls on a school day within the hours.

    An end time earlier than the start time spans midnight (e.g. 22:00-06:00); the
    weekday is the day the period started.
    """

    if not settings.school_hours_enabled:
        return True
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    local = moment.astimezone(school_zone(settings))
    clock = local.time().replace(tzinfo=None)
    start, end = settings.school_start_time, settings.school_end_time
    days = set(settings.school_days or [])
    if start <= end:
        return local.isoweekday() in days and start <= clock < end
    if clock >= start:
        return local.isoweekday() in days
    if clock < end:
        previous_day = (local.isoweekday() - 2) % 7 + 1
        return previous_day in days
    return False


@dataclass(frozen=True)
class PushDecision:
    send: bool
    outside_school_hours: bool
    reason: str | None


def decide_push(settings: SchoolSettings, severity: str, occurred_at: datetime) -> PushDecision:
    """Whether a new alert is pushed. The alert is stored either way."""

    outside = not is_within_school_hours(settings, occurred_at)
    if outside:
        return PushDecision(False, True, OUTSIDE_SCHOOL_HOURS)
    if severity.strip().upper() not in set(settings.push_levels or []):
        return PushDecision(False, False, SEVERITY_PUSH_DISABLED)
    return PushDecision(True, False, None)


async def disabled_term_ids(db: AsyncSession, school_id: UUID) -> set[int]:
    result = await db.execute(
        select(SchoolDisabledTerm.term_id).where(SchoolDisabledTerm.school_id == school_id)
    )
    return set(result.scalars().all())


async def list_school_terms(db: AsyncSession, school_id: UUID) -> list[SchoolTerm]:
    result = await db.execute(
        select(SchoolTerm)
        .where(SchoolTerm.school_id == school_id)
        .order_by(SchoolTerm.severity_weight.desc(), SchoolTerm.id)
    )
    return list(result.scalars().all())


async def effective_terms(db: AsyncSession, school_id: UUID) -> list[dict]:
    """Global terms minus the ones this school turned off, plus this school's own terms."""

    disabled = await disabled_term_ids(db, school_id)
    global_rows = (
        await db.execute(
            select(SlurEntry).order_by(SlurEntry.severity_weight.desc(), SlurEntry.term_id)
        )
    ).scalars().all()
    terms = [
        {
            "text": row.slur_text,
            "language": row.language,
            "severity_weight": row.severity_weight,
            "source": "global",
            "term_id": row.term_id,
        }
        for row in global_rows
        if row.term_id not in disabled
    ]
    terms.extend(
        {
            "text": row.term_text,
            "language": row.language,
            "severity_weight": row.severity_weight,
            "source": "school",
            "term_id": row.id,
        }
        for row in await list_school_terms(db, school_id)
    )
    return terms


def config_version(payload: dict) -> str:
    """A stable hash of the device config so the edge can skip reloading unchanged config."""

    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
