"""Add validated alert languages and monitored-term evidence.

Revision ID: 20260728_0004
Revises: 20260727_0003
"""

from alembic import op
import sqlalchemy as sa

revision = "20260728_0004"
down_revision = "20260727_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "alerts",
        sa.Column("language_confidence", sa.Float(), nullable=True),
    )
    op.execute(
        """
        UPDATE alerts
        SET language = CASE
            WHEN language IS NULL OR btrim(language) = '' THEN 'unknown'
            WHEN lower(btrim(language)) IN ('fil', 'filipino', 'tagalog', 'tl') THEN 'fil'
            WHEN lower(btrim(language)) IN ('ceb', 'bisaya', 'cebuano', 'visayan') THEN 'ceb'
            WHEN lower(btrim(language)) IN ('en', 'english') THEN 'en'
            WHEN lower(btrim(language)) = 'mixed' THEN 'mixed'
            ELSE 'unknown'
        END
        """
    )
    op.alter_column(
        "alerts",
        "language",
        existing_type=sa.String(length=10),
        nullable=False,
        server_default="unknown",
    )
    op.create_check_constraint(
        "ck_alerts_language",
        "alerts",
        "language IN ('fil', 'ceb', 'en', 'mixed', 'unknown')",
    )
    op.create_check_constraint(
        "ck_alerts_language_confidence",
        "alerts",
        "language_confidence IS NULL OR (language_confidence >= 0 AND language_confidence <= 1)",
    )
    op.create_index("ix_alerts_language", "alerts", ["language"], unique=False)

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM slur_dictionary
                WHERE lower(btrim(language)) NOT IN (
                    'fil', 'filipino', 'tagalog', 'tl',
                    'ceb', 'bisaya', 'cebuano', 'visayan',
                    'en', 'english'
                )
            ) THEN
                RAISE EXCEPTION
                    'slur_dictionary contains a language that cannot be safely normalized';
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        UPDATE slur_dictionary
        SET language = CASE
            WHEN lower(btrim(language)) IN ('fil', 'filipino', 'tagalog', 'tl') THEN 'fil'
            WHEN lower(btrim(language)) IN ('ceb', 'bisaya', 'cebuano', 'visayan') THEN 'ceb'
            WHEN lower(btrim(language)) IN ('en', 'english') THEN 'en'
        END
        """
    )
    op.alter_column(
        "slur_dictionary",
        "language",
        existing_type=sa.String(length=30),
        type_=sa.String(length=3),
        existing_nullable=False,
    )
    op.create_check_constraint(
        "ck_slur_dictionary_language",
        "slur_dictionary",
        "language IN ('fil', 'ceb', 'en')",
    )

    op.create_table(
        "alert_matched_terms",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("alert_id", sa.Integer(), nullable=False),
        sa.Column("term_id", sa.Integer(), nullable=False),
        sa.Column("matched_text", sa.String(length=100), nullable=False),
        sa.Column(
            "match_type",
            sa.String(length=30),
            server_default="exact",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["alert_id"],
            ["alerts.id"],
            name="fk_alert_matched_terms_alert_id_alerts",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["term_id"],
            ["slur_dictionary.term_id"],
            name="fk_alert_matched_terms_term_id_slur_dictionary",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "alert_id",
            "term_id",
            name="uq_alert_matched_terms_alert_id_term_id",
        ),
    )
    op.create_index(
        "ix_alert_matched_terms_alert_id",
        "alert_matched_terms",
        ["alert_id"],
        unique=False,
    )
    op.create_index(
        "ix_alert_matched_terms_term_id",
        "alert_matched_terms",
        ["term_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_alert_matched_terms_term_id",
        table_name="alert_matched_terms",
    )
    op.drop_index(
        "ix_alert_matched_terms_alert_id",
        table_name="alert_matched_terms",
    )
    op.drop_table("alert_matched_terms")

    op.drop_index("ix_alerts_language", table_name="alerts")
    op.drop_constraint(
        "ck_slur_dictionary_language",
        "slur_dictionary",
        type_="check",
    )
    op.alter_column(
        "slur_dictionary",
        "language",
        existing_type=sa.String(length=3),
        type_=sa.String(length=30),
        existing_nullable=False,
    )
    op.execute(
        """
        UPDATE slur_dictionary
        SET language = CASE language
            WHEN 'fil' THEN 'Filipino'
            WHEN 'ceb' THEN 'Bisaya'
            WHEN 'en' THEN 'English'
        END
        """
    )

    op.drop_constraint("ck_alerts_language_confidence", "alerts", type_="check")
    op.drop_constraint("ck_alerts_language", "alerts", type_="check")
    op.alter_column(
        "alerts",
        "language",
        existing_type=sa.String(length=10),
        nullable=True,
        server_default=None,
    )
    op.drop_column("alerts", "language_confidence")
