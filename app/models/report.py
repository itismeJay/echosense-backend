from sqlalchemy import Column, Integer, String, Date, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.database import Base


class Report(Base):
    __tablename__ = "reports"

    report_id = Column(Integer, primary_key=True, index=True)
    generated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    generated_by_email = Column(String(100), nullable=True)
    date_from = Column(Date, nullable=False)
    date_to = Column(Date, nullable=False)
    total_incidents = Column(Integer, nullable=False)
    generated_at = Column(DateTime, server_default=func.now())
