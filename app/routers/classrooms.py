from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.classroom import Classroom
from app.models.school import School
from app.models.user import User
from app.routers.auth import require_admin
from app.schemas.classroom import ClassroomCreate, ClassroomResponse, ClassroomUpdate
from app.services.audit import (
    AuditAction,
    AuditResource,
    AuditStatus,
    record_audit_event,
)
from app.services.school_access import is_global_admin, require_school_access

router = APIRouter(prefix="/classrooms", tags=["Classrooms"])


def _classroom_response(classroom: Classroom) -> ClassroomResponse:
    return ClassroomResponse(
        id=classroom.id,
        school_id=classroom.school_id,
        school_name=classroom.school.name,
        name=classroom.name,
        is_active=classroom.is_active,
        created_at=classroom.created_at,
        updated_at=classroom.updated_at,
        devices=[
            {
                "id": device.id,
                "device_code": device.device_code,
                "display_name": device.display_name,
                "is_active": device.is_active,
            }
            for device in sorted(classroom.devices, key=lambda item: item.device_code)
        ],
    )


async def _get_classroom(
    classroom_id: UUID,
    db: AsyncSession,
    current_user: User,
    *,
    for_update: bool = False,
) -> Classroom:
    query = (
        select(Classroom)
        .where(Classroom.id == classroom_id)
        .options(selectinload(Classroom.devices))
    )
    if for_update:
        query = query.with_for_update(of=Classroom)
    classroom = (await db.execute(query)).scalar_one_or_none()
    if classroom is None:
        raise HTTPException(status_code=404, detail="Classroom not found")
    require_school_access(current_user, classroom.school_id)
    return classroom


@router.post("", response_model=ClassroomResponse, status_code=201)
async def create_classroom(
    request: Request,
    body: ClassroomCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    require_school_access(current_user, body.school_id)
    school = await db.get(School, body.school_id)
    if school is None:
        raise HTTPException(status_code=404, detail="School not found")
    if not school.is_active:
        raise HTTPException(status_code=409, detail="School is inactive")

    classroom = Classroom(school_id=school.id, name=body.name)
    db.add(classroom)
    try:
        await db.flush()
        await record_audit_event(
            db,
            request,
            AuditAction.CREATE_CLASSROOM,
            AuditResource.CLASSROOM,
            AuditStatus.SUCCESS,
            actor=current_user,
            resource_id=classroom.id,
            target=classroom.name,
            description="Administrator created a classroom.",
            metadata={"school_id": school.id},
            school_id=school.id,
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Classroom name already exists in this school",
        ) from exc
    return _classroom_response(await _get_classroom(classroom.id, db, current_user))


@router.get("", response_model=list[ClassroomResponse])
async def list_classrooms(
    school_id: UUID | None = None,
    is_active: bool | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    query = select(Classroom).options(selectinload(Classroom.devices)).order_by(Classroom.name)
    if is_global_admin(current_user):
        if school_id is not None:
            query = query.where(Classroom.school_id == school_id)
    else:
        if current_user.school_id is None:
            raise HTTPException(status_code=403, detail="Administrator is not assigned to a school")
        if school_id is not None and school_id != current_user.school_id:
            raise HTTPException(status_code=403, detail="School access denied")
        query = query.where(Classroom.school_id == current_user.school_id)
    if is_active is not None:
        query = query.where(Classroom.is_active == is_active)
    classrooms = list((await db.execute(query)).scalars().unique().all())
    return [_classroom_response(classroom) for classroom in classrooms]


@router.get("/{classroom_id}", response_model=ClassroomResponse)
async def read_classroom(
    classroom_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    return _classroom_response(await _get_classroom(classroom_id, db, current_user))


@router.patch("/{classroom_id}", response_model=ClassroomResponse)
async def update_classroom(
    request: Request,
    classroom_id: UUID,
    body: ClassroomUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    classroom = await _get_classroom(classroom_id, db, current_user, for_update=True)
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(classroom, field, value)
    classroom.updated_at = datetime.now(timezone.utc)
    await record_audit_event(
        db,
        request,
        AuditAction.UPDATE_CLASSROOM,
        AuditResource.CLASSROOM,
        AuditStatus.SUCCESS,
        actor=current_user,
        resource_id=classroom.id,
        target=classroom.name,
        description="Administrator updated a classroom.",
        metadata={"changed_fields": sorted(changes)},
        school_id=classroom.school_id,
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Classroom name already exists in this school",
        ) from exc
    return _classroom_response(await _get_classroom(classroom.id, db, current_user))
