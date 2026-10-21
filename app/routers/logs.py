import json
from collections import Counter
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.database import get_db
from app.models.alert import Alert
from app.routers.auth import require_alert_reviewer
from app.schemas.alert import AlertResponse
from app.routers.alerts import hydrate_alert
from app.services.school_access import scope_alert_query
from typing import List

router = APIRouter(prefix="/logs", tags=["Logs"])

# Emotion buckets we always report, even when count is zero.
EMOTION_BUCKETS = ["angry", "aggressive", "distressed", "upset", "neutral", "unknown"]


@router.get("/", response_model=List[AlertResponse])
async def get_logs(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_alert_reviewer),
):
    result = await db.execute(
        scope_alert_query(select(Alert), current_user)
        .order_by(Alert.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return [hydrate_alert(a) for a in result.scalars().all()]


@router.get("/stats")
async def get_stats(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_alert_reviewer),
):
    total = await db.execute(scope_alert_query(select(func.count(Alert.id)), current_user))
    high = await db.execute(
        scope_alert_query(select(func.count(Alert.id)), current_user).where(
            Alert.severity == "HIGH"
        )
    )
    medium = await db.execute(
        scope_alert_query(select(func.count(Alert.id)), current_user).where(
            Alert.severity == "MEDIUM"
        )
    )
    low = await db.execute(
        scope_alert_query(select(func.count(Alert.id)), current_user).where(Alert.severity == "LOW")
    )

    # Emotion breakdown — group counts, mapping NULL/unrecognized into "unknown".
    emotion_breakdown = {bucket: 0 for bucket in EMOTION_BUCKETS}
    emotion_rows = await db.execute(
        scope_alert_query(select(Alert.emotion, func.count(Alert.id)), current_user).group_by(
            Alert.emotion
        )
    )
    for emotion, count in emotion_rows.all():
        key = emotion if emotion in emotion_breakdown else "unknown"
        emotion_breakdown[key] += count

    # Top detected words — flatten every alert's JSON word list and count.
    words_rows = await db.execute(
        scope_alert_query(select(Alert.detected_words), current_user).where(
            Alert.detected_words.is_not(None)
        )
    )
    word_counter: Counter = Counter()
    for (raw,) in words_rows.all():
        try:
            word_counter.update(json.loads(raw or "[]"))
        except (json.JSONDecodeError, TypeError):
            continue
    top_detected_words = [word for word, _ in word_counter.most_common(10)]

    avg = await db.execute(scope_alert_query(select(func.avg(Alert.confidence)), current_user))
    average_confidence = round(float(avg.scalar() or 0.0), 4)

    return {
        "total_alerts": total.scalar(),
        "high_severity": high.scalar(),
        "medium_severity": medium.scalar(),
        "low_severity": low.scalar(),
        "emotion_breakdown": emotion_breakdown,
        "top_detected_words": top_detected_words,
        "average_confidence": average_confidence,
    }
