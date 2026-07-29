from datetime import datetime
from enum import StrEnum
import math
from typing import Annotated, Dict, List, Optional
import unicodedata
from uuid import UUID

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.languages import DictionaryLanguageCode, LanguageCode

MAX_ALERT_TRANSCRIPT_LENGTH = 10_000
MAX_EVIDENCE_ITEMS = 50
MAX_EVIDENCE_STRING_LENGTH = 200
MAX_EVIDENCE_TOTAL_CHARACTERS = 16_000
MAX_WAVEFORM_POINTS = 256
REVIEW_NOTICE = "Unverified possible-aggression alert. Human review required."


class SeverityLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"

    @classmethod
    def normalize(cls, value):
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise ValueError("severity must be LOW, MEDIUM, or HIGH")
        try:
            return cls(value.strip().upper())
        except ValueError as exc:
            raise ValueError("severity must be LOW, MEDIUM, or HIGH") from exc


BoundedEvidenceString = Annotated[
    str,
    Field(min_length=1, max_length=MAX_EVIDENCE_STRING_LENGTH),
]


class SeverityEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: SeverityLevel
    reasons: List[BoundedEvidenceString] = Field(
        min_length=1,
        max_length=MAX_EVIDENCE_ITEMS,
    )
    term_categories: Dict[BoundedEvidenceString, List[BoundedEvidenceString]] = Field(
        default_factory=dict,
        max_length=MAX_EVIDENCE_ITEMS,
    )
    supporting_evidence: List[BoundedEvidenceString] = Field(
        default_factory=list,
        max_length=MAX_EVIDENCE_ITEMS,
    )

    @field_validator("level", mode="before")
    @classmethod
    def normalize_level(cls, value):
        return SeverityLevel.normalize(value)

    @field_validator("term_categories")
    @classmethod
    def bound_term_category_values(cls, value):
        for phrases in value.values():
            if len(phrases) > MAX_EVIDENCE_ITEMS:
                raise ValueError(
                    f"term category phrase lists may contain at most {MAX_EVIDENCE_ITEMS} items"
                )
        return value

    @model_validator(mode="after")
    def bound_total_characters(self):
        total = sum(len(reason) for reason in self.reasons)
        total += sum(len(item) for item in self.supporting_evidence)
        total += sum(
            len(category) + sum(len(phrase) for phrase in phrases)
            for category, phrases in self.term_categories.items()
        )
        if total > MAX_EVIDENCE_TOTAL_CHARACTERS:
            raise ValueError("severity evidence exceeds the maximum total character count")
        return self


def normalize_term(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).strip().casefold().split())


class MatchedTermCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    model_config = ConfigDict(extra="forbid")

    event_id: Optional[UUID] = None
    severity: SeverityLevel
    severity_evidence: Optional[SeverityEvidence] = None
    confidence: float = Field(ge=0.0, le=1.0)
    duration: float = Field(ge=0.0, le=3600.0)
    location: Optional[str] = Field(default="Classroom", max_length=200)
    transcribed_text: Optional[str] = Field(
        default=None,
        max_length=MAX_ALERT_TRANSCRIPT_LENGTH,
        validation_alias=AliasChoices("transcribed_text", "transcript"),
    )
    detected_words: Optional[List[Annotated[str, Field(max_length=100)]]] = Field(
        default=None,
        max_length=100,
    )
    yamnet_class: Optional[str] = Field(default=None, max_length=200)
    yamnet_score: Optional[float] = None
    yamnet_ran: Optional[bool] = None
    emotion: Optional[str] = Field(default=None, max_length=100)
    rms: Optional[float] = None
    energy_variance: Optional[float] = None
    zero_crossing_rate: Optional[float] = None
    peak_to_average: Optional[float] = None
    waveform_snapshot: Optional[List[Annotated[int, Field(ge=0, le=32768)]]] = Field(
        default=None, max_length=MAX_WAVEFORM_POINTS
    )
    # v2 fields
    categories: Optional[List[Annotated[str, Field(max_length=100)]]] = Field(
        default=None,
        max_length=MAX_EVIDENCE_ITEMS,
    )
    language: LanguageCode = LanguageCode.UNKNOWN
    language_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    matched_terms: List[MatchedTermCreate] = Field(
        default_factory=list,
        max_length=MAX_EVIDENCE_ITEMS,
    )
    hard_hits: Optional[List[Annotated[str, Field(max_length=100)]]] = Field(
        default=None,
        max_length=MAX_EVIDENCE_ITEMS,
    )
    soft_hits: Optional[List[Annotated[str, Field(max_length=100)]]] = Field(
        default=None,
        max_length=MAX_EVIDENCE_ITEMS,
    )
    duration_gate: Optional[str] = Field(default=None, max_length=20)
    required_duration: Optional[float] = Field(default=None, ge=0.0, le=3600.0)

    @field_validator("severity", mode="before")
    @classmethod
    def normalize_severity(cls, value):
        return SeverityLevel.normalize(value)

    @model_validator(mode="after")
    def require_new_event_transcript(self):
        if self.event_id is not None and (
            self.transcribed_text is None or not self.transcribed_text.strip()
        ):
            raise ValueError(
                "transcribed_text must contain the finalized transcript when event_id is provided"
            )
        return self

    @model_validator(mode="after")
    def require_matching_severity_evidence_level(self):
        if self.severity_evidence is not None and self.severity_evidence.level != self.severity:
            raise ValueError("severity_evidence.level must match severity")
        return self

    @model_validator(mode="after")
    def require_finite_numeric_evidence(self):
        values = (
            self.confidence,
            self.duration,
            self.yamnet_score,
            self.rms,
            self.energy_variance,
            self.zero_crossing_rate,
            self.peak_to_average,
            self.required_duration,
        )
        if any(value is not None and not math.isfinite(value) for value in values):
            raise ValueError("numeric alert evidence must be finite")
        return self

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
    severity_level: SeverityLevel
    severity_evidence: Optional[SeverityEvidence] = None
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
    review_notice: str = REVIEW_NOTICE

    @model_validator(mode="before")
    @classmethod
    def expose_compatible_and_canonical_severity(cls, value):
        if isinstance(value, dict):
            data = dict(value)
        else:
            data = {
                field: getattr(value, field, None)
                for field in cls.model_fields
                if field not in {"severity_level", "review_notice"}
            }
        level = SeverityLevel.normalize(data.get("severity"))
        data["severity"] = level.value.lower()
        data["severity_level"] = level
        return data


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
