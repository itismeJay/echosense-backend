from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.user import User
from app.schemas.audit_log import AuditLogOut
from app.routers.auth import require_admin

router = APIRouter(prefix="/audit-logs", tags=["Audit Logs"])


async def log_action(
    db: AsyncSession,
    user: User,
    action: str,
    module: str,
    target: Optional[str] = None,
):
    log = AuditLog(
        user_id=user.id,
        actor_email=user.email,
        action=action,
        module=module,
        target=target,
    )
    db.add(log)
    await db.flush()


@router.get("/", response_model=List[AuditLogOut])
async def get_audit_logs(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin),
):
    result = await db.execute(
        select(AuditLog).order_by(AuditLog.performed_at.desc()).limit(100)
    )
    return result.scalars().all()
