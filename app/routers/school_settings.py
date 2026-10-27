"""Per-school settings. School admins manage their own school; a super admin manages the
school they entered (the dashboard sends it as ?school_id). Counselors and staff have no
access. Every change is audit-logged against the school it changed."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.school_settings import SchoolDisabledTerm, SchoolSettings, SchoolTerm
from app.models.slur import SlurEntry
from app.models.user import User
from app.routers.auth import require_admin
from app.schemas.alert import normalize_term
from app.schemas.school_settings import (
    GlobalTermForSchool,
    GlobalTermToggle,
    SchoolHours,
    SchoolSettingsOut,
    SchoolSettingsUpdate,
    SchoolTermCreate,
    SchoolTermOut,
    SchoolTermsOut,
)
from app.services.audit import AuditAction, AuditResource, AuditStatus, record_audit_event
from app.services.school_settings import (
    disabled_term_ids,
    get_school_settings,
    list_school_terms,
    resolve_settings_school,
)

router = APIRouter(prefix="/school-settings", tags=["School Settings"])


def settings_out(row: SchoolSettings) -> SchoolSettingsOut:
    return SchoolSettingsOut(
        school_id=row.school_id,
        push_levels=list(row.push_levels or []),
        school_hours=SchoolHours(
            enabled=row.school_hours_enabled,
            weekdays=list(row.school_days or []),
            start=row.school_start_time,
            end=row.school_end_time,
            timezone=row.timezone,
        ),
        sensitivity=row.sensitivity,
        updated_at=row.updated_at,
    )


def _hours_snapshot(row: SchoolSettings) -> dict:
    return {
        "enabled": row.school_hours_enabled,
        "weekdays": list(row.school_days or []),
        "start": row.school_start_time.strftime("%H:%M"),
        "end": row.school_end_time.strftime("%H:%M"),
        "timezone": row.timezone,
    }


@router.get("", response_model=SchoolSettingsOut)
@router.get("/", response_model=SchoolSettingsOut, include_in_schema=False)
async def read_school_settings(
    school_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    target = await resolve_settings_school(db, current_user, school_id)
    row = await get_school_settings(db, target)
    await db.commit()
    return settings_out(row)


@router.patch("", response_model=SchoolSettingsOut)
@router.patch("/", response_model=SchoolSettingsOut, include_in_schema=False)
async def update_school_settings(
    request: Request,
    body: SchoolSettingsUpdate,
    school_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    target = await resolve_settings_school(db, current_user, school_id)
    row = await get_school_settings(db, target)

    previous: dict = {}
    updated: dict = {}
    if body.push_levels is not None and body.push_levels != list(row.push_levels or []):
        previous["push_levels"] = list(row.push_levels or [])
        updated["push_levels"] = body.push_levels
        row.push_levels = body.push_levels
    if body.school_hours is not None:
        before = _hours_snapshot(row)
        row.school_hours_enabled = body.school_hours.enabled
        row.school_days = body.school_hours.weekdays
        row.school_start_time = body.school_hours.start
        row.school_end_time = body.school_hours.end
        row.timezone = body.school_hours.timezone
        after = _hours_snapshot(row)
        if after != before:
            previous["school_hours"] = before
            updated["school_hours"] = after
    if body.sensitivity is not None and body.sensitivity != row.sensitivity:
        previous["sensitivity"] = row.sensitivity
        updated["sensitivity"] = body.sensitivity
        row.sensitivity = body.sensitivity

    if updated:
        row.updated_at = func.now()
        await record_audit_event(
            db,
            request,
            AuditAction.UPDATE_SCHOOL_SETTINGS,
            AuditResource.SCHOOL_SETTINGS,
            AuditStatus.SUCCESS,
            actor=current_user,
            resource_id=target,
            target="School settings",
            description="Administrator updated school settings.",
            metadata={
                "changed_fields": sorted(updated),
                "previous_values": previous,
                "new_values": updated,
            },
            school_id=target,
        )
    await db.commit()
    await db.refresh(row)
    return settings_out(row)


async def _terms_out(db: AsyncSession, target: UUID) -> SchoolTermsOut:
    disabled = await disabled_term_ids(db, target)
    global_rows = (
        await db.execute(
            select(SlurEntry).order_by(SlurEntry.severity_weight.desc(), SlurEntry.term_id)
        )
    ).scalars().all()
    return SchoolTermsOut(
        school_id=target,
        global_terms=[
            GlobalTermForSchool(
                term_id=row.term_id,
                text=row.slur_text,
                language=row.language,
                severity_weight=row.severity_weight,
                enabled_for_school=row.term_id not in disabled,
            )
            for row in global_rows
        ],
        school_terms=[
            SchoolTermOut.model_validate(row) for row in await list_school_terms(db, target)
        ],
    )


@router.get("/terms", response_model=SchoolTermsOut)
async def read_school_terms(
    school_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    target = await resolve_settings_school(db, current_user, school_id)
    return await _terms_out(db, target)


@router.post("/terms", response_model=SchoolTermOut, status_code=201)
async def add_school_term(
    request: Request,
    body: SchoolTermCreate,
    school_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    target = await resolve_settings_school(db, current_user, school_id)
    normalized = normalize_term(body.text)
    global_texts = (await db.execute(select(SlurEntry.slur_text))).scalars().all()
    if any(normalize_term(text) == normalized for text in global_texts):
        raise HTTPException(status_code=409, detail="This word is already on the global list")
    school_texts = (
        await db.execute(select(SchoolTerm.term_text).where(SchoolTerm.school_id == target))
    ).scalars().all()
    if any(normalize_term(text) == normalized for text in school_texts):
        raise HTTPException(status_code=409, detail="Your school already monitors this word")

    term = SchoolTerm(
        school_id=target,
        term_text=" ".join(body.text.split()),
        language=body.language.value,
        severity_weight=body.severity_weight,
    )
    db.add(term)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Your school already monitors this word") from exc
    await record_audit_event(
        db,
        request,
        AuditAction.ADD_SCHOOL_TERM,
        AuditResource.SCHOOL_SETTINGS,
        AuditStatus.SUCCESS,
        actor=current_user,
        resource_id=term.id,
        target=term.term_text,
        description="Administrator added a monitored word for the school.",
        metadata={"language": term.language, "severity_weight": term.severity_weight},
        school_id=target,
    )
    await db.commit()
    await db.refresh(term)
    return SchoolTermOut.model_validate(term)


@router.delete("/terms/{term_id}", status_code=204)
async def delete_school_term(
    request: Request,
    term_id: int,
    school_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    target = await resolve_settings_school(db, current_user, school_id)
    term = await db.get(SchoolTerm, term_id)
    # Another school's term is reported as missing, never as forbidden.
    if term is None or term.school_id != target:
        raise HTTPException(status_code=404, detail="Word not found")
    text, language = term.term_text, term.language
    await db.delete(term)
    await record_audit_event(
        db,
        request,
        AuditAction.DELETE_SCHOOL_TERM,
        AuditResource.SCHOOL_SETTINGS,
        AuditStatus.SUCCESS,
        actor=current_user,
        resource_id=term_id,
        target=text,
        description="Administrator removed a monitored word for the school.",
        metadata={"language": language},
        school_id=target,
    )
    await db.commit()


@router.put("/global-terms/{term_id}", response_model=GlobalTermForSchool)
async def toggle_global_term(
    request: Request,
    term_id: int,
    body: GlobalTermToggle,
    school_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    target = await resolve_settings_school(db, current_user, school_id)
    entry = await db.get(SlurEntry, term_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Word not found")
    disabled = term_id in await disabled_term_ids(db, target)

    if body.enabled and disabled:
        await db.execute(
            delete(SchoolDisabledTerm).where(
                SchoolDisabledTerm.school_id == target, SchoolDisabledTerm.term_id == term_id
            )
        )
        action, description = AuditAction.ENABLE_GLOBAL_TERM, "turned a global word back on"
    elif not body.enabled and not disabled:
        db.add(SchoolDisabledTerm(school_id=target, term_id=term_id))
        action, description = AuditAction.DISABLE_GLOBAL_TERM, "turned off a global word"
    else:
        action = None

    if action is not None:
        await record_audit_event(
            db,
            request,
            action,
            AuditResource.SCHOOL_SETTINGS,
            AuditStatus.SUCCESS,
            actor=current_user,
            resource_id=term_id,
            target=entry.slur_text,
            description=f"Administrator {description} for the school.",
            metadata={"language": entry.language},
            school_id=target,
        )
        await db.commit()
    return GlobalTermForSchool(
        term_id=entry.term_id,
        text=entry.slur_text,
        language=entry.language,
        severity_weight=entry.severity_weight,
        enabled_for_school=body.enabled,
    )
