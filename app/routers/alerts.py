import asyncio
import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.alert import Alert
from app.models.user import User
from app.schemas.alert import AlertCreate, AlertResponse
from app.notifications.push import send_expo_pushes
from typing import List, Optional

router = APIRouter(prefix="/alerts", tags=["Alerts"])


def hydrate_alert(alert: Alert) -> Alert:
    """Decode the JSON-string columns back into Python lists so the response
    model validates. Mutates the (already-loaded) ORM instance in place."""
    alert.detected_words = json.loads(alert.detected_words or "[]")
    alert.waveform_snapshot = json.loads(alert.waveform_snapshot or "[]")
    return alert


@router.post("/", response_model=AlertResponse)
async def create_alert(alert: AlertCreate, db: AsyncSession = Depends(get_db)):
    new_alert = Alert(
        severity=alert.severity,
        confidence=alert.confidence,
        duration=alert.duration,
        location=alert.location,
        transcribed_text=alert.transcribed_text,
        detected_words=json.dumps(alert.detected_words or []),
        yamnet_class=alert.yamnet_class,
        yamnet_score=alert.yamnet_score,
        emotion=alert.emotion,
        rms=alert.rms,
        energy_variance=alert.energy_variance,
        zero_crossing_rate=alert.zero_crossing_rate,
        peak_to_average=alert.peak_to_average,
        waveform_snapshot=json.dumps(alert.waveform_snapshot or []),
    )
    db.add(new_alert)
    await db.commit()
    await db.refresh(new_alert)

    token_result = await db.execute(select(User).where(User.push_token.isnot(None)))
    tokens = [u.push_token for u in token_result.scalars().all()]
    asyncio.create_task(send_expo_pushes(tokens, new_alert.id, new_alert.severity, new_alert.location))

    return hydrate_alert(new_alert)

@router.get("/", response_model=List[AlertResponse])
async def get_alerts(severity: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    query = select(Alert).order_by(Alert.created_at.desc())
    if severity:
        query = query.where(Alert.severity == severity)
    result = await db.execute(query)
    return [hydrate_alert(a) for a in result.scalars().all()]

@router.get("/{alert_id}", response_model=AlertResponse)
async def get_alert(alert_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Alert).where(Alert.id == alert_id))
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return hydrate_alert(alert)