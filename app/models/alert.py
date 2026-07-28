from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
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
    )

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(UUID(as_uuid=True), nullable=True)
    severity = Column(String, nullable=False)
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
