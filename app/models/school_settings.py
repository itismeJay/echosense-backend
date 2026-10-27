from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Time,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from app.database import Base

DEFAULT_PUSH_LEVELS = ["HIGH", "MEDIUM"]
# ISO weekdays: 1 = Monday ... 7 = Sunday.
DEFAULT_SCHOOL_DAYS = [1, 2, 3, 4, 5]
DEFAULT_SCHOOL_TIMEZONE = "Asia/Manila"
SENSITIVITY_LEVELS = ("normal", "strict")


class SchoolSettings(Base):
    """One row per school: who gets pushed, when, and how strict detection is."""

    __tablename__ = "school_settings"
    __table_args__ = (
        CheckConstraint(
            "push_levels <@ ARRAY['HIGH', 'MEDIUM', 'LOW']::varchar[]",
            name="ck_school_settings_push_levels",
        ),
        CheckConstraint(
            "school_days <@ ARRAY[1, 2, 3, 4, 5, 6, 7]",
            name="ck_school_settings_school_days",
        ),
        CheckConstraint(
            "sensitivity IN ('normal', 'strict')",
            name="ck_school_settings_sensitivity",
        ),
    )

    school_id = Column(
        UUID(as_uuid=True),
        ForeignKey("schools.id", ondelete="CASCADE"),
        primary_key=True,
    )
    push_levels = Column(
        ARRAY(String(10)),
        nullable=False,
        default=lambda: list(DEFAULT_PUSH_LEVELS),
        server_default=text("ARRAY['HIGH', 'MEDIUM']::varchar[]"),
    )
    school_hours_enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    school_days = Column(
        ARRAY(Integer),
        nullable=False,
        default=lambda: list(DEFAULT_SCHOOL_DAYS),
        server_default=text("ARRAY[1, 2, 3, 4, 5]"),
    )
    school_start_time = Column(Time, nullable=False, server_default=text("'07:00'"))
    school_end_time = Column(Time, nullable=False, server_default=text("'17:00'"))
    timezone = Column(
        String(64),
        nullable=False,
        default=DEFAULT_SCHOOL_TIMEZONE,
        server_default=DEFAULT_SCHOOL_TIMEZONE,
    )
    sensitivity = Column(String(10), nullable=False, default="normal", server_default="normal")
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class SchoolTerm(Base):
    """A monitored word one school adds on top of the global dictionary."""

    __tablename__ = "school_terms"
    __table_args__ = (
        CheckConstraint(
            "language IN ('fil', 'ceb', 'en')",
            name="ck_school_terms_language",
        ),
        CheckConstraint(
            "severity_weight >= 0 AND severity_weight <= 1",
            name="ck_school_terms_severity_weight",
        ),
        Index(
            "uq_school_terms_school_normalized_text",
            "school_id",
            text("lower(btrim(term_text))"),
            unique=True,
        ),
    )

    id = Column(Integer, primary_key=True)
    school_id = Column(
        UUID(as_uuid=True),
        ForeignKey("schools.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    term_text = Column(String(100), nullable=False)
    language = Column(String(3), nullable=False)
    severity_weight = Column(Float, nullable=False, default=0.5)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class SchoolDisabledTerm(Base):
    """A global dictionary term one school has turned off."""

    __tablename__ = "school_disabled_terms"

    school_id = Column(
        UUID(as_uuid=True),
        ForeignKey("schools.id", ondelete="CASCADE"),
        primary_key=True,
    )
    term_id = Column(
        Integer,
        ForeignKey("slur_dictionary.term_id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
