from pydantic import BaseModel, computed_field
from datetime import datetime
from typing import Optional


class SystemSettingsOut(BaseModel):
    setting_id: int
    device_status: str
    last_heartbeat: Optional[datetime] = None
    stt_model_version: str
    yamnet_version: str
    last_ota_update: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # TODO: remove vosk_version once the web and mobile clients read stt_model_version.
    @computed_field(
        description="Deprecated alias of stt_model_version.",
        json_schema_extra={"deprecated": True},
    )
    @property
    def vosk_version(self) -> str:
        return self.stt_model_version

    class Config:
        from_attributes = True
