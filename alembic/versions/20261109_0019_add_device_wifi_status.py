"""Store non-secret Wi-Fi status reported by authenticated edge devices.

Revision ID: 20261109_0019
Revises: 20261105_0018

Only connection status, SSID, and the private Pi-local portal URL are stored.
Wi-Fi credentials remain exclusively on the Pi in NetworkManager.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261109_0019"
down_revision = "20261105_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("edge_devices", sa.Column("wifi_status", sa.String(20), nullable=True))
    op.add_column("edge_devices", sa.Column("wifi_ssid", sa.String(32), nullable=True))
    op.add_column("edge_devices", sa.Column("wifi_local_url", sa.String(255), nullable=True))
    op.create_check_constraint(
        "ck_edge_devices_wifi_status",
        "edge_devices",
        "wifi_status IS NULL OR wifi_status IN ('connected', 'disconnected')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_edge_devices_wifi_status", "edge_devices", type_="check")
    op.drop_column("edge_devices", "wifi_local_url")
    op.drop_column("edge_devices", "wifi_ssid")
    op.drop_column("edge_devices", "wifi_status")
