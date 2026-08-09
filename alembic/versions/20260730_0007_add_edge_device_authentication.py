"""Add authenticated edge devices and alert classroom assignments.

Revision ID: 20260730_0007
Revises: 20260729_0006
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260730_0007"
down_revision = "20260729_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "edge_devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_code", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("classroom_name", sa.String(length=200), nullable=False),
        sa.Column("school_name", sa.String(length=200), nullable=True),
        sa.Column("api_key_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_code", name="uq_edge_devices_device_code"),
    )
    op.add_column(
        "alerts",
        sa.Column("edge_device_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "alerts",
        sa.Column("classroom_name_snapshot", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "alerts",
        sa.Column("school_name_snapshot", sa.String(length=200), nullable=True),
    )
    op.create_index(
        "ix_alerts_edge_device_id",
        "alerts",
        ["edge_device_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_alerts_edge_device_id_edge_devices",
        "alerts",
        "edge_devices",
        ["edge_device_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_alerts_edge_device_id_edge_devices",
        "alerts",
        type_="foreignkey",
    )
    op.drop_index("ix_alerts_edge_device_id", table_name="alerts")
    op.drop_column("alerts", "school_name_snapshot")
    op.drop_column("alerts", "classroom_name_snapshot")
    op.drop_column("alerts", "edge_device_id")
    op.drop_table("edge_devices")
