import asyncio
import json
from collections import Counter
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.alert import Alert, AlertMatchedTerm
from app.models.slur import SlurEntry
from app.models.user import User
from app.schemas.alert import (
    AlertCreate,
    AlertResponse,
    AlertAnalyticsResponse,
    AlertSummaryResponse,
    PeriodStats,
    TopWord,
    normalize_term,
)
from app.notifications.push import send_expo_pushes
from typing import List, Optional
from sqlalchemy.orm import selectinload

from app.languages import LanguageCode

router = APIRouter(prefix="/alerts", tags=["Alerts"])


def hydrate_alert(alert: Alert) -> Alert:
    """Decode JSON-string columns back into Python lists for the response model."""
    alert.detected_words = json.loads(alert.detected_words or "[]")
    alert.waveform_snapshot = json.loads(alert.waveform_snapshot or "[]")
    alert.categories = json.loads(alert.categories or "[]")
    alert.hard_hits = json.loads(alert.hard_hits or "[]")
    alert.soft_hits = json.loads(alert.soft_hits or "[]")
    return alert


async def resolve_matched_terms(
    requested_terms,
    db: AsyncSession,
) -> list[tuple[SlurEntry, str, str]]:
    if not requested_terms:
        return []

    result = await db.execute(select(SlurEntry))
    dictionary_entries = list(result.scalars().all())
    entries_by_id = {entry.term_id: entry for entry in dictionary_entries}
    entries_by_text: dict[str, list[SlurEntry]] = {}
    for entry in dictionary_entries:
        entries_by_text.setdefault(normalize_term(entry.slur_text), []).append(entry)

    resolved: list[tuple[SlurEntry, str, str]] = []
    resolved_ids: set[int] = set()
    for requested in requested_terms:
        if requested.term_id is not None:
            entry = entries_by_id.get(requested.term_id)
            if entry is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"Monitored term {requested.term_id} does not exist",
                )
        else:
            matches = entries_by_text.get(normalize_term(requested.term or ""), [])
            if not matches:
                raise HTTPException(
                    status_code=422,
                    detail="Matched text does not correspond to a monitored term",
                )
            if len(matches) > 1:
                raise HTTPException(
                    status_code=422,
                    detail="Matched text is ambiguous; provide term_id",
                )
            entry = matches[0]

        if requested.term is not None and normalize_term(requested.term) != normalize_term(
            entry.slur_text
        ):
            raise HTTPException(
                status_code=422,
                detail=f"Matched text does not correspond to monitored term {entry.term_id}",
            )
        if requested.language is not None and requested.language.value != entry.language:
            raise HTTPException(
                status_code=422,
                detail=f"Language does not correspond to monitored term {entry.term_id}",
            )
        if entry.term_id in resolved_ids:
            raise HTTPException(
                status_code=422,
                detail=f"Monitored term {entry.term_id} is duplicated",
            )

        resolved_ids.add(entry.term_id)
        resolved.append(
            (
                entry,
                (requested.term or entry.slur_text).strip(),
                requested.match_type,
            )
        )

    return resolved


@router.post("/", response_model=AlertResponse)
async def create_alert(alert: AlertCreate, db: AsyncSession = Depends(get_db)):
    resolved_terms = await resolve_matched_terms(alert.matched_terms, db)
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
        language=alert.language.value,
        language_confidence=alert.language_confidence,
        hard_hits=json.dumps(alert.hard_hits or []),
        soft_hits=json.dumps(alert.soft_hits or []),
        duration_gate=alert.duration_gate,
        required_duration=alert.required_duration,
    )
    for dictionary_term, matched_text, match_type in resolved_terms:
        new_alert.matched_terms.append(
            AlertMatchedTerm(
                dictionary_term=dictionary_term,
                matched_text=matched_text,
                match_type=match_type,
            )
        )
    db.add(new_alert)
    await db.commit()
    result = await db.execute(
        select(Alert)
        .where(Alert.id == new_alert.id)
        .options(selectinload(Alert.matched_terms).joinedload(AlertMatchedTerm.dictionary_term))
    )
    new_alert = result.scalar_one()

    token_result = await db.execute(select(User).where(User.push_token.isnot(None)))
    tokens = [u.push_token for u in token_result.scalars().all()]
    asyncio.create_task(
        send_expo_pushes(tokens, new_alert.id, new_alert.severity, new_alert.location)
    )

    return hydrate_alert(new_alert)


@router.get("/analytics/categories", response_model=AlertAnalyticsResponse)
async def get_category_analytics(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Alert))
    alerts = result.scalars().all()

    by_category: dict = {}
    by_language: dict = {}
    by_severity: dict = {}
    by_duration_gate: dict = {}
    word_counter: Counter = Counter()

    for alert in alerts:
        by_severity[alert.severity] = by_severity.get(alert.severity, 0) + 1

        if alert.language:
            by_language[alert.language] = by_language.get(alert.language, 0) + 1

        if alert.duration_gate:
            by_duration_gate[alert.duration_gate] = by_duration_gate.get(alert.duration_gate, 0) + 1

        try:
            cats = json.loads(alert.categories or "[]")
        except (json.JSONDecodeError, TypeError):
            cats = []
        for cat in cats:
            by_category[cat] = by_category.get(cat, 0) + 1

        try:
            words = json.loads(alert.detected_words or "[]")
        except (json.JSONDecodeError, TypeError):
            words = []
        word_counter.update(words)

    top_detected_words = [TopWord(word=w, count=c) for w, c in word_counter.most_common(10)]

    return AlertAnalyticsResponse(
        total_alerts=len(alerts),
        by_category=by_category,
        by_language=by_language,
        by_severity=by_severity,
        by_duration_gate=by_duration_gate,
        top_detected_words=top_detected_words,
    )


def _period_stats(alerts: list) -> PeriodStats:
    cat_counter: Counter = Counter()
    lang_counter: Counter = Counter()
    high = medium = low = 0

    for a in alerts:
        if a.severity == "high":
            high += 1
        elif a.severity == "medium":
            medium += 1
        elif a.severity == "low":
            low += 1

        if a.language:
            lang_counter[a.language] += 1

        try:
            cats = json.loads(a.categories or "[]")
        except (json.JSONDecodeError, TypeError):
            cats = []
        cat_counter.update(cats)

    most_common_category = cat_counter.most_common(1)[0][0] if cat_counter else None
    most_common_language = lang_counter.most_common(1)[0][0] if lang_counter else None

    return PeriodStats(
        total=len(alerts),
        high=high,
        medium=medium,
        low=low,
        most_common_category=most_common_category,
        most_common_language=most_common_language,
    )


@router.get("/analytics/summary", response_model=AlertSummaryResponse)
async def get_summary_analytics(db: AsyncSession = Depends(get_db)):
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())

    all_result = await db.execute(select(Alert))
    all_alerts = all_result.scalars().all()

    today_alerts = [
        a
        for a in all_alerts
        if a.created_at and a.created_at.replace(tzinfo=timezone.utc) >= today_start
    ]
    week_alerts = [
        a
        for a in all_alerts
        if a.created_at and a.created_at.replace(tzinfo=timezone.utc) >= week_start
    ]

    return AlertSummaryResponse(
        today=_period_stats(today_alerts),
        this_week=_period_stats(week_alerts),
        all_time=_period_stats(all_alerts),
    )


@router.get("/", response_model=List[AlertResponse])
async def get_alerts(
    severity: Optional[str] = None,
    category: Optional[str] = None,
    language: Optional[LanguageCode] = None,
    duration_gate: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(Alert)
        .options(selectinload(Alert.matched_terms).joinedload(AlertMatchedTerm.dictionary_term))
        .order_by(Alert.created_at.desc())
    )
    if severity:
        query = query.where(Alert.severity == severity)
    if language:
        query = query.where(Alert.language == language.value)
    if category:
        query = query.where(Alert.categories.like(f'%"{category}"%'))
    if duration_gate:
        query = query.where(Alert.duration_gate == duration_gate)
    result = await db.execute(query)
    return [hydrate_alert(a) for a in result.scalars().all()]


@router.get("/{alert_id}", response_model=AlertResponse)
async def get_alert(alert_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Alert)
        .where(Alert.id == alert_id)
        .options(selectinload(Alert.matched_terms).joinedload(AlertMatchedTerm.dictionary_term))
    )
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return hydrate_alert(alert)
