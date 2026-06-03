from sqlalchemy import Column, Integer, String, Float, DateTime
from sqlalchemy.sql import func
from app.database import Base


class SystemSettings(Base):
    __tablename__ = "system_settings"

    setting_id = Column(Integer, primary_key=True, index=True)
    confidence_threshold = Column(Float, default=0.55)
    aggression_duration_threshold = Column(Float, default=2.0)
    device_status = Column(String(20), default="offline")
    last_heartbeat = Column(DateTime, nullable=True)
    vosk_version = Column(String(50), default="vosk-model-small-en-us-0.15")
    yamnet_version = Column(String(50), default="YAMNet TFLite v1.0")
    last_ota_update = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, server_default=func.now())
