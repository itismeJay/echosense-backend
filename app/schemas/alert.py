from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List

class AlertCreate(BaseModel):
    severity: str
    confidence: float
    duration: float
    location: Optional[str] = "Classroom"
    transcribed_text: Optional[str] = None
    detected_words: Optional[List[str]] = None
    yamnet_class: Optional[str] = None
    yamnet_score: Optional[float] = None
    emotion: Optional[str] = None
    rms: Optional[float] = None
    energy_variance: Optional[float] = None
    zero_crossing_rate: Optional[float] = None
    peak_to_average: Optional[float] = None
    waveform_snapshot: Optional[List[int]] = None

class AlertResponse(BaseModel):
    id: int
    severity: str
    confidence: float
    duration: float
    location: str
    status: str
    created_at: datetime
    transcribed_text: Optional[str] = None
    detected_words: Optional[List[str]] = None
    yamnet_class: Optional[str] = None
    yamnet_score: Optional[float] = None
    emotion: Optional[str] = None
    rms: Optional[float] = None
    energy_variance: Optional[float] = None
    zero_crossing_rate: Optional[float] = None
    peak_to_average: Optional[float] = None
    waveform_snapshot: Optional[List[int]] = None

    class Config:
        from_attributes = True

class AlertUpdate(BaseModel):
    status: Optional[str] = None