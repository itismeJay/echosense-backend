from pydantic import BaseModel
from datetime import date, datetime
from typing import Optional


class ReportCreate(BaseModel):
    date_from: date
    date_to: date


class ReportOut(BaseModel):
    report_id: int
    generated_by: Optional[int] = None
    generated_by_email: Optional[str] = None
    date_from: date
    date_to: date
    total_incidents: int
    generated_at: datetime

    class Config:
        from_attributes = True
