"""Store matched terms that are not in the global dictionary instead of rejecting the alert.

Revision ID: 20261105_0014
Revises: 20261027_0013

Before this revision, an alert whose device matched a term missing from slur_dictionary
was answered with 422 and lost on the edge (the "buanga" outbox row, 2026-11-04). Now:
- alert_matched_terms.term_id is nullable;
- source says where the term came from: "global" (term_id set), "school" (school_term_id
  set while that school word exists) or "unlisted" (text only);
- reported_language keeps the device's language for terms that are not linked.

Existing rows are all global links, so they get source = 'global' and nothing else changes.
Downgrade deletes the non-global rows (their alerts are kept), because the old schema
cannot hold them.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261105_0014"
down_revision = "20261027_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "alert_matched_terms",
        sa.Column("source", sa.String(length=20), nullable=False, server_default="global"),
    )
    op.add_column(
        "alert_matched_terms",
        sa.Column("school_term_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "alert_matched_terms",
        sa.Column("reported_language", sa.String(length=3), nullable=True),
    )
    op.create_foreign_key(
        "fk_alert_matched_terms_school_term_id_school_terms",
        "alert_matched_terms",
        "school_terms",
        ["school_term_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_alert_matched_terms_school_term_id",
        "alert_matched_terms",
        ["school_term_id"],
        unique=False,
    )
    op.alter_column("alert_matched_terms", "term_id", existing_type=sa.Integer(), nullable=True)
    op.create_check_constraint(
        "ck_alert_matched_terms_source",
        "alert_matched_terms",
        "source IN ('global', 'school', 'unlisted')",
    )
    op.create_check_constraint(
        "ck_alert_matched_terms_global_has_term",
        "alert_matched_terms",
        "(source = 'global') = (term_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.execute("DELETE FROM alert_matched_terms WHERE term_id IS NULL")
    op.drop_constraint(
        "ck_alert_matched_terms_global_has_term", "alert_matched_terms", type_="check"
    )
    op.drop_constraint("ck_alert_matched_terms_source", "alert_matched_terms", type_="check")
    op.alter_column("alert_matched_terms", "term_id", existing_type=sa.Integer(), nullable=False)
    op.drop_index("ix_alert_matched_terms_school_term_id", table_name="alert_matched_terms")
    op.drop_constraint(
        "fk_alert_matched_terms_school_term_id_school_terms",
        "alert_matched_terms",
        type_="foreignkey",
    )
    op.drop_column("alert_matched_terms", "reported_language")
    op.drop_column("alert_matched_terms", "school_term_id")
    op.drop_column("alert_matched_terms", "source")
