from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
)

from app.services.audit import sanitise_metadata


class AuditLogOut(BaseModel):
    id: str
    occurred_at: Optional[datetime] = None
    actor_user_id: Optional[str] = None
    actor_email: Optional[str] = None
    actor_role: Optional[str] = None
    action: str
    resource: str
    resource_id: Optional[str] = None
    target: Optional[str] = None
    status: Optional[str] = None
    description: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    request_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("metadata_json", "metadata"),
    )
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("id", "actor_user_id", mode="before")
    @classmethod
    def stringify_identifiers(cls, value):
        return None if value is None else str(value)

    @field_validator("metadata", mode="before")
    @classmethod
    def protect_metadata(cls, value):
        return sanitise_metadata(value if isinstance(value, dict) else {})

    @field_serializer("occurred_at", "created_at")
    def serialise_utc_datetime(self, value: Optional[datetime]):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class AuditLogPage(BaseModel):
    items: list[AuditLogOut]
    page: int
    page_size: int
    total: int
    total_pages: int
