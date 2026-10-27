from datetime import datetime, time
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from app.languages import DictionaryLanguageCode

PushLevel = Literal["HIGH", "MEDIUM", "LOW"]
Sensitivity = Literal["normal", "strict"]
PUSH_LEVEL_ORDER = ("HIGH", "MEDIUM", "LOW")


class SchoolHours(BaseModel):
    enabled: StrictBool
    # ISO weekdays: 1 = Monday ... 7 = Sunday.
    weekdays: list[int] = Field(min_length=0, max_length=7)
    start: time
    end: time
    timezone: str = Field(default="Asia/Manila", max_length=64)

    @field_validator("weekdays")
    @classmethod
    def validate_weekdays(cls, value: list[int]) -> list[int]:
        if any(day < 1 or day > 7 for day in value):
            raise ValueError("weekdays must be ISO weekdays 1 (Monday) to 7 (Sunday)")
        return sorted(set(value))

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be an IANA time zone such as Asia/Manila") from exc
        return value

    @field_validator("start", "end")
    @classmethod
    def strip_seconds(cls, value: time) -> time:
        return value.replace(second=0, microsecond=0, tzinfo=None)


class SchoolSettingsOut(BaseModel):
    school_id: UUID
    push_levels: list[PushLevel]
    school_hours: SchoolHours
    sensitivity: Sensitivity
    updated_at: datetime | None = None


class SchoolSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    push_levels: list[PushLevel] | None = Field(default=None, max_length=3)
    school_hours: SchoolHours | None = None
    sensitivity: Sensitivity | None = None

    @field_validator("push_levels")
    @classmethod
    def order_push_levels(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return [level for level in PUSH_LEVEL_ORDER if level in set(value)]


class GlobalTermForSchool(BaseModel):
    term_id: int
    text: str
    language: DictionaryLanguageCode
    severity_weight: float
    enabled_for_school: bool


class SchoolTermOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    text: str = Field(validation_alias="term_text")
    language: DictionaryLanguageCode
    severity_weight: float
    created_at: datetime


class SchoolTermsOut(BaseModel):
    school_id: UUID
    global_terms: list[GlobalTermForSchool]
    school_terms: list[SchoolTermOut]


class SchoolTermCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    text: str = Field(min_length=1, max_length=100)
    language: DictionaryLanguageCode
    severity_weight: float = Field(default=0.5, ge=0.0, le=1.0)


class GlobalTermToggle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool


class DeviceConfigTerm(BaseModel):
    text: str
    language: DictionaryLanguageCode
    severity_weight: float
    source: Literal["global", "school"]
    term_id: int


class DeviceConfigOut(BaseModel):
    schema_version: Literal[1] = 1
    device_code: str
    school_id: UUID
    sensitivity: Sensitivity
    terms: list[DeviceConfigTerm]
    config_version: str
    generated_at: datetime
