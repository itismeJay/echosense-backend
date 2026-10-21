import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# Deliberately simple: one "@", no whitespace, and a dotted domain. EmailStr is not used
# because email-validator rejects the special-use .local/.test domains used locally.
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s.]+$")
PASSWORD_MIN_LENGTH = 8
# bcrypt only hashes the first 72 bytes and newer releases reject longer input.
PASSWORD_MAX_BYTES = 72


def normalize_email(value: str) -> str:
    return value.strip().lower()


def validate_new_password(value: str) -> str:
    if len(value) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"password must be at least {PASSWORD_MIN_LENGTH} characters")
    if len(value.encode("utf-8")) > PASSWORD_MAX_BYTES:
        raise ValueError(f"password must be at most {PASSWORD_MAX_BYTES} bytes")
    return value


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def strip_email(cls, value: str) -> str:
        return value.strip()


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    role: str
    school_id: UUID | None = None
    is_super_admin: bool = False
    must_change_password: bool = False

    @field_validator("id", mode="before")
    @classmethod
    def stringify_id(cls, value) -> str:
        return str(value)


class RegisterRequest(BaseModel):
    email: str = Field(max_length=254)
    password: str
    role: Literal["admin", "staff", "counselor"] = "staff"
    school_id: UUID | None = None
    is_super_admin: bool = False

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = normalize_email(value)
        if not EMAIL_PATTERN.fullmatch(normalized):
            raise ValueError("email must be a valid email address")
        return normalized

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return validate_new_password(value)

    @model_validator(mode="after")
    def validate_super_admin_role(self):
        if self.is_super_admin and self.role != "admin":
            raise ValueError("is_super_admin requires role=admin")
        return self


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    must_change_password: bool = False
    user: UserOut


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return validate_new_password(value)

    @model_validator(mode="after")
    def require_different_password(self):
        if self.new_password == self.current_password:
            raise ValueError("new_password must differ from current_password")
        return self


class PushTokenRequest(BaseModel):
    token: str


class NotificationRecipientAudit(BaseModel):
    controlled_test_mode: bool
    configured_user_reference_present: bool
    configured_recipient_resolved: bool
    selected_recipient_count: int
    eligible_recipient_count: int
    recipient_identifier_masked: str | None
    recipient_internal_id: str | None
    masked_email: str | None
    role: str | None
    account_active_status: Literal["active", "inactive", "not_recorded"]
    has_push_token: bool
    token_structurally_valid: bool
    token_provider: Literal["expo", "unknown", "not_recorded"]
    token_duplicate_count: int
    token_last_updated: Literal["not_recorded"]
    token_stale_status: Literal["not_recorded"]
    selected_recipient_source: Literal["controlled_user", "fallback", "broadcast", "none"]
    broadcast_risk: bool
    failure_reason: str | None = None


class ProviderTestDryRunRequest(BaseModel):
    confirmed_recipient_user_id: int = Field(gt=0)
    physical_device_confirmed: bool


class ProviderTestSendRequest(ProviderTestDryRunRequest):
    test_id: str = Field(min_length=1, max_length=128)
    approve_single_send: bool


class ProviderTestApplicationData(BaseModel):
    type: Literal["provider_test"]
    test_id: str
    route: Literal["/notifications/test"]
    severity: Literal["LOW"]
    is_test: Literal[True]


class ProviderTestPayloadPreview(BaseModel):
    title: Literal["EchoSense notification test"]
    body: Literal[
        "This is a controlled delivery test for the approved device. "
        "No classroom alert was created."
    ]
    priority: Literal["normal"]
    channel_id: Literal["echosense-alerts"] = Field(alias="channelId")
    data: ProviderTestApplicationData


class ProviderTestDryRunResponse(BaseModel):
    test_id: str
    controlled_test_mode: bool
    recipient_internal_id: str
    masked_email: str
    role: str
    account_active_status: Literal["active", "inactive", "not_recorded"]
    token_present: bool
    token_structurally_valid: bool
    token_provider: Literal["expo", "unknown", "not_recorded"]
    token_duplicate_count: int
    recipient_count: Literal[1]
    payload: ProviderTestPayloadPreview
    payload_data_keys: list[str]
    expected_provider_submissions: Literal[1]
    expected_recipients: Literal[1]
    expected_alert_rows: Literal[0]
    expected_event_ids: Literal[0]
    expected_classroom_analytics_writes: Literal[0]


class ProviderTestSendResponse(BaseModel):
    submission_timestamp: datetime
    test_id: str
    masked_recipient: str
    selected_recipient_count: Literal[1]
    provider_http_status: int | None
    provider_classification: Literal[
        "accepted",
        "rejected",
        "invalid_token",
        "rate_limited",
        "temporary_failure",
        "unknown",
    ]
    provider_ticket_id_redacted: str | None
    message_count: Literal[1]
    result_message: str
