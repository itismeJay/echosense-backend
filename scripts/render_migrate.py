"""Safely baseline and upgrade the production PostgreSQL schema on Render."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Connection, URL, make_url
from sqlalchemy.pool import NullPool

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASELINE_REVISION = "20260727_0001"
HEAD_REVISION = "20260727_0003"
MIGRATION_LOCK_ID = 202607270003

BASELINE_COLUMNS = {
    "users": frozenset({"id", "email", "hashed_password", "role", "push_token"}),
    "alerts": frozenset(
        {
            "id",
            "severity",
            "confidence",
            "duration",
            "location",
            "status",
            "created_at",
            "transcribed_text",
            "detected_words",
            "yamnet_class",
            "yamnet_score",
            "emotion",
            "rms",
            "energy_variance",
            "zero_crossing_rate",
            "peak_to_average",
            "waveform_snapshot",
            "categories",
            "language",
            "hard_hits",
            "soft_hits",
            "duration_gate",
            "required_duration",
        }
    ),
    "slur_dictionary": frozenset(
        {"term_id", "slur_text", "language", "severity_weight", "added_at"}
    ),
    "system_settings": frozenset(
        {
            "setting_id",
            "confidence_threshold",
            "aggression_duration_threshold",
            "device_status",
            "last_heartbeat",
            "vosk_version",
            "yamnet_version",
            "last_ota_update",
            "updated_at",
        }
    ),
    "audit_logs": frozenset(
        {
            "log_id",
            "user_id",
            "actor_email",
            "action",
            "module",
            "target",
            "performed_at",
        }
    ),
    "reports": frozenset(
        {
            "report_id",
            "generated_by",
            "generated_by_email",
            "date_from",
            "date_to",
            "total_incidents",
            "generated_at",
        }
    ),
}


class MigrationSafetyError(RuntimeError):
    """Raised before migration when the database cannot be upgraded safely."""


@dataclass(frozen=True)
class ColumnSpec:
    family: str
    nullable: bool
    length: int | None = None


LEGACY_AUDIT_COLUMN_SPECS = {
    "log_id": ColumnSpec("integer", nullable=False),
    "user_id": ColumnSpec("integer", nullable=True),
    "actor_email": ColumnSpec("string", nullable=True, length=100),
    "action": ColumnSpec("string", nullable=False, length=100),
    "module": ColumnSpec("string", nullable=False, length=50),
    "target": ColumnSpec("string", nullable=True, length=100),
    "performed_at": ColumnSpec("datetime_without_timezone", nullable=True),
}


def _required_database_url() -> str:
    database_url = os.environ.get("ALEMBIC_DATABASE_URL")
    if not database_url or not database_url.strip():
        raise MigrationSafetyError(
            "ALEMBIC_DATABASE_URL is required; the application DATABASE_URL is never used."
        )
    return database_url


def _sync_database_url(database_url: str) -> URL:
    try:
        parsed = make_url(database_url)
    except sa.exc.ArgumentError as exc:
        raise MigrationSafetyError("ALEMBIC_DATABASE_URL is not a valid database URL.") from exc

    if parsed.get_backend_name() != "postgresql":
        raise MigrationSafetyError("ALEMBIC_DATABASE_URL must select PostgreSQL.")
    return parsed.set(drivername="postgresql+psycopg2")


def _alembic_config() -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return config


def _verify_migration_head(config: Config) -> None:
    heads = tuple(ScriptDirectory.from_config(config).get_heads())
    if heads != (HEAD_REVISION,):
        raise MigrationSafetyError(
            f"Expected exactly one Alembic head at {HEAD_REVISION}; migration was not attempted."
        )


def _column_family(column_type: sa.types.TypeEngine) -> str:
    if isinstance(column_type, sa.DateTime):
        return (
            "datetime_with_timezone"
            if bool(getattr(column_type, "timezone", False))
            else "datetime_without_timezone"
        )
    if isinstance(column_type, sa.Date):
        return "date"
    if isinstance(column_type, sa.Integer):
        return "integer"
    if isinstance(column_type, sa.Text):
        return "text"
    if isinstance(column_type, sa.String):
        return "string"
    if isinstance(column_type, sa.Float):
        return "float"
    return column_type.__class__.__name__.lower()


def _validate_legacy_schema(connection: Connection) -> None:
    inspector = sa.inspect(connection)
    actual_tables = set(inspector.get_table_names(schema="public"))
    expected_tables = set(BASELINE_COLUMNS)

    missing_tables = sorted(expected_tables - actual_tables)
    unexpected_tables = sorted(actual_tables - expected_tables)
    if missing_tables or unexpected_tables:
        details = []
        if missing_tables:
            details.append(f"missing tables: {', '.join(missing_tables)}")
        if unexpected_tables:
            details.append(f"unexpected tables: {', '.join(unexpected_tables)}")
        raise MigrationSafetyError(
            "Legacy schema does not match revision 20260727_0001 (" + "; ".join(details) + ")."
        )

    for table_name, expected_columns in BASELINE_COLUMNS.items():
        actual_columns = {
            column["name"] for column in inspector.get_columns(table_name, schema="public")
        }
        missing_columns = sorted(expected_columns - actual_columns)
        unexpected_columns = sorted(actual_columns - expected_columns)
        if missing_columns or unexpected_columns:
            details = []
            if missing_columns:
                details.append(f"missing columns: {', '.join(missing_columns)}")
            if unexpected_columns:
                details.append(f"unexpected columns: {', '.join(unexpected_columns)}")
            raise MigrationSafetyError(
                f"Legacy table {table_name} does not match revision {BASELINE_REVISION} "
                f"({'; '.join(details)})."
            )

    audit_columns = {
        column["name"]: column for column in inspector.get_columns("audit_logs", schema="public")
    }
    for column_name, expected in LEGACY_AUDIT_COLUMN_SPECS.items():
        actual = audit_columns[column_name]
        actual_family = _column_family(actual["type"])
        if actual_family != expected.family:
            raise MigrationSafetyError(f"Legacy audit_logs.{column_name} has an incompatible type.")
        if bool(actual["nullable"]) != expected.nullable:
            raise MigrationSafetyError(
                f"Legacy audit_logs.{column_name} has incompatible nullability."
            )
        if expected.family == "string" and actual["type"].length != expected.length:
            raise MigrationSafetyError(
                f"Legacy audit_logs.{column_name} has an incompatible length."
            )

    audit_primary_key = inspector.get_pk_constraint("audit_logs", schema="public")
    if audit_primary_key.get("constrained_columns") != ["log_id"]:
        raise MigrationSafetyError("Legacy audit_logs primary key is not log_id.")

    users_primary_key = inspector.get_pk_constraint("users", schema="public")
    if users_primary_key.get("constrained_columns") != ["id"]:
        raise MigrationSafetyError("Legacy users primary key is not id.")

    audit_foreign_keys = inspector.get_foreign_keys("audit_logs", schema="public")
    has_expected_user_foreign_key = any(
        foreign_key.get("constrained_columns") == ["user_id"]
        and foreign_key.get("referred_table") == "users"
        and foreign_key.get("referred_columns") == ["id"]
        for foreign_key in audit_foreign_keys
    )
    if not has_expected_user_foreign_key:
        raise MigrationSafetyError(
            "Legacy audit_logs.user_id does not reference users.id as expected."
        )

    database_has_records = any(
        bool(
            connection.execute(
                sa.text(f'SELECT EXISTS (SELECT 1 FROM public."{table_name}" LIMIT 1)')
            ).scalar_one()
        )
        for table_name in sorted(BASELINE_COLUMNS)
    )
    if not database_has_records:
        raise MigrationSafetyError(
            "Legacy tables contain no records; refusing to stamp an empty database."
        )


def _read_revisions(connection: Connection) -> tuple[str, ...]:
    rows = connection.execute(
        sa.text("SELECT version_num FROM public.alembic_version ORDER BY version_num")
    )
    return tuple(row[0] for row in rows)


def _require_single_revision(connection: Connection) -> str:
    revisions = _read_revisions(connection)
    if len(revisions) != 1 or not revisions[0]:
        raise MigrationSafetyError(
            "alembic_version must contain exactly one current revision; no stamp was attempted."
        )
    return revisions[0]


def run_migrations() -> None:
    """Run the one-way production migration after all safety checks pass."""

    database_url = _required_database_url()
    sync_database_url = _sync_database_url(database_url)
    config = _alembic_config()
    _verify_migration_head(config)

    engine = sa.create_engine(sync_database_url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            lock_acquired = bool(
                connection.execute(
                    sa.text("SELECT pg_try_advisory_lock(:lock_id)"),
                    {"lock_id": MIGRATION_LOCK_ID},
                ).scalar_one()
            )
            if not lock_acquired:
                raise MigrationSafetyError(
                    "Another production migration runner is active; this release was not migrated."
                )

            try:
                inspector = sa.inspect(connection)
                has_version_table = inspector.has_table("alembic_version", schema="public")

                if has_version_table:
                    current_revision = _require_single_revision(connection)
                    print(f"Existing Alembic revision detected: {current_revision}")
                    # Release relation locks acquired while inspecting the current
                    # revision. The PostgreSQL advisory lock is session-scoped and
                    # remains held across this commit.
                    connection.commit()
                    command.upgrade(config, "head")
                else:
                    _validate_legacy_schema(connection)
                    print(
                        "Legacy schema safely matches the production baseline; "
                        f"stamping {BASELINE_REVISION}."
                    )
                    # Alembic needs exclusive locks on the inspected legacy tables.
                    # End the read transaction without releasing the session-level
                    # advisory lock before stamping and upgrading.
                    connection.commit()
                    command.stamp(config, BASELINE_REVISION)
                    command.upgrade(config, HEAD_REVISION)

                final_revision = _require_single_revision(connection)
                if final_revision != HEAD_REVISION:
                    raise MigrationSafetyError(
                        f"Migration verification expected {HEAD_REVISION}, "
                        f"but found {final_revision}."
                    )
                print(f"Production migration verified at revision {HEAD_REVISION}.")
            finally:
                connection.execute(
                    sa.text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": MIGRATION_LOCK_ID},
                )
    finally:
        engine.dispose()


def main() -> int:
    try:
        run_migrations()
    except MigrationSafetyError as exc:
        print(f"Migration aborted safely: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(
            "Migration failed safely before release activation "
            f"({type(exc).__name__}); credentials were not displayed.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
