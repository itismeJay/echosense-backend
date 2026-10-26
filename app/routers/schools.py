from datetime import datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.alert import Alert
from app.models.edge_device import EdgeDevice
from app.models.school import School
from app.models.user import User
from app.routers.auth import require_super_admin
from app.schemas.school import (
    SchoolCreate,
    SchoolResponse,
    SchoolSummary,
    SchoolUpdate,
    SchoolUserCounts,
)
from app.services.device_status import is_device_online
from app.services.audit import (
    AuditAction,
    AuditResource,
    AuditStatus,
    record_audit_event,
)

router = APIRouter(prefix="/schools", tags=["Schools"])

DUPLICATE_SCHOOL_DETAIL = "School name already exists"


async def _get_school(school_id: UUID, db: AsyncSession, *, for_update: bool = False) -> School:
    query = select(School).where(School.id == school_id)
    if for_update:
        query = query.with_for_update()
    school = (await db.execute(query)).scalar_one_or_none()
    if school is None:
        raise HTTPException(status_code=404, detail="School not found")
    return school


@router.post("/", response_model=SchoolResponse, status_code=201, include_in_schema=False)
@router.post("", response_model=SchoolResponse, status_code=201)
async def create_school(
    request: Request,
    body: SchoolCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    school = School(name=body.name)
    db.add(school)
    try:
        await db.flush()
        await record_audit_event(
            db,
            request,
            AuditAction.CREATE_SCHOOL,
            AuditResource.SCHOOL,
            AuditStatus.SUCCESS,
            actor=current_user,
            resource_id=school.id,
            target=school.name,
            description="Super administrator created a school.",
            school_id=school.id,
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=DUPLICATE_SCHOOL_DETAIL) from exc
    await db.refresh(school)
    return school


@router.get("/", response_model=list[SchoolResponse], include_in_schema=False)
@router.get("", response_model=list[SchoolResponse])
async def list_schools(
    is_active: bool | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    query = select(School).order_by(School.name)
    if is_active is not None:
        query = query.where(School.is_active == is_active)
    return list((await db.execute(query)).scalars().all())


def _period_starts(tz_name: str) -> tuple[datetime, datetime]:
    """Midnight today and midnight on Monday in the viewer's time zone, as UTC."""

    try:
        zone = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Unknown time zone") from exc
    local_now = datetime.now(zone)
    today = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    week = today - timedelta(days=today.weekday())
    return today.astimezone(timezone.utc), week.astimezone(timezone.utc)


@router.get("/summary", response_model=list[SchoolSummary])
async def school_summaries(
    tz: str = Query(default="UTC", max_length=64),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    """Per-school counts for the All schools cards (super admins only)."""

    today_start, week_start = _period_starts(tz)
    schools = list((await db.execute(select(School).order_by(School.name))).scalars().all())

    alert_rows = await db.execute(
        select(
            Alert.school_id,
            func.count().filter(Alert.status == "active"),
            func.count().filter(Alert.severity == "HIGH", Alert.created_at >= today_start),
            func.count().filter(Alert.created_at >= week_start),
        ).group_by(Alert.school_id)
    )
    alerts = {row[0]: row[1:] for row in alert_rows.all()}

    device_rows = await db.execute(
        select(EdgeDevice.school_id, EdgeDevice.is_active, EdgeDevice.last_seen_at)
    )
    now = datetime.now(timezone.utc)
    devices: dict = {}
    for school_id, is_active, last_seen_at in device_rows.all():
        online, total = devices.get(school_id, (0, 0))
        devices[school_id] = (
            online + int(bool(is_active) and is_device_online(last_seen_at, now)),
            total + 1,
        )

    user_rows = await db.execute(
        select(User.school_id, User.role, func.count()).group_by(User.school_id, User.role)
    )
    users: dict = {}
    for school_id, role, count in user_rows.all():
        users.setdefault(school_id, {})[role] = count

    return [
        SchoolSummary(
            id=school.id,
            name=school.name,
            is_active=school.is_active,
            created_at=school.created_at,
            alerts_needing_review=alerts.get(school.id, (0, 0, 0))[0],
            high_today=alerts.get(school.id, (0, 0, 0))[1],
            alerts_this_week=alerts.get(school.id, (0, 0, 0))[2],
            devices_online=devices.get(school.id, (0, 0))[0],
            devices_total=devices.get(school.id, (0, 0))[1],
            users=SchoolUserCounts(
                **{
                    role: count
                    for role, count in users.get(school.id, {}).items()
                    if role in {"admin", "counselor", "staff"}
                }
            ),
        )
        for school in schools
    ]


@router.get("/{school_id}", response_model=SchoolResponse)
async def read_school(
    school_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    return await _get_school(school_id, db)


@router.patch("/{school_id}", response_model=SchoolResponse)
async def update_school(
    request: Request,
    school_id: UUID,
    body: SchoolUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    school = await _get_school(school_id, db, for_update=True)
    changes = body.model_dump(exclude_unset=True)
    previous = {field: getattr(school, field) for field in changes}
    for field, value in changes.items():
        setattr(school, field, value)
    school.updated_at = datetime.now(timezone.utc)
    try:
        # Flush before auditing so a duplicate name surfaces here as 409, not 500.
        await db.flush()
        await record_audit_event(
            db,
            request,
            AuditAction.UPDATE_SCHOOL,
            AuditResource.SCHOOL,
            AuditStatus.SUCCESS,
            actor=current_user,
            resource_id=school.id,
            target=school.name,
            description="Super administrator updated a school.",
            metadata={
                "changed_fields": sorted(changes),
                "previous_values": previous,
                "new_values": changes,
            },
            school_id=school.id,
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=DUPLICATE_SCHOOL_DETAIL) from exc
    await db.refresh(school)
    return school
