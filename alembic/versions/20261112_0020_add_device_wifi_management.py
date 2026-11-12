"""Dashboard-managed Wi-Fi networks and a per-device Wi-Fi command queue.

Revision ID: 20261112_0020
Revises: 20261109_0019

device_wifi_networks holds each device's saved networks. Passwords are stored only as
Fernet ciphertext (key from ECHOSENSE_WIFI_SECRET_KEY, never in the database).
device_wifi_commands queues add/update/remove/set_priority/connect_now for the edge to
pull with its device key; a command's ciphertext is cleared once the edge reports a result.
edge_devices.wifi_signal stores the non-secret signal strength (0-100) from heartbeats.

Purely additive: no existing row is changed. Downgrade drops the new tables and column.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261112_0020"
down_revision = "20261109_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("edge_devices", sa.Column("wifi_signal", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_edge_devices_wifi_signal",
        "edge_devices",
        "wifi_signal IS NULL OR (wifi_signal >= 0 AND wifi_signal <= 100)",
    )

    op.create_table(
        "device_wifi_networks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("edge_devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ssid", sa.String(32), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("password_encrypted", sa.Text(), nullable=True),
        sa.Column("last_status", sa.String(20), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("device_id", "ssid", name="uq_device_wifi_networks_device_ssid"),
        sa.CheckConstraint(
            "priority >= 0 AND priority <= 999", name="ck_device_wifi_networks_priority"
        ),
        sa.CheckConstraint(
            "last_status IS NULL OR last_status IN ('pending', 'applied', 'failed')",
            name="ck_device_wifi_networks_last_status",
        ),
    )

    op.create_table(
        "device_wifi_commands",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("edge_devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("ssid", sa.String(32), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("password_encrypted", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "action IN ('add', 'update', 'remove', 'set_priority', 'connect_now')",
            name="ck_device_wifi_commands_action",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'applied', 'failed')",
            name="ck_device_wifi_commands_status",
        ),
    )
    op.create_index(
        "ix_device_wifi_commands_device_status",
        "device_wifi_commands",
        ["device_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_device_wifi_commands_device_status", table_name="device_wifi_commands")
    op.drop_table("device_wifi_commands")
    op.drop_table("device_wifi_networks")
    op.drop_constraint("ck_edge_devices_wifi_signal", "edge_devices", type_="check")
    op.drop_column("edge_devices", "wifi_signal")
