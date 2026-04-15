from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.database import get_db
from app.models.alert import Alert
from app.schemas.alert import AlertResponse
from typing import List

router = APIRouter(prefix="/logs", tags=["Logs"])

@router.get("/", response_model=List[AlertResponse])
async def get_logs(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Alert).order_by(Alert.created_at.desc()).limit(100)
    )
    logs = result.scalars().all()
    return logs

@router.get("/stats")
async def get_stats(db: AsyncSession = Depends(get_db)):
    total = await db.execute(select(func.count(Alert.id)))
    high = await db.execute(
        select(func.count(Alert.id)).where(Alert.severity == "high")
    )
    medium = await db.execute(
        select(func.count(Alert.id)).where(Alert.severity == "medium")
    )
    low = await db.execute(
        select(func.count(Alert.id)).where(Alert.severity == "low")
    )
    return {
        "total_alerts": total.scalar(),
        "high_severity": high.scalar(),
        "medium_severity": medium.scalar(),
        "low_severity": low.scalar()
    }