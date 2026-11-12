from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

WifiCommandAction = Literal["add", "update", "remove", "set_priority", "connect_now"]
WifiCommandStatus = Literal["pending", "applied", "failed"]
MAX_WIFI_PRIORITY = 999


def validate_ssid(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("SSID is required")
    if len(value.encode("utf-8")) > 32:
        raise ValueError("SSID must be at most 32 UTF-8 bytes")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("SSID must not contain control characters")
    return value


def _validate_password(value: str | None) -> str | None:
    """WPA passphrase (8-63 printable ASCII) or 64 hex digits; None/"" means open network."""

    if value is None or value == "":
        return None
    if len(value) == 64 and all(c in "0123456789abcdefABCDEF" for c in value):
        return value
    if not 8 <= len(value) <= 63 or any(not 32 <= ord(c) <= 126 for c in value):
        raise ValueError("Wi-Fi password must be 8-63 printable ASCII characters")
    return value


class WifiNetworkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    ssid: str
    password: str | None = Field(default=None, repr=False)
    priority: int = Field(default=0, ge=0, le=MAX_WIFI_PRIORITY)

    _ssid = field_validator("ssid")(validate_ssid)
    _password = field_validator("password")(_validate_password)


class WifiNetworkUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    # Sent (even null) = change the password; null or "" makes it an open network.
    password: str | None = Field(default=None, repr=False)
    priority: int | None = Field(default=None, ge=0, le=MAX_WIFI_PRIORITY)

    _password = field_validator("password")(_validate_password)

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("send password and/or priority")
        if "priority" in self.model_fields_set and self.priority is None:
            raise ValueError("priority cannot be null")
        return self


class WifiConnectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ssid: str
    _ssid = field_validator("ssid")(validate_ssid)


class WifiNetworkOut(BaseModel):
    ssid: str
    priority: int
    has_password: bool
    last_status: WifiCommandStatus | None
    last_error: str | None
    updated_at: datetime


class WifiCommandOut(BaseModel):
    id: UUID
    action: WifiCommandAction
    ssid: str
    priority: int | None
    status: WifiCommandStatus
    error: str | None
    created_at: datetime
    applied_at: datetime | None


class WifiCurrentConnection(BaseModel):
    ssid: str | None
    wifi_status: Literal["connected", "disconnected"] | None
    signal: int | None
    online: bool
    last_seen_at: datetime | None


class DeviceWifiOut(BaseModel):
    device_id: UUID
    can_manage: bool
    current: WifiCurrentConnection
    # Empty for read-only roles (counselor, teacher).
    networks: list[WifiNetworkOut]
    pending_commands: list[WifiCommandOut]


class EdgeWifiCommand(BaseModel):
    id: UUID
    action: WifiCommandAction
    ssid: str
    priority: int | None
    # Plaintext only on add/update for a secured network, only in this device response.
    password: str | None = Field(default=None, repr=False)
    password_withheld: bool = False
    created_at: datetime


class EdgeWifiCommandsOut(BaseModel):
    commands: list[EdgeWifiCommand]


class EdgeWifiCommandResult(BaseModel):
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)

    status: Literal["applied", "failed"]
    error: str | None = Field(default=None, max_length=500)
    # Sent (even null) = what the device is connected to now; null = disconnected.
    current_ssid: str | None = None

    @field_validator("current_ssid")
    @classmethod
    def _current_ssid(cls, value: str | None) -> str | None:
        return None if value in (None, "") else validate_ssid(value)


class EdgeWifiCommandResultOut(BaseModel):
    id: UUID
    status: WifiCommandStatus
    applied_at: datetime | None
