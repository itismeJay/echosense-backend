"""Remote Wi-Fi scan for edge devices.

Revision ID: 20261113_0023
Revises: 20261113_0022

Adds the 'scan' Wi-Fi command action, the device's latest scan result (networks list with
ssid/signal/security/known only; never passwords) and whether the device supports remote
scans. Downgrade deletes scan commands before restoring the old action constraint.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261113_0023"
down_revision = "20261113_0022"
branch_labels = None
depends_on = None

OLD_ACTIONS = "action IN ('add', 'update', 'remove', 'set_priority', 'connect_now')"
NEW_ACTIONS = "action IN ('add', 'update', 'remove', 'set_priority', 'connect_now', 'scan')"


def upgrade() -> None:
    op.add_column(
        "edge_devices",
        sa.Column(
            "wifi_remote_scan_supported",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column("edge_devices", sa.Column("wifi_last_scan", postgresql.JSONB(), nullable=True))
    op.add_column(
        "edge_devices",
        sa.Column("wifi_last_scan_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_constraint("ck_device_wifi_commands_action", "device_wifi_commands", type_="check")
    op.create_check_constraint(
        "ck_device_wifi_commands_action", "device_wifi_commands", NEW_ACTIONS
    )


def downgrade() -> None:
    op.execute("DELETE FROM device_wifi_commands WHERE action = 'scan'")
    op.drop_constraint("ck_device_wifi_commands_action", "device_wifi_commands", type_="check")
    op.create_check_constraint(
        "ck_device_wifi_commands_action", "device_wifi_commands", OLD_ACTIONS
    )
    op.drop_column("edge_devices", "wifi_last_scan_at")
    op.drop_column("edge_devices", "wifi_last_scan")
    op.drop_column("edge_devices", "wifi_remote_scan_supported")
