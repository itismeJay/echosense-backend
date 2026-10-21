from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.school import School
from app.models.user import User
from app.routers.auth import require_super_admin
from app.schemas.school import SchoolCreate, SchoolResponse, SchoolUpdate
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
