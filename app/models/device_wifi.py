from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from app.database import Base

WIFI_COMMAND_ACTIONS = ("add", "update", "remove", "set_priority", "connect_now")
WIFI_COMMAND_STATUSES = ("pending", "applied", "failed")


class DeviceWifiNetwork(Base):
    """A Wi-Fi network the dashboard wants a device to know. Password is Fernet ciphertext."""

    __tablename__ = "device_wifi_networks"
    __table_args__ = (
        UniqueConstraint("device_id", "ssid", name="uq_device_wifi_networks_device_ssid"),
        CheckConstraint(
            "priority >= 0 AND priority <= 999", name="ck_device_wifi_networks_priority"
        ),
        CheckConstraint(
            "last_status IS NULL OR last_status IN ('pending', 'applied', 'failed')",
            name="ck_device_wifi_networks_last_status",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    device_id = Column(
        UUID(as_uuid=True), ForeignKey("edge_devices.id", ondelete="CASCADE"), nullable=False
    )
    ssid = Column(String(32), nullable=False)
    priority = Column(Integer, nullable=False, default=0, server_default="0")
    password_encrypted = Column(Text, nullable=True)
    last_status = Column(String(20), nullable=True)
    last_error = Column(String(500), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:  # never include the ciphertext
        return f"<DeviceWifiNetwork device={self.device_id} ssid={self.ssid!r}>"


class DeviceWifiCommand(Base):
    """A queued change for the edge. payload never holds secrets; the password (add/update
    only) is Fernet ciphertext in password_encrypted, cleared once the edge reports back."""

    __tablename__ = "device_wifi_commands"
    __table_args__ = (
        CheckConstraint(
            "action IN ('add', 'update', 'remove', 'set_priority', 'connect_now')",
            name="ck_device_wifi_commands_action",
        ),
        CheckConstraint(
            "status IN ('pending', 'applied', 'failed')", name="ck_device_wifi_commands_status"
        ),
        Index("ix_device_wifi_commands_device_status", "device_id", "status", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    device_id = Column(
        UUID(as_uuid=True), ForeignKey("edge_devices.id", ondelete="CASCADE"), nullable=False
    )
    action = Column(String(20), nullable=False)
    ssid = Column(String(32), nullable=False)
    payload = Column(JSONB, nullable=False, default=dict, server_default="{}")
    password_encrypted = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="pending", server_default="pending")
    error = Column(String(500), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    applied_at = Column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:  # never include the ciphertext
        return f"<DeviceWifiCommand {self.id} {self.action} ssid={self.ssid!r} {self.status}>"
