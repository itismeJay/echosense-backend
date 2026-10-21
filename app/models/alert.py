from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.languages import LanguageCode


class AlertMatchedTerm(Base):
    __tablename__ = "alert_matched_terms"
    __table_args__ = (
        UniqueConstraint(
            "alert_id",
            "term_id",
            name="uq_alert_matched_terms_alert_id_term_id",
        ),
    )

    id = Column(Integer, primary_key=True)
    alert_id = Column(
        Integer,
        ForeignKey("alerts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    term_id = Column(
        Integer,
        ForeignKey("slur_dictionary.term_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    matched_text = Column(String(100), nullable=False)
    match_type = Column(String(30), nullable=False, default="exact", server_default="exact")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    alert = relationship("Alert", back_populates="matched_terms")
    dictionary_term = relationship("SlurEntry", back_populates="alert_matches")

    @property
    def term(self) -> str:
        return self.dictionary_term.slur_text

    @property
    def language(self) -> str:
        return self.dictionary_term.language


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        CheckConstraint(
            "language IN ('fil', 'ceb', 'en', 'mixed', 'unknown')",
            name="ck_alerts_language",
        ),
        CheckConstraint(
            "language_confidence IS NULL OR "
            "(language_confidence >= 0 AND language_confidence <= 1)",
            name="ck_alerts_language_confidence",
        ),
        CheckConstraint(
            "severity IN ('LOW', 'MEDIUM', 'HIGH')",
            name="ck_alerts_severity",
        ),
        CheckConstraint(
            "trigger_type IS NULL OR trigger_type IN ('KEYWORD', 'ACOUSTIC', 'TEST')",
            name="ck_alerts_trigger_type",
        ),
        CheckConstraint(
            "delivery_status IN ('stored')",
            name="ck_alerts_delivery_status",
        ),
        CheckConstraint(
            "schema_version IS NULL OR schema_version >= 1",
            name="ck_alerts_schema_version",
        ),
        CheckConstraint(
            "event_start_timestamp IS NULL OR event_end_timestamp IS NULL OR "
            "event_end_timestamp >= event_start_timestamp",
            name="ck_alerts_event_timestamp_order",
        ),
        CheckConstraint(
            "push_status IN ('pending', 'accepted', 'partial', 'rejected', 'failed', 'skipped')",
            name="ck_alerts_push_status",
        ),
        CheckConstraint(
            "push_attempt_count >= 0",
            name="ck_alerts_push_attempt_count",
        ),
        CheckConstraint(
            "yamnet_ran IS NULL OR "
            "(yamnet_ran = false AND yamnet_class IS NOT NULL "
            "AND yamnet_class = 'NotRun' AND yamnet_score IS NOT NULL "
            "AND yamnet_score = 0) OR "
            "(yamnet_ran = true AND yamnet_class IS NOT NULL "
            "AND btrim(yamnet_class) <> '' AND yamnet_score IS NOT NULL "
            "AND lower(btrim(yamnet_class)) <> 'notrun' "
            "AND yamnet_score >= 0 AND yamnet_score <= 1)",
            name="ck_alerts_yamnet_evidence",
        ),
        UniqueConstraint("event_id", name="uq_alerts_event_id"),
        ForeignKeyConstraint(
            ["classroom_id", "school_id"],
            ["classrooms.id", "classrooms.school_id"],
            name="fk_alerts_classroom_school",
            ondelete="RESTRICT",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(UUID(as_uuid=True), nullable=True)
    schema_version = Column(Integer, nullable=True)
    trigger_type = Column(String(20), nullable=True, index=True)
    edge_device_id = Column(
        UUID(as_uuid=True),
        ForeignKey("edge_devices.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    classroom_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    school_id = Column(
        UUID(as_uuid=True),
        ForeignKey("schools.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    classroom_name_snapshot = Column(String(200), nullable=True)
    school_name_snapshot = Column(String(200), nullable=True)
    severity = Column(String, nullable=False)
    severity_reasons = Column(JSONB, nullable=True)
    review_message = Column(Text, nullable=True)
    device_identifier = Column(String(100), nullable=True)
    device_source = Column(JSONB, nullable=True)
    event_start_timestamp = Column(DateTime(timezone=True), nullable=True, index=True)
    event_end_timestamp = Column(DateTime(timezone=True), nullable=True)
    severity_evidence = Column(JSONB, nullable=True)
    monitored_terms = Column(JSONB, nullable=True)
    monitored_word_detected = Column(Boolean, nullable=True)
    monitored_word_occurrences = Column(JSONB, nullable=True)
    acoustic_trigger_evidence = Column(JSONB, nullable=True)
    detailed_acoustic_evidence = Column(JSONB, nullable=True)
    tone_evidence = Column(JSONB, nullable=True)
    repetition_evidence = Column(JSONB, nullable=True)
    direct_address_evidence = Column(JSONB, nullable=True)
    laughter_context = Column(JSONB, nullable=True)
    transcription_status = Column(String(100), nullable=True)
    processing_latency = Column(JSONB, nullable=True)
    dropped_data_metrics = Column(JSONB, nullable=True)
    collector_statuses = Column(JSONB, nullable=True)
    event_delivery_summary = Column(JSONB, nullable=True)
    extension_count = Column(Integer, nullable=True)
    extension_reasons = Column(JSONB, nullable=True)
    maximum_duration_reached = Column(Boolean, nullable=True)
    pre_trigger_seconds = Column(Float, nullable=True)
    post_trigger_seconds = Column(Float, nullable=True)
    trigger_timestamp = Column(DateTime(timezone=True), nullable=True)
    test_mode = Column(Boolean, nullable=False, default=False, server_default="false")
    delivery_status = Column(String(20), nullable=False, default="stored", server_default="stored")
    request_fingerprint = Column(String(64), nullable=True)
    push_status = Column(
        String(20),
        nullable=False,
        default="pending",
        server_default="pending",
    )
    push_attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    push_last_error = Column(String(100), nullable=True)
    push_provider_ticket_id = Column(Text, nullable=True)
    push_submitted_at = Column(DateTime(timezone=True), nullable=True)
    confidence = Column(Float, nullable=False)
    duration = Column(Float, nullable=False)
    location = Column(String, default="Classroom")
    status = Column(String, default="active")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Rich evidence from the edge device (all nullable for backward compat)
    transcribed_text = Column(String, nullable=True)
    detected_words = Column(Text, nullable=True)  # JSON-encoded list[str]
    yamnet_class = Column(String, nullable=True)
    yamnet_score = Column(Float, nullable=True)
    yamnet_ran = Column(Boolean, nullable=True)
    emotion = Column(String, nullable=True)
    rms = Column(Float, nullable=True)
    energy_variance = Column(Float, nullable=True)
    zero_crossing_rate = Column(Float, nullable=True)
    peak_to_average = Column(Float, nullable=True)
    waveform_snapshot = Column(Text, nullable=True)  # JSON-encoded list[int]

    # v2 fields from upgraded Pi payload
    categories = Column(Text, nullable=True)  # JSON-encoded list[str]
    language = Column(
        String(10),
        nullable=False,
        default=LanguageCode.UNKNOWN.value,
        server_default=LanguageCode.UNKNOWN.value,
        index=True,
    )
    language_confidence = Column(Float, nullable=True)
    hard_hits = Column(Text, nullable=True)  # JSON-encoded list[str]
    soft_hits = Column(Text, nullable=True)  # JSON-encoded list[str]
    duration_gate = Column(String(20), nullable=True)  # e.g. "threat", "hard", "repeated"
    required_duration = Column(Float, nullable=True)

    matched_terms = relationship(
        "AlertMatchedTerm",
        back_populates="alert",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="AlertMatchedTerm.id",
    )
    edge_device = relationship("EdgeDevice", back_populates="alerts", lazy="selectin")

    @property
    def device_id(self):
        return self.edge_device_id

    @property
    def device_code(self) -> str | None:
        return self.edge_device.device_code if self.edge_device is not None else None

    @property
    def device_display_name(self) -> str | None:
        return self.edge_device.display_name if self.edge_device is not None else None

    @property
    def classroom_name(self) -> str | None:
        return self.classroom_name_snapshot

    @property
    def school_name(self) -> str | None:
        return self.school_name_snapshot
