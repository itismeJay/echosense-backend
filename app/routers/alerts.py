import asyncio
import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from app.database import get_db
from app.models.alert import Alert
from app.models.user import User
from app.schemas.alert import AlertCreate, AlertResponse, AlertAnalyticsResponse
from app.notifications.push import send_expo_pushes
from typing import List, Optional

router = APIRouter(prefix="/alerts", tags=["Alerts"])


def hydrate_alert(alert: Alert) -> Alert:
    """Decode JSON-string columns back into Python lists for the response model."""
    alert.detected_words = json.loads(alert.detected_words or "[]")
    alert.waveform_snapshot = json.loads(alert.waveform_snapshot or "[]")
    alert.categories = json.loads(alert.categories or "[]")
    alert.hard_hits = json.loads(alert.hard_hits or "[]")
    alert.soft_hits = json.loads(alert.soft_hits or "[]")
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
        categories=json.dumps(alert.categories or []),
        language=alert.language,
        hard_hits=json.dumps(alert.hard_hits or []),
        soft_hits=json.dumps(alert.soft_hits or []),
    )
    db.add(new_alert)
    await db.commit()
    await db.refresh(new_alert)

    token_result = await db.execute(select(User).where(User.push_token.isnot(None)))
    tokens = [u.push_token for u in token_result.scalars().all()]
    asyncio.create_task(send_expo_pushes(tokens, new_alert.id, new_alert.severity, new_alert.location))

    return hydrate_alert(new_alert)


@router.get("/analytics/categories", response_model=AlertAnalyticsResponse)
async def get_category_analytics(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Alert))
    alerts = result.scalars().all()

    by_category: dict = {}
    by_language: dict = {}
    by_severity: dict = {}

    for alert in alerts:
        by_severity[alert.severity] = by_severity.get(alert.severity, 0) + 1

        if alert.language:
            by_language[alert.language] = by_language.get(alert.language, 0) + 1

        try:
            cats = json.loads(alert.categories or "[]")
        except (json.JSONDecodeError, TypeError):
            cats = []
        for cat in cats:
            by_category[cat] = by_category.get(cat, 0) + 1

    return AlertAnalyticsResponse(
        total_alerts=len(alerts),
        by_category=by_category,
        by_language=by_language,
        by_severity=by_severity,
    )


@router.get("/", response_model=List[AlertResponse])
async def get_alerts(
    severity: Optional[str] = None,
    category: Optional[str] = None,
    language: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(Alert).order_by(Alert.created_at.desc())
    if severity:
        query = query.where(Alert.severity == severity)
    if language:
        query = query.where(Alert.language == language)
    if category:
        # Use PostgreSQL JSONB containment to filter alerts that include the category
        query = query.where(
            text("categories::jsonb @> :cat").bindparams(cat=json.dumps([category]))
        )
    result = await db.execute(query)
    return [hydrate_alert(a) for a in result.scalars().all()]


@router.get("/{alert_id}", response_model=AlertResponse)
async def get_alert(alert_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Alert).where(Alert.id == alert_id))
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return hydrate_alert(alert)
