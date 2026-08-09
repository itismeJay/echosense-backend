import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEVICE_KEY_WARNING = "Store this key securely. It will not be shown again."
DEVICE_CODE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,99}$")


class EdgeDeviceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_code: str = Field(min_length=3, max_length=100)
    display_name: str = Field(min_length=1, max_length=200)
    classroom_name: str = Field(min_length=1, max_length=200)
    school_name: str | None = Field(default=None, max_length=200)

    @field_validator("device_code")
    @classmethod
    def validate_device_code(cls, value: str) -> str:
        normalized = value.strip()
        if not DEVICE_CODE_PATTERN.fullmatch(normalized):
            raise ValueError(
                "device_code must use lowercase letters, numbers, dots, underscores, or hyphens"
            )
        return normalized

    @field_validator("display_name", "classroom_name")
    @classmethod
    def strip_required_names(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name fields must not be blank")
        return normalized

    @field_validator("school_name")
    @classmethod
    def strip_optional_school(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class EdgeDeviceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    classroom_name: str | None = Field(default=None, min_length=1, max_length=200)
    school_name: str | None = Field(default=None, max_length=200)
    is_active: bool | None = None

    @field_validator("display_name", "classroom_name")
    @classmethod
    def strip_updated_names(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("name fields must not be blank")
        return normalized

    @field_validator("school_name")
    @classmethod
    def strip_updated_school(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("at least one device field must be provided")
        for field in ("display_name", "classroom_name", "is_active"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class EdgeDeviceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    device_code: str
    display_name: str
    classroom_name: str
    school_name: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    last_seen_at: datetime | None


class EdgeDeviceKeyResponse(BaseModel):
    device: EdgeDeviceResponse
    device_key: str
    warning: Literal["Store this key securely. It will not be shown again."] = DEVICE_KEY_WARNING
