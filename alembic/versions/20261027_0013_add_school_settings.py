"""Per-school settings, school terms and disabled global terms; alerts.outside_school_hours.

Revision ID: 20261027_0013
Revises: 20261021_0012

Replaces the global confidence/duration sliders (never read by the edge) with settings a
school actually controls:
- school_settings: one row per school (push levels, school hours, sensitivity). Every
  existing school gets a row with the defaults; new schools get one on creation.
- school_terms: extra monitored words for one school.
- school_disabled_terms: global dictionary terms a school has turned off.
- alerts.outside_school_hours: the alert was stored but not pushed because it happened
  outside the school's hours.

system_settings.confidence_threshold and aggression_duration_threshold are kept in the
database (removing them is not needed to drop them from the API) so a rollback of the
code alone stays safe.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261027_0013"
down_revision = "20261021_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "school_settings",
        sa.Column(
            "school_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("schools.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "push_levels",
            postgresql.ARRAY(sa.String(10)),
            nullable=False,
            server_default=sa.text("ARRAY['HIGH', 'MEDIUM']::varchar[]"),
        ),
        sa.Column(
            "school_hours_enabled", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "school_days",
            postgresql.ARRAY(sa.Integer()),
            nullable=False,
            server_default=sa.text("ARRAY[1, 2, 3, 4, 5]"),
        ),
        sa.Column(
            "school_start_time", sa.Time(), nullable=False, server_default=sa.text("'07:00'")
        ),
        sa.Column(
            "school_end_time", sa.Time(), nullable=False, server_default=sa.text("'17:00'")
        ),
        sa.Column(
            "timezone", sa.String(64), nullable=False, server_default="Asia/Manila"
        ),
        sa.Column("sensitivity", sa.String(10), nullable=False, server_default="normal"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "push_levels <@ ARRAY['HIGH', 'MEDIUM', 'LOW']::varchar[]",
            name="ck_school_settings_push_levels",
        ),
        sa.CheckConstraint(
            "school_days <@ ARRAY[1, 2, 3, 4, 5, 6, 7]",
            name="ck_school_settings_school_days",
        ),
        sa.CheckConstraint(
            "sensitivity IN ('normal', 'strict')",
            name="ck_school_settings_sensitivity",
        ),
    )
    op.execute(
        """
        INSERT INTO school_settings (school_id)
        SELECT id FROM schools
        ON CONFLICT (school_id) DO NOTHING
        """
    )

    op.create_table(
        "school_terms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "school_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("schools.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("term_text", sa.String(100), nullable=False),
        sa.Column("language", sa.String(3), nullable=False),
        sa.Column("severity_weight", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "language IN ('fil', 'ceb', 'en')", name="ck_school_terms_language"
        ),
        sa.CheckConstraint(
            "severity_weight >= 0 AND severity_weight <= 1",
            name="ck_school_terms_severity_weight",
        ),
    )
    op.create_index("ix_school_terms_school_id", "school_terms", ["school_id"])
    op.create_index(
        "uq_school_terms_school_normalized_text",
        "school_terms",
        ["school_id", sa.text("lower(btrim(term_text))")],
        unique=True,
    )

    op.create_table(
        "school_disabled_terms",
        sa.Column(
            "school_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("schools.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "term_id",
            sa.Integer(),
            sa.ForeignKey("slur_dictionary.term_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.add_column(
        "alerts",
        sa.Column(
            "outside_school_hours", sa.Boolean(), nullable=False, server_default="false"
        ),
    )


def downgrade() -> None:
    op.drop_column("alerts", "outside_school_hours")
    op.drop_table("school_disabled_terms")
    op.drop_index("uq_school_terms_school_normalized_text", table_name="school_terms")
    op.drop_index("ix_school_terms_school_id", table_name="school_terms")
    op.drop_table("school_terms")
    op.drop_table("school_settings")
