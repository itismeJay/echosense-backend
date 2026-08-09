"""Persist the finalized Phase 2 event contract and push-attempt state.

Revision ID: 20260804_0008
Revises: 20260730_0007
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260804_0008"
down_revision = "20260730_0007"
branch_labels = None
depends_on = None


def _jsonb_column(name: str) -> sa.Column:
    return sa.Column(name, postgresql.JSONB(astext_type=sa.Text()), nullable=True)


def upgrade() -> None:
    op.add_column("alerts", sa.Column("schema_version", sa.Integer(), nullable=True))
    op.add_column("alerts", sa.Column("trigger_type", sa.String(length=20), nullable=True))
    op.add_column("alerts", _jsonb_column("severity_reasons"))
    op.add_column("alerts", sa.Column("review_message", sa.Text(), nullable=True))
    op.add_column("alerts", sa.Column("device_identifier", sa.String(length=100), nullable=True))
    op.add_column("alerts", _jsonb_column("device_source"))
    op.add_column(
        "alerts",
        sa.Column("event_start_timestamp", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "alerts",
        sa.Column("event_end_timestamp", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("alerts", _jsonb_column("monitored_terms"))
    op.add_column("alerts", sa.Column("monitored_word_detected", sa.Boolean(), nullable=True))
    op.add_column("alerts", _jsonb_column("monitored_word_occurrences"))
    op.add_column("alerts", _jsonb_column("acoustic_trigger_evidence"))
    op.add_column("alerts", _jsonb_column("detailed_acoustic_evidence"))
    op.add_column("alerts", _jsonb_column("tone_evidence"))
    op.add_column("alerts", _jsonb_column("repetition_evidence"))
    op.add_column("alerts", _jsonb_column("direct_address_evidence"))
    op.add_column("alerts", _jsonb_column("laughter_context"))
    op.add_column("alerts", sa.Column("transcription_status", sa.String(length=100), nullable=True))
    op.add_column("alerts", _jsonb_column("processing_latency"))
    op.add_column("alerts", _jsonb_column("dropped_data_metrics"))
    op.add_column("alerts", _jsonb_column("collector_statuses"))
    op.add_column("alerts", _jsonb_column("event_delivery_summary"))
    op.add_column("alerts", sa.Column("extension_count", sa.Integer(), nullable=True))
    op.add_column("alerts", _jsonb_column("extension_reasons"))
    op.add_column("alerts", sa.Column("maximum_duration_reached", sa.Boolean(), nullable=True))
    op.add_column("alerts", sa.Column("pre_trigger_seconds", sa.Float(), nullable=True))
    op.add_column("alerts", sa.Column("post_trigger_seconds", sa.Float(), nullable=True))
    op.add_column(
        "alerts",
        sa.Column("trigger_timestamp", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "alerts",
        sa.Column(
            "test_mode",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "alerts",
        sa.Column(
            "delivery_status",
            sa.String(length=20),
            server_default="stored",
            nullable=False,
        ),
    )
    op.add_column("alerts", sa.Column("request_fingerprint", sa.String(length=64), nullable=True))
    op.add_column(
        "alerts",
        sa.Column(
            "push_status",
            sa.String(length=20),
            server_default="pending",
            nullable=False,
        ),
    )
    op.add_column(
        "alerts",
        sa.Column(
            "push_attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column("alerts", sa.Column("push_last_error", sa.String(length=100), nullable=True))
    op.add_column("alerts", sa.Column("push_provider_ticket_id", sa.Text(), nullable=True))
    op.add_column(
        "alerts",
        sa.Column("push_submitted_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_check_constraint(
        "ck_alerts_trigger_type",
        "alerts",
        "trigger_type IS NULL OR trigger_type IN ('KEYWORD', 'ACOUSTIC', 'TEST')",
    )
    op.create_check_constraint(
        "ck_alerts_delivery_status",
        "alerts",
        "delivery_status IN ('stored')",
    )
    op.create_check_constraint(
        "ck_alerts_schema_version",
        "alerts",
        "schema_version IS NULL OR schema_version >= 1",
    )
    op.create_check_constraint(
        "ck_alerts_event_timestamp_order",
        "alerts",
        "event_start_timestamp IS NULL OR event_end_timestamp IS NULL OR "
        "event_end_timestamp >= event_start_timestamp",
    )
    op.create_check_constraint(
        "ck_alerts_push_status",
        "alerts",
        "push_status IN ('pending', 'accepted', 'partial', 'rejected', 'failed', 'skipped')",
    )
    op.create_check_constraint(
        "ck_alerts_push_attempt_count",
        "alerts",
        "push_attempt_count >= 0",
    )
    op.create_index("ix_alerts_trigger_type", "alerts", ["trigger_type"], unique=False)
    op.create_index(
        "ix_alerts_event_start_timestamp",
        "alerts",
        ["event_start_timestamp"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_alerts_event_start_timestamp", table_name="alerts")
    op.drop_index("ix_alerts_trigger_type", table_name="alerts")
    op.drop_constraint("ck_alerts_push_attempt_count", "alerts", type_="check")
    op.drop_constraint("ck_alerts_push_status", "alerts", type_="check")
    op.drop_constraint("ck_alerts_event_timestamp_order", "alerts", type_="check")
    op.drop_constraint("ck_alerts_schema_version", "alerts", type_="check")
    op.drop_constraint("ck_alerts_delivery_status", "alerts", type_="check")
    op.drop_constraint("ck_alerts_trigger_type", "alerts", type_="check")

    for column_name in (
        "push_submitted_at",
        "push_provider_ticket_id",
        "push_last_error",
        "push_attempt_count",
        "push_status",
        "request_fingerprint",
        "delivery_status",
        "test_mode",
        "trigger_timestamp",
        "post_trigger_seconds",
        "pre_trigger_seconds",
        "maximum_duration_reached",
        "extension_reasons",
        "extension_count",
        "event_delivery_summary",
        "collector_statuses",
        "dropped_data_metrics",
        "processing_latency",
        "transcription_status",
        "laughter_context",
        "direct_address_evidence",
        "repetition_evidence",
        "tone_evidence",
        "detailed_acoustic_evidence",
        "acoustic_trigger_evidence",
        "monitored_word_occurrences",
        "monitored_word_detected",
        "monitored_terms",
        "event_end_timestamp",
        "event_start_timestamp",
        "device_source",
        "device_identifier",
        "review_message",
        "severity_reasons",
        "trigger_type",
        "schema_version",
    ):
        op.drop_column("alerts", column_name)
