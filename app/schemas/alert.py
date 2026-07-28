from datetime import datetime
import math
from typing import Dict, List, Optional
import unicodedata
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from app.languages import DictionaryLanguageCode, LanguageCode


def normalize_term(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).strip().casefold().split())


class MatchedTermCreate(BaseModel):
    term_id: Optional[int] = Field(default=None, gt=0)
    term: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=100,
        validation_alias=AliasChoices("term", "matched_text"),
    )
    language: Optional[DictionaryLanguageCode] = None
    match_type: str = Field(default="exact", min_length=1, max_length=30)

    @model_validator(mode="after")
    def require_term_identifier(self):
        if self.term_id is None and self.term is None:
            raise ValueError("matched term must include term_id or term")
        return self


class MatchedTermResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    term_id: int
    term: str
    language: DictionaryLanguageCode
    match_type: str


class AlertCreate(BaseModel):
    event_id: Optional[UUID] = None
    severity: str
    confidence: float
    duration: float
    location: Optional[str] = "Classroom"
    transcribed_text: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("transcribed_text", "transcript"),
    )
    detected_words: Optional[List[str]] = None
    yamnet_class: Optional[str] = None
    yamnet_score: Optional[float] = None
    yamnet_ran: Optional[bool] = None
    emotion: Optional[str] = None
    rms: Optional[float] = None
    energy_variance: Optional[float] = None
    zero_crossing_rate: Optional[float] = None
    peak_to_average: Optional[float] = None
    waveform_snapshot: Optional[List[int]] = None
    # v2 fields
    categories: Optional[List[str]] = None
    language: LanguageCode = LanguageCode.UNKNOWN
    language_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    matched_terms: List[MatchedTermCreate] = Field(default_factory=list)
    hard_hits: Optional[List[str]] = None
    soft_hits: Optional[List[str]] = None
    duration_gate: Optional[str] = None
    required_duration: Optional[float] = None

    @model_validator(mode="after")
    def validate_yamnet_evidence(self):
        if self.yamnet_ran is None:
            return self

        if not self.yamnet_ran:
            self.yamnet_class = "NotRun"
            self.yamnet_score = 0.0
            return self

        label = (self.yamnet_class or "").strip()
        if not label or label.casefold() == "notrun":
            raise ValueError(
                "yamnet_class must be an actual non-empty label when yamnet_ran is true"
            )
        if (
            self.yamnet_score is None
            or not math.isfinite(self.yamnet_score)
            or not 0.0 <= self.yamnet_score <= 1.0
        ):
            raise ValueError("yamnet_score must be between 0 and 1 when yamnet_ran is true")

        self.yamnet_class = label
        return self

    @model_validator(mode="after")
    def reject_duplicate_matched_terms(self):
        identifiers: set[tuple[str, object]] = set()
        for matched_term in self.matched_terms:
            if matched_term.term_id is not None:
                identifier = ("id", matched_term.term_id)
            else:
                identifier = ("term", normalize_term(matched_term.term or ""))
            if identifier in identifiers:
                raise ValueError("matched_terms contains a duplicate entry")
            identifiers.add(identifier)
        return self


class AlertResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: Optional[UUID] = None
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
    yamnet_ran: Optional[bool] = None
    emotion: Optional[str] = None
    rms: Optional[float] = None
    energy_variance: Optional[float] = None
    zero_crossing_rate: Optional[float] = None
    peak_to_average: Optional[float] = None
    waveform_snapshot: Optional[List[int]] = None
    # v2 fields
    categories: Optional[List[str]] = None
    language: LanguageCode
    language_confidence: Optional[float] = None
    matched_terms: List[MatchedTermResponse] = Field(default_factory=list)
    hard_hits: Optional[List[str]] = None
    soft_hits: Optional[List[str]] = None
    duration_gate: Optional[str] = None
    required_duration: Optional[float] = None


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
