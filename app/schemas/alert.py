from datetime import datetime
from enum import StrEnum
import math
import re
from typing import Annotated, Any, Dict, List, Literal, Optional
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
SUPPORTED_SCHEMA_VERSIONS = frozenset({2})
MAX_STRUCTURED_EVIDENCE_ITEMS = 256
MAX_STRUCTURED_EVIDENCE_DEPTH = 8
MAX_STRUCTURED_EVIDENCE_STRING_LENGTH = 10_000
MAX_STRUCTURED_EVIDENCE_CHARACTERS = 64_000

PROHIBITED_AUDIO_KEYS = frozenset(
    {
        "raw_audio",
        "raw_pcm",
        "pcm",
        "pcm_samples",
        "audio_bytes",
        "audio_blob",
        "audio_base64",
        "wav",
        "waveform_bytes",
        "recorded_audio",
    }
)
PROHIBITED_DEBUG_KEYS = frozenset(
    {
        "raw_vosk_text",
        "vosk_partial",
        "debug_transcript",
        "partial_transcript",
        "audio_debug",
    }
)


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


class TriggerType(StrEnum):
    KEYWORD = "KEYWORD"
    ACOUSTIC = "ACOUSTIC"
    TEST = "TEST"

    @classmethod
    def normalize(cls, value):
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise ValueError("trigger_type must be KEYWORD, ACOUSTIC, or TEST")
        try:
            return cls(value.strip().upper())
        except ValueError as exc:
            raise ValueError("trigger_type must be KEYWORD, ACOUSTIC, or TEST") from exc


def _normalized_privacy_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().casefold()).strip("_")


def validate_structured_evidence(value: Any, *, field_name: str) -> Any:
    """Validate bounded JSON evidence without assigning detector-specific semantics."""

    character_count = 0

    def visit(item: Any, depth: int) -> None:
        nonlocal character_count
        if depth > MAX_STRUCTURED_EVIDENCE_DEPTH:
            raise ValueError(
                f"{field_name} exceeds the maximum nesting depth of {MAX_STRUCTURED_EVIDENCE_DEPTH}"
            )
        if isinstance(item, dict):
            if len(item) > MAX_STRUCTURED_EVIDENCE_ITEMS:
                raise ValueError(
                    f"{field_name} objects may contain at most {MAX_STRUCTURED_EVIDENCE_ITEMS} keys"
                )
            for key, nested in item.items():
                normalized_key = _normalized_privacy_key(key)
                if normalized_key in PROHIBITED_AUDIO_KEYS:
                    raise ValueError(f"prohibited raw-audio field: {key}")
                if normalized_key in PROHIBITED_DEBUG_KEYS:
                    raise ValueError(f"prohibited debug speech field: {key}")
                character_count += len(str(key))
                visit(nested, depth + 1)
            return
        if isinstance(item, (list, tuple)):
            if len(item) > MAX_STRUCTURED_EVIDENCE_ITEMS:
                raise ValueError(
                    f"{field_name} arrays may contain at most {MAX_STRUCTURED_EVIDENCE_ITEMS} items"
                )
            for nested in item:
                visit(nested, depth + 1)
            return
        if isinstance(item, str):
            if len(item) > MAX_STRUCTURED_EVIDENCE_STRING_LENGTH:
                raise ValueError(f"{field_name} contains an oversized string")
            character_count += len(item)
            return
        if (
            isinstance(item, bool)
            or item is None
            or isinstance(item, int)
            or isinstance(item, (datetime, UUID, StrEnum))
        ):
            return
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError(f"{field_name} numeric evidence must be finite")
            return
        raise ValueError(f"{field_name} must contain JSON-compatible evidence")

    visit(value, 0)
    if character_count > MAX_STRUCTURED_EVIDENCE_CHARACTERS:
        raise ValueError(
            f"{field_name} exceeds the maximum total character count of "
            f"{MAX_STRUCTURED_EVIDENCE_CHARACTERS}"
        )
    return value


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

    event_id: UUID
    schema_version: int = Field(ge=1, strict=True)
    trigger_type: TriggerType
    severity: SeverityLevel
    severity_reasons: List[BoundedEvidenceString] = Field(
        min_length=1,
        max_length=MAX_EVIDENCE_ITEMS,
    )
    review_message: str = Field(max_length=500)
    device_identifier: Optional[str] = Field(default=None, min_length=1, max_length=100)
    device_source: Optional[Dict[str, Any]] = Field(
        default=None,
        min_length=1,
        max_length=MAX_EVIDENCE_ITEMS,
    )
    event_start_timestamp: datetime
    event_end_timestamp: datetime
    severity_evidence: Optional[SeverityEvidence] = None
    monitored_terms: List[Any] = Field(default_factory=list, max_length=MAX_EVIDENCE_ITEMS)
    monitored_word_detected: bool = False
    monitored_word_occurrences: List[Dict[str, Any]] = Field(
        default_factory=list,
        max_length=MAX_EVIDENCE_ITEMS,
    )
    acoustic_trigger_evidence: Optional[Dict[str, Any]] = None
    detailed_acoustic_evidence: Optional[Dict[str, Any]] = None
    tone_evidence: Optional[Dict[str, Any]] = None
    repetition_evidence: Optional[Dict[str, Any]] = None
    direct_address_evidence: Optional[Dict[str, Any]] = None
    laughter_context: Optional[Dict[str, Any]] = None
    transcription_status: Optional[str] = Field(default=None, max_length=100)
    processing_latency: Optional[Dict[str, Any]] = None
    dropped_data_metrics: Optional[Dict[str, Any]] = None
    collector_statuses: Optional[Dict[str, Any] | List[Any]] = None
    event_delivery_summary: Optional[Dict[str, Any]] = None
    extension_count: int = Field(default=0, ge=0)
    extension_reasons: List[BoundedEvidenceString] = Field(
        default_factory=list,
        max_length=MAX_EVIDENCE_ITEMS,
    )
    maximum_duration_reached: bool = False
    pre_trigger_seconds: Optional[float] = Field(default=None, ge=0.0, le=3600.0)
    post_trigger_seconds: Optional[float] = Field(default=None, ge=0.0, le=3600.0)
    trigger_timestamp: Optional[datetime] = None
    test_mode: bool = False
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    duration: Optional[float] = Field(default=None, ge=0.0, le=3600.0)
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
    yamnet_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
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

    @model_validator(mode="before")
    @classmethod
    def reject_recursive_private_content(cls, value):
        if isinstance(value, dict):
            validate_structured_evidence(value, field_name="alert payload")
        return value

    @field_validator("schema_version")
    @classmethod
    def require_supported_schema_version(cls, value: int) -> int:
        if value not in SUPPORTED_SCHEMA_VERSIONS:
            supported = ", ".join(str(item) for item in sorted(SUPPORTED_SCHEMA_VERSIONS))
            raise ValueError(f"schema_version must be one of: {supported}")
        return value

    @field_validator("trigger_type", mode="before")
    @classmethod
    def normalize_trigger_type(cls, value):
        return TriggerType.normalize(value)

    @field_validator("device_identifier")
    @classmethod
    def normalize_device_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("device_identifier must not be blank")
        return normalized

    @field_validator("severity", mode="before")
    @classmethod
    def normalize_severity(cls, value):
        return SeverityLevel.normalize(value)

    @model_validator(mode="after")
    def require_new_event_transcript(self):
        if self.transcription_status == "complete" and (
            self.transcribed_text is None or not self.transcribed_text.strip()
        ):
            raise ValueError("transcript must be nonblank when transcription_status is complete")
        return self

    @model_validator(mode="after")
    def validate_finalized_contract(self):
        if self.device_identifier is None and self.device_source is None:
            raise ValueError("device_identifier or device_source is required")
        if self.event_start_timestamp.tzinfo is None or self.event_end_timestamp.tzinfo is None:
            raise ValueError("event timestamps must include a timezone")
        if self.event_end_timestamp < self.event_start_timestamp:
            raise ValueError("event_end_timestamp must not be earlier than event_start_timestamp")
        if self.review_message != REVIEW_NOTICE:
            raise ValueError(f"review_message must equal: {REVIEW_NOTICE}")
        if self.trigger_type == TriggerType.TEST and not self.test_mode:
            raise ValueError("TEST trigger_type requires test_mode=true")
        if self.trigger_type != TriggerType.TEST and self.test_mode:
            raise ValueError("test_mode=true requires trigger_type=TEST")
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
            self.pre_trigger_seconds,
            self.post_trigger_seconds,
        )
        if any(value is not None and not math.isfinite(value) for value in values):
            raise ValueError("numeric alert evidence must be finite")
        return self

    @field_validator("monitored_word_occurrences")
    @classmethod
    def validate_monitored_word_confidence(cls, value):
        def visit(item):
            if isinstance(item, dict):
                for key, nested in item.items():
                    if _normalized_privacy_key(key) == "confidence":
                        if (
                            isinstance(nested, bool)
                            or not isinstance(nested, (int, float))
                            or not math.isfinite(float(nested))
                            or not 0.0 <= float(nested) <= 1.0
                        ):
                            raise ValueError("monitored-word confidence must be between 0 and 1")
                    visit(nested)
            elif isinstance(item, list):
                for nested in item:
                    visit(nested)

        visit(value)
        return value

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
    schema_version: Optional[int] = None
    trigger_type: Optional[TriggerType] = None
    device_id: Optional[UUID] = None
    device_code: Optional[str] = None
    device_display_name: Optional[str] = None
    classroom_name: Optional[str] = None
    school_name: Optional[str] = None
    severity: str
    severity_level: SeverityLevel
    severity_reasons: Optional[List[str]] = None
    review_message: Optional[str] = None
    device_identifier: Optional[str] = None
    device_source: Optional[Dict[str, Any]] = None
    event_start_timestamp: Optional[datetime] = None
    event_end_timestamp: Optional[datetime] = None
    severity_evidence: Optional[SeverityEvidence] = None
    monitored_terms: Optional[List[Any]] = None
    monitored_word_detected: Optional[bool] = None
    monitored_word_occurrences: Optional[List[Dict[str, Any]]] = None
    acoustic_trigger_evidence: Optional[Dict[str, Any]] = None
    detailed_acoustic_evidence: Optional[Dict[str, Any]] = None
    tone_evidence: Optional[Dict[str, Any]] = None
    repetition_evidence: Optional[Dict[str, Any]] = None
    direct_address_evidence: Optional[Dict[str, Any]] = None
    laughter_context: Optional[Dict[str, Any]] = None
    transcript: Optional[str] = None
    transcription_status: Optional[str] = None
    processing_latency: Optional[Dict[str, Any]] = None
    dropped_data_metrics: Optional[Dict[str, Any]] = None
    collector_statuses: Optional[Dict[str, Any] | List[Any]] = None
    event_delivery_summary: Optional[Dict[str, Any]] = None
    extension_count: Optional[int] = None
    extension_reasons: Optional[List[str]] = None
    maximum_duration_reached: Optional[bool] = None
    pre_trigger_seconds: Optional[float] = None
    post_trigger_seconds: Optional[float] = None
    trigger_timestamp: Optional[datetime] = None
    test_mode: bool = False
    delivery_status: str = "stored"
    push_status: Optional[
        Literal["pending", "accepted", "partial", "rejected", "failed", "skipped"]
    ] = None
    confidence: Optional[float] = None
    duration: Optional[float] = None
    location: Optional[str] = None
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
        if data.get("transcript") is None:
            data["transcript"] = data.get("transcribed_text")
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
