from pydantic import BaseModel, Field
from datetime import datetime


class SlurCreate(BaseModel):
    slur_text: str
    language: str
    severity_weight: float = Field(..., ge=0.0, le=1.0)


class SlurEntry(BaseModel):
    term_id: int
    slur_text: str
    language: str
    severity_weight: float
    added_at: datetime

    class Config:
        from_attributes = True
