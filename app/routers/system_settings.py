from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.system_settings import SystemSettings
from app.models.user import User
from app.schemas.system_settings import SystemSettingsOut, SystemSettingsUpdate
from app.routers.auth import require_admin
from app.services.audit import (
    AuditAction,
    AuditResource,
    AuditStatus,
    record_audit_event,
)

router = APIRouter(prefix="/system-settings", tags=["System Settings"])


async def _get_row(db: AsyncSession) -> SystemSettings:
    result = await db.execute(select(SystemSettings))
    return result.scalar_one_or_none()


@router.get("/", response_model=SystemSettingsOut)
async def get_settings(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin),
):
    row = await _get_row(db)
    if not row:
        raise HTTPException(status_code=404, detail="Settings not found")
    return row


@router.put("/", response_model=SystemSettingsOut)
async def update_settings(
    request: Request,
    body: SystemSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    row = await _get_row(db)
    if not row:
        raise HTTPException(status_code=404, detail="Settings not found")

    previous_values = {}
    new_values = {}
    if (
        body.confidence_threshold is not None
        and body.confidence_threshold != row.confidence_threshold
    ):
        previous_values["confidence_threshold"] = row.confidence_threshold
        new_values["confidence_threshold"] = body.confidence_threshold
        row.confidence_threshold = body.confidence_threshold
    if (
        body.aggression_duration_threshold is not None
        and body.aggression_duration_threshold != row.aggression_duration_threshold
    ):
        previous_values["aggression_duration_threshold"] = row.aggression_duration_threshold
        new_values["aggression_duration_threshold"] = body.aggression_duration_threshold
        row.aggression_duration_threshold = body.aggression_duration_threshold

    if not new_values:
        return row

    row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await record_audit_event(
        db,
        request,
        AuditAction.UPDATE_SETTINGS,
        AuditResource.SETTINGS,
        AuditStatus.SUCCESS,
        actor=current_user,
        resource_id=row.setting_id,
        target="Global detection settings",
        description="Administrator updated detection settings.",
        metadata={
            "changed_fields": sorted(new_values),
            "previous_values": previous_values,
            "new_values": new_values,
        },
    )
    await db.commit()
    await db.refresh(row)
    return row


@router.post("/heartbeat")
async def post_heartbeat(db: AsyncSession = Depends(get_db)):
    now = datetime.now(timezone.utc)
    row = await _get_row(db)
    if row:
        row.device_status = "online"
        row.last_heartbeat = now.replace(tzinfo=None)  # TIMESTAMP col requires naive datetime
        await db.commit()
    return {"status": "ok", "timestamp": now.isoformat()}


@router.get("/heartbeat")
async def get_heartbeat(db: AsyncSession = Depends(get_db)):
    row = await _get_row(db)
    if not row:
        return {"device_status": "unknown", "last_heartbeat": None}
    return {
        "device_status": row.device_status,
        "last_heartbeat": row.last_heartbeat.isoformat() if row.last_heartbeat else None,
    }


@router.post("/ota-push")
async def ota_push(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    row = await _get_row(db)
    if row:
        row.last_ota_update = datetime.now(timezone.utc).replace(tzinfo=None)
        await record_audit_event(
            db,
            request,
            AuditAction.TRIGGER_OTA_PUSH,
            AuditResource.SETTINGS,
            AuditStatus.SUCCESS,
            actor=current_user,
            resource_id=row.setting_id,
            target="OTA update request",
            description="Administrator recorded an OTA push request.",
        )
        await db.commit()
    return {"message": "OTA push triggered"}
