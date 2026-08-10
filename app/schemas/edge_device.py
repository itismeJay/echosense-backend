import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEVICE_KEY_WARNING = "Store this key securely. It will not be shown again."
DEVICE_CODE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,99}$")


def _normalize_required_name(value: str) -> str:
    normalized = " ".join(value.strip().split())
    if not normalized:
        raise ValueError("name fields must not be blank")
    return normalized


class EdgeDeviceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_code: str = Field(min_length=3, max_length=100)
    display_name: str = Field(min_length=1, max_length=200)
    school_id: UUID | None = None
    classroom_id: UUID | None = None
    # Deprecated lookup fields retained for existing administrator clients.
    classroom_name: str | None = Field(default=None, min_length=1, max_length=200)
    school_name: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("device_code")
    @classmethod
    def validate_device_code(cls, value: str) -> str:
        normalized = value.strip()
        if not DEVICE_CODE_PATTERN.fullmatch(normalized):
            raise ValueError(
                "device_code must use lowercase letters, numbers, dots, underscores, or hyphens"
            )
        return normalized

    @field_validator("display_name", "classroom_name", "school_name")
    @classmethod
    def strip_names(cls, value: str | None) -> str | None:
        return _normalize_required_name(value) if value is not None else None

    @model_validator(mode="after")
    def validate_assignment_selector(self):
        uses_ids = self.school_id is not None or self.classroom_id is not None
        uses_names = self.school_name is not None or self.classroom_name is not None
        if uses_ids and uses_names:
            raise ValueError("use classroom_id/school_id or legacy classroom_name/school_name")
        if self.classroom_name is not None and self.school_name is None:
            raise ValueError("school_name is required with classroom_name")
        if self.school_name is not None and self.classroom_name is None:
            raise ValueError("classroom_name is required with school_name")
        return self


class EdgeDeviceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    is_active: bool | None = None
    # Deprecated assignment lookup retained for existing administrator clients.
    classroom_name: str | None = Field(default=None, min_length=1, max_length=200)
    school_name: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("display_name", "classroom_name", "school_name")
    @classmethod
    def strip_names(cls, value: str | None) -> str | None:
        return _normalize_required_name(value) if value is not None else None

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("at least one device field must be provided")
        if "display_name" in self.model_fields_set and self.display_name is None:
            raise ValueError("display_name cannot be null")
        if "is_active" in self.model_fields_set and self.is_active is None:
            raise ValueError("is_active cannot be null")
        name_fields = {"classroom_name", "school_name"} & self.model_fields_set
        if name_fields and (self.classroom_name is None or self.school_name is None):
            raise ValueError("classroom_name and school_name must be provided together")
        return self


class DeviceAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classroom_id: UUID
    expected_current_classroom_id: UUID | None = Field(
        default=None,
        description="Optional optimistic concurrency guard for reassignment.",
    )


class EdgeDeviceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    device_code: str
    display_name: str
    school_id: UUID | None
    school_name: str | None
    classroom_id: UUID | None
    classroom_name: str | None
    assignment_state: Literal["assigned", "unassigned"]
    is_active: bool
    created_at: datetime
    updated_at: datetime
    last_seen_at: datetime | None
    assigned_at: datetime | None
    key_rotated_at: datetime | None


class EdgeDeviceKeyResponse(BaseModel):
    device: EdgeDeviceResponse
    device_key: str = Field(
        description="One-time plaintext credential; it cannot be retrieved later.",
        repr=False,
    )
    warning: Literal["Store this key securely. It will not be shown again."] = DEVICE_KEY_WARNING
