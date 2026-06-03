from sqlalchemy import Column, Integer, String, Float, DateTime
from sqlalchemy.sql import func
from app.database import Base


class SlurEntry(Base):
    __tablename__ = "slur_dictionary"

    term_id = Column(Integer, primary_key=True, index=True)
    slur_text = Column(String(100), nullable=False, unique=True)
    language = Column(String(30), nullable=False)
    severity_weight = Column(Float, nullable=False, default=0.5)
    added_at = Column(DateTime, server_default=func.now())
