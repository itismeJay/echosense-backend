"""Add synchronized edge AudioEvent evidence.

Revision ID: 20260728_0005
Revises: 20260728_0004
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260728_0005"
down_revision = "20260728_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "alerts",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "alerts",
        sa.Column("yamnet_ran", sa.Boolean(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_alerts_event_id",
        "alerts",
        ["event_id"],
    )
    op.create_check_constraint(
        "ck_alerts_yamnet_evidence",
        "alerts",
        "yamnet_ran IS NULL OR "
        "(yamnet_ran = false AND yamnet_class IS NOT NULL "
        "AND yamnet_class = 'NotRun' AND yamnet_score IS NOT NULL "
        "AND yamnet_score = 0) OR "
        "(yamnet_ran = true AND yamnet_class IS NOT NULL "
        "AND btrim(yamnet_class) <> '' AND yamnet_score IS NOT NULL "
        "AND lower(btrim(yamnet_class)) <> 'notrun' "
        "AND yamnet_score >= 0 AND yamnet_score <= 1)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_alerts_yamnet_evidence", "alerts", type_="check")
    op.drop_constraint("uq_alerts_event_id", "alerts", type_="unique")
    op.drop_column("alerts", "yamnet_ran")
    op.drop_column("alerts", "event_id")
