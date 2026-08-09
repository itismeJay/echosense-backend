import asyncio
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone, timedelta
from enum import Enum
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.database import get_db
from app.models.alert import Alert, AlertMatchedTerm
from app.models.edge_device import EdgeDevice
from app.models.slur import SlurEntry
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
from app.routers.auth import require_alert_reviewer
from app.services.notification_recipients import resolve_notification_recipients
from app.services.device_auth import authenticate_edge_device
from typing import List, Optional
from sqlalchemy.orm import selectinload

from app.config import settings
from app.languages import LanguageCode

router = APIRouter(prefix="/alerts", tags=["Alerts"])

FINGERPRINT_EXCLUDED_FIELDS = frozenset({"location"})


def hydrate_alert(alert: Alert) -> Alert:
    """Decode JSON-string columns back into Python lists for the response model."""
    alert.detected_words = json.loads(alert.detected_words or "[]")
    alert.waveform_snapshot = json.loads(alert.waveform_snapshot or "[]")
    alert.categories = json.loads(alert.categories or "[]")
    alert.hard_hits = json.loads(alert.hard_hits or "[]")
    alert.soft_hits = json.loads(alert.soft_hits or "[]")
    return alert


def alert_load_options():
    return (
        selectinload(Alert.matched_terms).joinedload(AlertMatchedTerm.dictionary_term),
        selectinload(Alert.edge_device),
    )


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


async def get_alert_by_event_id(event_id, db: AsyncSession) -> Alert | None:
    result = await db.execute(
        select(Alert).where(Alert.event_id == event_id).options(*alert_load_options())
    )
    return result.scalar_one_or_none()


def _canonical_fingerprint_value(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {
            str(key): _canonical_fingerprint_value(nested)
            for key, nested in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_fingerprint_value(item) for item in value]
    return value


def alert_request_fingerprint(alert: AlertCreate) -> str:
    """Hash every canonical request field except untrusted legacy location."""

    payload = alert.model_dump(mode="python", exclude=FINGERPRINT_EXCLUDED_FIELDS)
    canonical = _canonical_fingerprint_value(payload)
    encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_duplicate_alert(
    existing_alert: Alert,
    edge_device: EdgeDevice,
    request_fingerprint: str,
) -> None:
    if (
        existing_alert.edge_device_id is not None
        and existing_alert.edge_device_id != edge_device.id
    ):
        raise HTTPException(status_code=409, detail="Event identifier conflict")
    if existing_alert.request_fingerprint != request_fingerprint:
        raise HTTPException(status_code=409, detail="Event payload conflict")


def validate_idempotency_key(idempotency_key: str | None, event_id: UUID) -> None:
    if idempotency_key is None or not idempotency_key.strip():
        raise HTTPException(status_code=422, detail="Idempotency-Key header is required")
    try:
        header_event_id = UUID(idempotency_key.strip())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Idempotency-Key must be a UUID") from exc
    if header_event_id != event_id:
        raise HTTPException(
            status_code=422,
            detail="Idempotency-Key must match event_id",
        )


def legacy_confidence_value(alert: AlertCreate) -> float:
    if alert.confidence is not None:
        return alert.confidence
    occurrence_confidences = [
        occurrence.get("confidence")
        for occurrence in alert.monitored_word_occurrences
        if isinstance(occurrence.get("confidence"), (int, float))
        and not isinstance(occurrence.get("confidence"), bool)
    ]
    if occurrence_confidences:
        return float(max(occurrence_confidences))
    if alert.yamnet_score is not None:
        return alert.yamnet_score
    return 0.0


@router.post("/", response_model=AlertResponse)
async def create_alert(
    alert: AlertCreate,
    db: AsyncSession = Depends(get_db),
    edge_device: EdgeDevice = Depends(authenticate_edge_device),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    validate_idempotency_key(idempotency_key, alert.event_id)
    if (
        alert.device_identifier is not None
        and alert.device_identifier.strip() != edge_device.device_code
    ):
        raise HTTPException(
            status_code=422, detail="device_identifier must match authenticated device"
        )
    if alert.device_source is not None:
        for identity_key in ("device_identifier", "device_code"):
            reported_identity = alert.device_source.get(identity_key)
            if (
                reported_identity is not None
                and str(reported_identity).strip() != edge_device.device_code
            ):
                raise HTTPException(
                    status_code=422,
                    detail=f"device_source.{identity_key} must match authenticated device",
                )
    if alert.test_mode and not settings.ECHOSENSE_ALLOW_TEST_ALERTS:
        raise HTTPException(status_code=403, detail="Synthetic alert ingestion is disabled")

    request_fingerprint = alert_request_fingerprint(alert)
    existing_alert = await get_alert_by_event_id(alert.event_id, db)
    if existing_alert is not None:
        validate_duplicate_alert(existing_alert, edge_device, request_fingerprint)
        return hydrate_alert(existing_alert)

    resolved_terms = await resolve_matched_terms(alert.matched_terms, db)
    duration = alert.duration
    if duration is None:
        duration = (alert.event_end_timestamp - alert.event_start_timestamp).total_seconds()
    new_alert = Alert(
        event_id=alert.event_id,
        schema_version=alert.schema_version,
        trigger_type=alert.trigger_type.value,
        edge_device_id=edge_device.id,
        classroom_name_snapshot=edge_device.classroom_name,
        school_name_snapshot=edge_device.school_name,
        severity=alert.severity.value,
        severity_reasons=alert.severity_reasons,
        review_message=alert.review_message,
        device_identifier=alert.device_identifier,
        device_source=alert.device_source,
        event_start_timestamp=alert.event_start_timestamp,
        event_end_timestamp=alert.event_end_timestamp,
        severity_evidence=(
            alert.severity_evidence.model_dump(mode="json")
            if alert.severity_evidence is not None
            else None
        ),
        monitored_terms=alert.monitored_terms,
        monitored_word_detected=alert.monitored_word_detected,
        monitored_word_occurrences=alert.monitored_word_occurrences,
        acoustic_trigger_evidence=alert.acoustic_trigger_evidence,
        detailed_acoustic_evidence=alert.detailed_acoustic_evidence,
        tone_evidence=alert.tone_evidence,
        repetition_evidence=alert.repetition_evidence,
        direct_address_evidence=alert.direct_address_evidence,
        laughter_context=alert.laughter_context,
        transcription_status=alert.transcription_status,
        processing_latency=alert.processing_latency,
        dropped_data_metrics=alert.dropped_data_metrics,
        collector_statuses=alert.collector_statuses,
        event_delivery_summary=alert.event_delivery_summary,
        extension_count=alert.extension_count,
        extension_reasons=alert.extension_reasons,
        maximum_duration_reached=alert.maximum_duration_reached,
        pre_trigger_seconds=alert.pre_trigger_seconds,
        post_trigger_seconds=alert.post_trigger_seconds,
        trigger_timestamp=alert.trigger_timestamp,
        test_mode=alert.test_mode,
        delivery_status="stored",
        request_fingerprint=request_fingerprint,
        push_status="pending",
        confidence=legacy_confidence_value(alert),
        duration=duration,
        location=edge_device.classroom_name,
        transcribed_text=alert.transcribed_text,
        detected_words=json.dumps(alert.detected_words or []),
        yamnet_class=alert.yamnet_class,
        yamnet_score=alert.yamnet_score,
        yamnet_ran=alert.yamnet_ran,
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
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing_alert = await get_alert_by_event_id(alert.event_id, db)
        if existing_alert is not None:
            validate_duplicate_alert(existing_alert, edge_device, request_fingerprint)
            return hydrate_alert(existing_alert)
        raise
    result = await db.execute(
        select(Alert).where(Alert.id == new_alert.id).options(*alert_load_options())
    )
    new_alert = result.scalar_one()

    recipients = await resolve_notification_recipients(db)
    if not recipients.controlled_test_mode or recipients.failure_reason is None:
        asyncio.create_task(
            send_expo_pushes(
                list(recipients.tokens),
                new_alert.id,
                new_alert.severity,
                new_alert.location,
                event_id=new_alert.event_id,
                trigger_type=new_alert.trigger_type,
                is_test=new_alert.test_mode,
                record_status=True,
            )
        )
    else:
        new_alert.push_status = "skipped"
        new_alert.push_last_error = recipients.failure_reason
        await db.commit()

    return hydrate_alert(new_alert)


@router.get("/analytics/categories", response_model=AlertAnalyticsResponse)
async def get_category_analytics(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_alert_reviewer),
):
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
        if a.severity == "HIGH":
            high += 1
        elif a.severity == "MEDIUM":
            medium += 1
        elif a.severity == "LOW":
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
async def get_summary_analytics(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_alert_reviewer),
):
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
    event_id: UUID | None = None,
    severity: Optional[str] = None,
    category: Optional[str] = None,
    language: Optional[LanguageCode] = None,
    duration_gate: Optional[str] = None,
    skip: int = Query(default=0, ge=0),
    limit: int | None = Query(default=None, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_alert_reviewer),
):
    query = select(Alert).options(*alert_load_options()).order_by(Alert.created_at.desc())
    if event_id is not None:
        query = query.where(Alert.event_id == event_id)
    if severity:
        from app.schemas.alert import SeverityLevel

        try:
            normalized_severity = SeverityLevel.normalize(severity)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        query = query.where(Alert.severity == normalized_severity.value)
    if language:
        query = query.where(Alert.language == language.value)
    if category:
        query = query.where(Alert.categories.like(f'%"{category}"%'))
    if duration_gate:
        query = query.where(Alert.duration_gate == duration_gate)
    query = query.offset(skip)
    if limit is not None:
        query = query.limit(limit)
    result = await db.execute(query)
    return [hydrate_alert(a) for a in result.scalars().all()]


@router.get("/{alert_id}", response_model=AlertResponse)
async def get_alert(
    alert_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_alert_reviewer),
):
    result = await db.execute(
        select(Alert).where(Alert.id == alert_id).options(*alert_load_options())
    )
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return hydrate_alert(alert)
