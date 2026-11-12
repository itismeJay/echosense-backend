"""Push LOW alerts by default.

Revision ID: 20261113_0022
Revises: 20261113_0021

school_settings.push_levels now defaults to HIGH, MEDIUM and LOW. Schools still on the old
default (exactly HIGH, MEDIUM) get LOW added; any school whose admin chose other levels is
left unchanged. Idempotent. Downgrade restores the old default and removes LOW only from
schools that are exactly on the new default.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261113_0022"
down_revision = "20261113_0021"
branch_labels = None
depends_on = None

OLD = "ARRAY['HIGH', 'MEDIUM']::varchar[]"
NEW = "ARRAY['HIGH', 'MEDIUM', 'LOW']::varchar[]"


def upgrade() -> None:
    op.alter_column("school_settings", "push_levels", server_default=sa.text(NEW))
    op.execute(f"UPDATE school_settings SET push_levels = {NEW} WHERE push_levels = {OLD}")


def downgrade() -> None:
    op.execute(f"UPDATE school_settings SET push_levels = {OLD} WHERE push_levels = {NEW}")
    op.alter_column("school_settings", "push_levels", server_default=sa.text(OLD))
