from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ClassroomCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    school_id: UUID
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("name must not be blank")
        return normalized


class ClassroomUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("name must not be blank")
        return normalized

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("at least one classroom field must be provided")
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class ClassroomDeviceSummary(BaseModel):
    id: UUID
    device_code: str
    display_name: str
    is_active: bool


class ClassroomResponse(BaseModel):
    id: UUID
    school_id: UUID
    school_name: str
    name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    devices: list[ClassroomDeviceSummary] = Field(default_factory=list)
