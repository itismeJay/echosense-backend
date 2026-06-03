from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, cast, Date
from typing import List
from app.database import get_db
from app.models.report import Report
from app.models.alert import Alert
from app.models.user import User
from app.schemas.report import ReportCreate, ReportOut
from app.routers.auth import get_current_user

router = APIRouter(prefix="/reports", tags=["Reports"])


async def require_admin_or_counselor(
    current_user: User = Depends(get_current_user),
) -> User:
    if current_user.role not in ("admin", "counselor"):
        raise HTTPException(status_code=403, detail="Admin or counselor access required")
    return current_user


@router.get("/", response_model=List[ReportOut])
async def get_reports(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin_or_counselor),
):
    result = await db.execute(select(Report).order_by(Report.generated_at.desc()))
    return result.scalars().all()


@router.post("/", response_model=ReportOut, status_code=201)
async def generate_report(
    body: ReportCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin_or_counselor),
):
    count_result = await db.execute(
        select(func.count(Alert.id)).where(
            cast(Alert.created_at, Date) >= body.date_from,
            cast(Alert.created_at, Date) <= body.date_to,
        )
    )
    total = count_result.scalar() or 0

    report = Report(
        generated_by=current_user.id,
        generated_by_email=current_user.email,
        date_from=body.date_from,
        date_to=body.date_to,
        total_incidents=total,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return report
