from sqlalchemy import CheckConstraint, Column, DateTime, Float, Integer, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class SlurEntry(Base):
    __tablename__ = "slur_dictionary"
    __table_args__ = (
        CheckConstraint(
            "language IN ('fil', 'ceb', 'en')",
            name="ck_slur_dictionary_language",
        ),
    )

    term_id = Column(Integer, primary_key=True, index=True)
    slur_text = Column(String(100), nullable=False, unique=True)
    language = Column(String(3), nullable=False)
    severity_weight = Column(Float, nullable=False, default=0.5)
    added_at = Column(DateTime, server_default=func.now())

    alert_matches = relationship(
        "AlertMatchedTerm",
        back_populates="dictionary_term",
        passive_deletes=True,
    )
