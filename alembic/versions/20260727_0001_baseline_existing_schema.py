"""Baseline the schema that predates Alembic.

Existing deployments must be stamped at this revision before applying later
revisions. Fresh local databases may upgrade from base normally.

Revision ID: 20260727_0001
Revises:
"""

from alembic import op
import sqlalchemy as sa

revision = "20260727_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("hashed_password", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("push_token", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_id", "users", ["id"], unique=False)
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("duration", sa.Float(), nullable=False),
        sa.Column("location", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("transcribed_text", sa.String(), nullable=True),
        sa.Column("detected_words", sa.Text(), nullable=True),
        sa.Column("yamnet_class", sa.String(), nullable=True),
        sa.Column("yamnet_score", sa.Float(), nullable=True),
        sa.Column("emotion", sa.String(), nullable=True),
        sa.Column("rms", sa.Float(), nullable=True),
        sa.Column("energy_variance", sa.Float(), nullable=True),
        sa.Column("zero_crossing_rate", sa.Float(), nullable=True),
        sa.Column("peak_to_average", sa.Float(), nullable=True),
        sa.Column("waveform_snapshot", sa.Text(), nullable=True),
        sa.Column("categories", sa.Text(), nullable=True),
        sa.Column("language", sa.String(length=10), nullable=True),
        sa.Column("hard_hits", sa.Text(), nullable=True),
        sa.Column("soft_hits", sa.Text(), nullable=True),
        sa.Column("duration_gate", sa.String(length=20), nullable=True),
        sa.Column("required_duration", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_alerts_id", "alerts", ["id"], unique=False)

    op.create_table(
        "slur_dictionary",
        sa.Column("term_id", sa.Integer(), nullable=False),
        sa.Column("slur_text", sa.String(length=100), nullable=False),
        sa.Column("language", sa.String(length=30), nullable=False),
        sa.Column("severity_weight", sa.Float(), nullable=False),
        sa.Column(
            "added_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("term_id"),
        sa.UniqueConstraint("slur_text"),
    )
    op.create_index(
        "ix_slur_dictionary_term_id",
        "slur_dictionary",
        ["term_id"],
        unique=False,
    )

    op.create_table(
        "system_settings",
        sa.Column("setting_id", sa.Integer(), nullable=False),
        sa.Column("confidence_threshold", sa.Float(), nullable=True),
        sa.Column("aggression_duration_threshold", sa.Float(), nullable=True),
        sa.Column("device_status", sa.String(length=20), nullable=True),
        sa.Column("last_heartbeat", sa.DateTime(), nullable=True),
        sa.Column("vosk_version", sa.String(length=50), nullable=True),
        sa.Column("yamnet_version", sa.String(length=50), nullable=True),
        sa.Column("last_ota_update", sa.DateTime(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("setting_id"),
    )
    op.create_index(
        "ix_system_settings_setting_id",
        "system_settings",
        ["setting_id"],
        unique=False,
    )

    op.create_table(
        "audit_logs",
        sa.Column("log_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("actor_email", sa.String(length=100), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("module", sa.String(length=50), nullable=False),
        sa.Column("target", sa.String(length=100), nullable=True),
        sa.Column(
            "performed_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_audit_logs_user_id_users",
        ),
        sa.PrimaryKeyConstraint("log_id"),
    )
    op.create_index(
        "ix_audit_logs_log_id",
        "audit_logs",
        ["log_id"],
        unique=False,
    )

    op.create_table(
        "reports",
        sa.Column("report_id", sa.Integer(), nullable=False),
        sa.Column("generated_by", sa.Integer(), nullable=True),
        sa.Column("generated_by_email", sa.String(length=100), nullable=True),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("total_incidents", sa.Integer(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["generated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("report_id"),
    )
    op.create_index("ix_reports_report_id", "reports", ["report_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_reports_report_id", table_name="reports")
    op.drop_table("reports")
    op.drop_index("ix_audit_logs_log_id", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index("ix_system_settings_setting_id", table_name="system_settings")
    op.drop_table("system_settings")
    op.drop_index("ix_slur_dictionary_term_id", table_name="slur_dictionary")
    op.drop_table("slur_dictionary")
    op.drop_index("ix_alerts_id", table_name="alerts")
    op.drop_table("alerts")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_id", table_name="users")
    op.drop_table("users")
