import secrets
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.database import get_db
from app.models.classroom import Classroom
from app.models.edge_device import EdgeDevice
from app.models.school import School
from app.models.user import User
from app.routers.auth import require_admin
from app.schemas.edge_device import (
    DEVICE_KEY_WARNING,
    DeviceAssignmentRequest,
    EdgeDeviceCreate,
    EdgeDeviceKeyResponse,
    EdgeDeviceResponse,
    EdgeDeviceUpdate,
)
from app.services.audit import (
    AuditAction,
    AuditResource,
    AuditStatus,
    record_audit_event,
)
from app.services.device_auth import hash_device_key
from app.services.school_access import is_global_admin, require_school_access

router = APIRouter(prefix="/devices", tags=["Devices"])


def _generate_device_key() -> str:
    return f"edk_{secrets.token_urlsafe(32)}"


def _device_response(device: EdgeDevice) -> EdgeDeviceResponse:
    return EdgeDeviceResponse.model_validate(device)


async def _get_device(
    device_id: UUID,
    db: AsyncSession,
    current_user: User,
    *,
    for_update: bool = False,
) -> EdgeDevice:
    query = (
        select(EdgeDevice)
        .where(EdgeDevice.id == device_id)
        .options(joinedload(EdgeDevice.classroom), joinedload(EdgeDevice.school))
    )
    if for_update:
        query = query.with_for_update(of=EdgeDevice)
    device = (await db.execute(query)).scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    if device.school_id is None:
        if not is_global_admin(current_user):
            raise HTTPException(status_code=403, detail="School access denied")
    else:
        require_school_access(current_user, device.school_id)
    return device


async def _get_assignable_classroom(
    classroom_id: UUID,
    db: AsyncSession,
    current_user: User,
) -> Classroom:
    classroom = await db.get(Classroom, classroom_id)
    if classroom is None:
        raise HTTPException(status_code=404, detail="Classroom not found")
    require_school_access(current_user, classroom.school_id)
    if not classroom.is_active or not classroom.school.is_active:
        raise HTTPException(status_code=409, detail="Classroom is inactive")
    return classroom


async def _find_legacy_classroom(
    school_name: str,
    classroom_name: str,
    db: AsyncSession,
    current_user: User,
) -> Classroom:
    query = (
        select(Classroom)
        .join(School)
        .where(
            func.lower(func.btrim(School.name)) == school_name.casefold(),
            func.lower(func.btrim(Classroom.name)) == classroom_name.casefold(),
        )
    )
    classroom = (await db.execute(query)).scalar_one_or_none()
    if classroom is None:
        raise HTTPException(status_code=404, detail="Classroom not found")
    return await _get_assignable_classroom(classroom.id, db, current_user)


def _assign_device(device: EdgeDevice, classroom: Classroom) -> None:
    now = datetime.now(timezone.utc)
    device.school_id = classroom.school_id
    device.classroom_id = classroom.id
    device.legacy_school_name = classroom.school.name
    device.legacy_classroom_name = classroom.name
    device.assigned_at = now
    device.updated_at = now


async def _registration_assignment(
    body: EdgeDeviceCreate,
    db: AsyncSession,
    current_user: User,
) -> tuple[School, Classroom | None]:
    classroom = None
    if body.classroom_id is not None:
        classroom = await _get_assignable_classroom(body.classroom_id, db, current_user)
        if body.school_id is not None and body.school_id != classroom.school_id:
            raise HTTPException(status_code=409, detail="Classroom does not belong to school")
        return classroom.school, classroom
    if body.classroom_name is not None and body.school_name is not None:
        classroom = await _find_legacy_classroom(
            body.school_name,
            body.classroom_name,
            db,
            current_user,
        )
        return classroom.school, classroom

    school_id = body.school_id or current_user.school_id
    if school_id is None:
        raise HTTPException(
            status_code=422, detail="school_id is required for an unassigned device"
        )
    require_school_access(current_user, school_id)
    school = await db.get(School, school_id)
    if school is None:
        raise HTTPException(status_code=404, detail="School not found")
    if not school.is_active:
        raise HTTPException(status_code=409, detail="School is inactive")
    return school, None


@router.post("", response_model=EdgeDeviceKeyResponse, status_code=201)
async def create_device(
    request: Request,
    body: EdgeDeviceCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    existing = await db.scalar(
        select(EdgeDevice.id).where(EdgeDevice.device_code == body.device_code)
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="Device code already registered")

    school, classroom = await _registration_assignment(body, db, current_user)
    device_key = _generate_device_key()
    device = EdgeDevice(
        device_code=body.device_code,
        display_name=body.display_name,
        school_id=school.id,
        classroom_id=classroom.id if classroom is not None else None,
        legacy_classroom_name=classroom.name if classroom is not None else None,
        legacy_school_name=school.name,
        assigned_at=datetime.now(timezone.utc) if classroom is not None else None,
        api_key_hash=hash_device_key(device_key),
    )
    db.add(device)
    try:
        await db.flush()
        await record_audit_event(
            db,
            request,
            AuditAction.REGISTER_EDGE_DEVICE,
            AuditResource.EDGE_DEVICE,
            AuditStatus.SUCCESS,
            actor=current_user,
            resource_id=device.id,
            target=device.device_code,
            description="Administrator registered an edge device.",
            metadata={"school_id": school.id, "classroom_id": device.classroom_id},
            school_id=school.id,
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Device code already registered") from exc
    device = await _get_device(device.id, db, current_user)
    return EdgeDeviceKeyResponse(
        device=_device_response(device),
        device_key=device_key,
        warning=DEVICE_KEY_WARNING,
    )


@router.get("", response_model=list[EdgeDeviceResponse])
async def list_devices(
    school_id: UUID | None = None,
    classroom_id: UUID | None = None,
    is_active: bool | None = Query(default=None),
    unassigned: bool | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    query = select(EdgeDevice).options(
        joinedload(EdgeDevice.classroom), joinedload(EdgeDevice.school)
    )
    if is_global_admin(current_user):
        if school_id is not None:
            query = query.where(EdgeDevice.school_id == school_id)
    else:
        if current_user.school_id is None:
            raise HTTPException(status_code=403, detail="Administrator is not assigned to a school")
        if school_id is not None and school_id != current_user.school_id:
            raise HTTPException(status_code=403, detail="School access denied")
        query = query.where(EdgeDevice.school_id == current_user.school_id)
    if classroom_id is not None:
        query = query.where(EdgeDevice.classroom_id == classroom_id)
    if is_active is not None:
        query = query.where(EdgeDevice.is_active == is_active)
    if unassigned is True:
        query = query.where(EdgeDevice.classroom_id.is_(None))
    elif unassigned is False:
        query = query.where(EdgeDevice.classroom_id.is_not(None))
    devices = list((await db.execute(query.order_by(EdgeDevice.device_code))).scalars().all())
    return [_device_response(device) for device in devices]


@router.get("/{device_id}", response_model=EdgeDeviceResponse)
async def read_device(
    device_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    return _device_response(await _get_device(device_id, db, current_user))


@router.patch("/{device_id}", response_model=EdgeDeviceResponse)
async def update_device(
    request: Request,
    device_id: UUID,
    body: EdgeDeviceUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    device = await _get_device(device_id, db, current_user, for_update=True)
    changes = body.model_dump(exclude_unset=True)
    if body.classroom_name is not None and body.school_name is not None:
        classroom = await _find_legacy_classroom(
            body.school_name,
            body.classroom_name,
            db,
            current_user,
        )
        _assign_device(device, classroom)
    if body.display_name is not None:
        device.display_name = body.display_name
    if body.is_active is not None:
        device.is_active = body.is_active
    device.updated_at = datetime.now(timezone.utc)
    await record_audit_event(
        db,
        request,
        AuditAction.UPDATE_EDGE_DEVICE,
        AuditResource.EDGE_DEVICE,
        AuditStatus.SUCCESS,
        actor=current_user,
        resource_id=device.id,
        target=device.device_code,
        description="Administrator updated an edge device.",
        metadata={"changed_fields": sorted(changes)},
        school_id=device.school_id,
    )
    await db.commit()
    return _device_response(await _get_device(device.id, db, current_user))


async def _change_assignment(
    request: Request,
    device_id: UUID,
    body: DeviceAssignmentRequest,
    db: AsyncSession,
    current_user: User,
) -> EdgeDeviceResponse:
    device = await _get_device(device_id, db, current_user, for_update=True)
    if (
        body.expected_current_classroom_id is not None
        and body.expected_current_classroom_id != device.classroom_id
    ):
        raise HTTPException(status_code=409, detail="Device assignment changed; refresh and retry")
    classroom = await _get_assignable_classroom(body.classroom_id, db, current_user)
    if device.school_id != classroom.school_id and not is_global_admin(current_user):
        raise HTTPException(status_code=403, detail="Cross-school assignment denied")
    previous_classroom_id = device.classroom_id
    _assign_device(device, classroom)
    await record_audit_event(
        db,
        request,
        AuditAction.ASSIGN_EDGE_DEVICE,
        AuditResource.EDGE_DEVICE,
        AuditStatus.SUCCESS,
        actor=current_user,
        resource_id=device.id,
        target=device.device_code,
        description="Administrator changed an edge device classroom assignment.",
        metadata={
            "previous_classroom_id": previous_classroom_id,
            "classroom_id": classroom.id,
            "school_id": classroom.school_id,
        },
        school_id=classroom.school_id,
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Device assignment conflict") from exc
    return _device_response(await _get_device(device.id, db, current_user))


@router.post("/{device_id}/assign", response_model=EdgeDeviceResponse)
async def assign_device(
    request: Request,
    device_id: UUID,
    body: DeviceAssignmentRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    return await _change_assignment(request, device_id, body, db, current_user)


@router.post("/{device_id}/unassign", response_model=EdgeDeviceResponse)
async def unassign_device(
    request: Request,
    device_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    device = await _get_device(device_id, db, current_user, for_update=True)
    previous_classroom_id = device.classroom_id
    device.classroom_id = None
    device.legacy_classroom_name = None
    device.assigned_at = None
    device.updated_at = datetime.now(timezone.utc)
    await record_audit_event(
        db,
        request,
        AuditAction.UNASSIGN_EDGE_DEVICE,
        AuditResource.EDGE_DEVICE,
        AuditStatus.SUCCESS,
        actor=current_user,
        resource_id=device.id,
        target=device.device_code,
        description="Administrator unassigned an edge device.",
        metadata={"previous_classroom_id": previous_classroom_id},
        school_id=device.school_id,
    )
    await db.commit()
    return _device_response(await _get_device(device.id, db, current_user))


async def _set_device_active(
    request: Request,
    device_id: UUID,
    active: bool,
    db: AsyncSession,
    current_user: User,
) -> EdgeDeviceResponse:
    device = await _get_device(device_id, db, current_user, for_update=True)
    device.is_active = active
    device.updated_at = datetime.now(timezone.utc)
    await record_audit_event(
        db,
        request,
        AuditAction.ENABLE_EDGE_DEVICE if active else AuditAction.DISABLE_EDGE_DEVICE,
        AuditResource.EDGE_DEVICE,
        AuditStatus.SUCCESS,
        actor=current_user,
        resource_id=device.id,
        target=device.device_code,
        description=f"Administrator {'enabled' if active else 'disabled'} an edge device.",
        school_id=device.school_id,
    )
    await db.commit()
    return _device_response(await _get_device(device.id, db, current_user))


@router.post("/{device_id}/disable", response_model=EdgeDeviceResponse)
async def disable_device(
    request: Request,
    device_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    return await _set_device_active(request, device_id, False, db, current_user)


@router.post("/{device_id}/enable", response_model=EdgeDeviceResponse)
async def enable_device(
    request: Request,
    device_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    return await _set_device_active(request, device_id, True, db, current_user)


@router.post("/{device_id}/rotate-key", response_model=EdgeDeviceKeyResponse)
async def rotate_device_key(
    request: Request,
    device_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    device = await _get_device(device_id, db, current_user, for_update=True)
    device_key = _generate_device_key()
    now = datetime.now(timezone.utc)
    device.api_key_hash = hash_device_key(device_key)
    device.key_rotated_at = now
    device.updated_at = now
    await record_audit_event(
        db,
        request,
        AuditAction.ROTATE_EDGE_DEVICE_KEY,
        AuditResource.EDGE_DEVICE,
        AuditStatus.SUCCESS,
        actor=current_user,
        resource_id=device.id,
        target=device.device_code,
        description="Administrator rotated an edge device key.",
        school_id=device.school_id,
    )
    await db.commit()
    device = await _get_device(device.id, db, current_user)
    return EdgeDeviceKeyResponse(
        device=_device_response(device),
        device_key=device_key,
        warning=DEVICE_KEY_WARNING,
    )
