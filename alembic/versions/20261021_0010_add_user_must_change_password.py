"""Add a must-change-password flag for administrator-issued temporary passwords.

Revision ID: 20261021_0010
Revises: 20260810_0009

Additive only: existing rows and inserts from code that does not know the column
receive the server default FALSE, so current accounts are unaffected.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261021_0010"
down_revision = "20260810_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "must_change_password")
