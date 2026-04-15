from sqlalchemy import Column, Integer, String, Float, DateTime
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