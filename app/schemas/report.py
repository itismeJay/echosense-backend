from pydantic import BaseModel
from datetime import date, datetime
from typing import Dict, Optional


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
    # Alerts in range per review outcome, plus "not_set"; null for reports made before 0021.
    outcome_counts: Optional[Dict[str, int]] = None
    generated_at: datetime

    class Config:
        from_attributes = True
