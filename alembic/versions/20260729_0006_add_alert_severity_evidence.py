"""Normalize alert severity and add severity evidence.

Revision ID: 20260729_0006
Revises: 20260728_0005
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260729_0006"
down_revision = "20260728_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Refuse to invent a severity for an unsupported historical value.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM alerts
                WHERE severity IS NULL
                   OR upper(btrim(severity)) NOT IN ('LOW', 'MEDIUM', 'HIGH')
            ) THEN
                RAISE EXCEPTION
                    'alerts contains a severity that cannot be safely normalized';
            END IF;
        END
        $$;
        """
    )
    op.execute("UPDATE alerts SET severity = upper(btrim(severity))")
    op.create_check_constraint(
        "ck_alerts_severity",
        "alerts",
        "severity IN ('LOW', 'MEDIUM', 'HIGH')",
    )
    op.add_column(
        "alerts",
        sa.Column(
            "severity_evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("alerts", "severity_evidence")
    op.drop_constraint("ck_alerts_severity", "alerts", type_="check")
    # Restore the former lowercase API/database convention for compatibility
    # with the pre-migration application.
    op.execute("UPDATE alerts SET severity = lower(severity)")
