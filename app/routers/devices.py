import secrets
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.edge_device import EdgeDevice
from app.models.user import User
from app.routers.auth import require_admin
from app.schemas.edge_device import (
    DEVICE_KEY_WARNING,
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

router = APIRouter(prefix="/devices", tags=["Devices"])


def _generate_device_key() -> str:
    return f"edk_{secrets.token_urlsafe(32)}"


async def _get_device(device_id: UUID, db: AsyncSession) -> EdgeDevice:
    device = await db.get(EdgeDevice, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


@router.post("", response_model=EdgeDeviceKeyResponse, status_code=201)
async def create_device(
    request: Request,
    body: EdgeDeviceCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    result = await db.execute(
        select(EdgeDevice.id).where(EdgeDevice.device_code == body.device_code)
    )
    if result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Device code already registered")

    device_key = _generate_device_key()
    device = EdgeDevice(
        device_code=body.device_code,
        display_name=body.display_name,
        classroom_name=body.classroom_name,
        school_name=body.school_name,
        api_key_hash=hash_device_key(device_key),
    )
    db.add(device)
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
        metadata={
            "classroom_name": device.classroom_name,
            "school_name": device.school_name,
        },
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Device code already registered") from exc
    await db.refresh(device)
    return EdgeDeviceKeyResponse(
        device=device,
        device_key=device_key,
        warning=DEVICE_KEY_WARNING,
    )


@router.get("", response_model=list[EdgeDeviceResponse])
async def list_devices(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(EdgeDevice).order_by(EdgeDevice.device_code))
    return list(result.scalars().all())


@router.get("/{device_id}", response_model=EdgeDeviceResponse)
async def read_device(
    device_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    return await _get_device(device_id, db)


@router.patch("/{device_id}", response_model=EdgeDeviceResponse)
async def update_device(
    request: Request,
    device_id: UUID,
    body: EdgeDeviceUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    device = await _get_device(device_id, db)
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(device, field, value)
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
    )
    await db.commit()
    await db.refresh(device)
    return device


@router.post("/{device_id}/rotate-key", response_model=EdgeDeviceKeyResponse)
async def rotate_device_key(
    request: Request,
    device_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    device = await _get_device(device_id, db)
    device_key = _generate_device_key()
    device.api_key_hash = hash_device_key(device_key)
    device.updated_at = datetime.now(timezone.utc)
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
    )
    await db.commit()
    await db.refresh(device)
    return EdgeDeviceKeyResponse(
        device=device,
        device_key=device_key,
        warning=DEVICE_KEY_WARNING,
    )
