from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.system_settings import SystemSettings
from app.models.user import User
from app.schemas.system_settings import SystemSettingsOut, SystemSettingsUpdate
from app.routers.auth import require_admin
from app.routers.audit_logs import log_action

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
    body: SystemSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    row = await _get_row(db)
    if not row:
        raise HTTPException(status_code=404, detail="Settings not found")
    if body.confidence_threshold is not None:
        row.confidence_threshold = body.confidence_threshold
    if body.aggression_duration_threshold is not None:
        row.aggression_duration_threshold = body.aggression_duration_threshold
    row.updated_at = datetime.now(timezone.utc)
    await log_action(db, current_user, "Updated System Settings", "System Settings")
    await db.commit()
    await db.refresh(row)
    return row


@router.post("/heartbeat")
async def heartbeat(db: AsyncSession = Depends(get_db)):
    row = await _get_row(db)
    if row:
        row.device_status = "online"
        row.last_heartbeat = datetime.now(timezone.utc)
        await db.commit()
    return {"message": "Heartbeat received"}


@router.post("/ota-push")
async def ota_push(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    row = await _get_row(db)
    if row:
        row.last_ota_update = datetime.now(timezone.utc)
        await log_action(db, current_user, "OTA Push Triggered", "System Settings")
        await db.commit()
    return {"message": "OTA push triggered"}
