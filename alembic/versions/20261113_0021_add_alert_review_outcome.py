"""Alert review outcome and note; per-outcome counts on reports.

Revision ID: 20261113_0021
Revises: 20261112_0020

alerts.review_outcome records what a reviewer concluded when marking an alert reviewed or
resolved (confirmed_bullying, false_alert, joking_banter, misheard); alerts.review_note is an
optional short note. reports.outcome_counts snapshots the per-outcome alert counts.

Purely additive: no existing row is changed. Downgrade drops the new columns.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261113_0021"
down_revision = "20261112_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("alerts", sa.Column("review_outcome", sa.String(30), nullable=True))
    op.add_column("alerts", sa.Column("review_note", sa.String(500), nullable=True))
    op.create_check_constraint(
        "ck_alerts_review_outcome",
        "alerts",
        "review_outcome IS NULL OR review_outcome IN "
        "('confirmed_bullying', 'false_alert', 'joking_banter', 'misheard')",
    )
    op.add_column("reports", sa.Column("outcome_counts", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("reports", "outcome_counts")
    op.drop_constraint("ck_alerts_review_outcome", "alerts", type_="check")
    op.drop_column("alerts", "review_note")
    op.drop_column("alerts", "review_outcome")
