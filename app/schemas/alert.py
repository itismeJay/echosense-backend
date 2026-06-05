from pydantic import BaseModel
from datetime import datetime
from typing import Any, Dict, Optional, List


class AlertCreate(BaseModel):
    severity: str
    confidence: float
    duration: float
    location: Optional[str] = "Classroom"
    transcribed_text: Optional[str] = None
    detected_words: Optional[List[str]] = None
    yamnet_class: Optional[str] = None
    yamnet_score: Optional[float] = None
    emotion: Optional[str] = None
    rms: Optional[float] = None
    energy_variance: Optional[float] = None
    zero_crossing_rate: Optional[float] = None
    peak_to_average: Optional[float] = None
    waveform_snapshot: Optional[List[int]] = None
    # v2 fields
    categories: Optional[List[str]] = None
    language: Optional[str] = None
    hard_hits: Optional[List[str]] = None
    soft_hits: Optional[List[str]] = None
    duration_gate: Optional[str] = None
    required_duration: Optional[float] = None


class AlertResponse(BaseModel):
    id: int
    severity: str
    confidence: float
    duration: float
    location: str
    status: str
    created_at: datetime
    transcribed_text: Optional[str] = None
    detected_words: Optional[List[str]] = None
    yamnet_class: Optional[str] = None
    yamnet_score: Optional[float] = None
    emotion: Optional[str] = None
    rms: Optional[float] = None
    energy_variance: Optional[float] = None
    zero_crossing_rate: Optional[float] = None
    peak_to_average: Optional[float] = None
    waveform_snapshot: Optional[List[int]] = None
    # v2 fields
    categories: Optional[List[str]] = None
    language: Optional[str] = None
    hard_hits: Optional[List[str]] = None
    soft_hits: Optional[List[str]] = None
    duration_gate: Optional[str] = None
    required_duration: Optional[float] = None

    class Config:
        from_attributes = True


class AlertUpdate(BaseModel):
    status: Optional[str] = None


class TopWord(BaseModel):
    word: str
    count: int


class AlertAnalyticsResponse(BaseModel):
    total_alerts: int
    by_category: Dict[str, int]
    by_language: Dict[str, int]
    by_severity: Dict[str, int]
    by_duration_gate: Dict[str, int]
    top_detected_words: List[TopWord]


class PeriodStats(BaseModel):
    total: int
    high: int
    medium: int
    low: int
    most_common_category: Optional[str] = None
    most_common_language: Optional[str] = None


class AlertSummaryResponse(BaseModel):
    today: PeriodStats
    this_week: PeriodStats
    all_time: PeriodStats
