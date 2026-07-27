from sqlalchemy import Column, Integer, String, Float, DateTime, Text
from sqlalchemy.sql import func
from app.database import Base


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, index=True)
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
    emotion = Column(String, nullable=True)
    rms = Column(Float, nullable=True)
    energy_variance = Column(Float, nullable=True)
    zero_crossing_rate = Column(Float, nullable=True)
    peak_to_average = Column(Float, nullable=True)
    waveform_snapshot = Column(Text, nullable=True)  # JSON-encoded list[int]

    # v2 fields from upgraded Pi payload
    categories = Column(Text, nullable=True)  # JSON-encoded list[str]
    language = Column(String(10), nullable=True)  # e.g. "tl", "ceb", "en"
    hard_hits = Column(Text, nullable=True)  # JSON-encoded list[str]
    soft_hits = Column(Text, nullable=True)  # JSON-encoded list[str]
    duration_gate = Column(String(20), nullable=True)  # e.g. "threat", "hard", "repeated"
    required_duration = Column(Float, nullable=True)
