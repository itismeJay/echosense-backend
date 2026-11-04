from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.system_settings import SystemSettings
from app.models.user import User
from app.schemas.system_settings import SystemSettingsOut
from app.routers.auth import require_super_admin
from app.models.edge_device import EdgeDevice
from app.services.device_auth import edge_device_or_transition
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


@router.get("", response_model=SystemSettingsOut, include_in_schema=False)
@router.get("/", response_model=SystemSettingsOut)
async def get_settings(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_super_admin),
):
    """Read-only engine information. The confidence/duration sliders were removed: the
    edge never read them. Per-school behavior lives in /school-settings."""
    row = await _get_row(db)
    if not row:
        raise HTTPException(status_code=404, detail="Settings not found")
    return row


@router.post("/heartbeat")
async def post_heartbeat(
    db: AsyncSession = Depends(get_db),
    device: EdgeDevice | None = Depends(edge_device_or_transition),
):
    # Device credentials are required (see edge_device_or_transition); authenticating
    # stamps the device's last_seen_at, which the per-device Online/Offline status reads.
    now = datetime.now(timezone.utc)
    row = await _get_row(db)
    if row:
        row.device_status = "online"
        row.last_heartbeat = now.replace(tzinfo=None)  # TIMESTAMP col requires naive datetime
    await db.commit()
    return {
        "status": "ok",
        "timestamp": now.isoformat(),
        "device_recognized": device is not None,
    }


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
    current_user: User = Depends(require_super_admin),
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
