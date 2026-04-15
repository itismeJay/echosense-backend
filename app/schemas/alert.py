from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class AlertCreate(BaseModel):
    severity: str
    confidence: float
    duration: float
    location: Optional[str] = "Classroom"

class AlertResponse(BaseModel):
    id: int
    severity: str
    confidence: float
    duration: float
    location: str
    status: str
    created_at: datetime

    class Config:
        from_attributes = True

class AlertUpdate(BaseModel):
    status: Optional[str] = None