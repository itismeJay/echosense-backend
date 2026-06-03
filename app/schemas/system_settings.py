from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class SystemSettingsOut(BaseModel):
    setting_id: int
    confidence_threshold: float
    aggression_duration_threshold: float
    device_status: str
    last_heartbeat: Optional[datetime] = None
    vosk_version: str
    yamnet_version: str
    last_ota_update: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class SystemSettingsUpdate(BaseModel):
    confidence_threshold: Optional[float] = None
    aggression_duration_threshold: Optional[float] = None
