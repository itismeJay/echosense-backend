from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.edge_device import EdgeDeviceResponse

SETUP_SECRET_WARNING = (
    "Print the label and write identity.json now. The setup code and box secret "
    "will not be shown again."
)


def _strip_device_code(value: str) -> str:
    return str(value or "").strip().lower()


class ProvisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(default=1, ge=1, le=50)
    display_name: str | None = Field(default=None, min_length=1, max_length=150)


class ProvisionedBox(BaseModel):
    device: EdgeDeviceResponse
    device_code: str
    claim_code: str = Field(repr=False, description="One-time setup code (in the QR).")
    box_secret: str = Field(repr=False, description="Only for the box's identity.json.")
    qr_payload: str = Field(repr=False)


class ProvisionResponse(BaseModel):
    boxes: list[ProvisionedBox]
    warning: str = SETUP_SECRET_WARNING


class ClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_code: str = Field(min_length=3, max_length=100)
    claim_code: str = Field(min_length=1, max_length=64, repr=False)
    display_name: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("device_code")
    @classmethod
    def _normalize_code(cls, value: str) -> str:
        return _strip_device_code(value)

    @field_validator("display_name")
    @classmethod
    def _strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("display_name must not be blank")
        return normalized


class ActivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_code: str = Field(min_length=3, max_length=100)
    claim_code: str = Field(min_length=1, max_length=64, repr=False)
    box_secret: str = Field(min_length=1, max_length=128, repr=False)

    @field_validator("device_code")
    @classmethod
    def _normalize_code(cls, value: str) -> str:
        return _strip_device_code(value)


class ActivateResponse(BaseModel):
    status: Literal["waiting", "active"]
    device_code: str
    device_key: str | None = Field(default=None, repr=False)
    school_name: str | None = None
    classroom_name: str | None = None
    message: str


class MoveDeviceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classroom_id: UUID


class RemovedDeviceResponse(BaseModel):
    id: UUID
    device_code: str
    status: Literal["removed"] = "removed"
    message: str = "Device removed. Its key no longer works."
