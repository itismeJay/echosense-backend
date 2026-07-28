from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.engine import URL, make_url
from sqlalchemy.pool import NullPool

from scripts import render_migrate


def _sync_url(database_url: str) -> URL:
    return make_url(database_url).set(drivername="postgresql+psycopg2")


def _database_url(database_name: str) -> str:
    return (
        _sync_url(os.environ["ECHOSENSE_TEST_DATABASE_URL"])
        .set(database=database_name)
        .render_as_string(hide_password=False)
    )


@pytest.fixture
def disposable_database() -> Iterator[str]:
    base_url = _sync_url(os.environ["ECHOSENSE_TEST_DATABASE_URL"])
    database_name = f"echosense_migrate_{uuid4().hex}"
    admin_database = "postgres" if base_url.database != "postgres" else "template1"
    admin_engine = sa.create_engine(
        base_url.set(database=admin_database),
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )

    with admin_engine.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')

    try:
        yield _database_url(database_name)
    finally:
        with admin_engine.connect() as connection:
            connection.execute(
                sa.text(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = :database_name
                      AND pid <> pg_backend_pid()
                    """
                ),
                {"database_name": database_name},
            )
            connection.exec_driver_sql(f'DROP DATABASE "{database_name}"')
        admin_engine.dispose()


def _upgrade_to(database_url: str, revision: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", database_url)
    command.upgrade(render_migrate._alembic_config(), revision)


def _downgrade_to(database_url: str, revision: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", database_url)
    command.downgrade(render_migrate._alembic_config(), revision)


def _seed_legacy_rows(database_url: str) -> None:
    engine = sa.create_engine(_sync_url(database_url), poolclass=NullPool)
    with engine.begin() as connection:
        user_id = connection.execute(
            sa.text(
                """
                INSERT INTO users (email, hashed_password, role)
                VALUES ('legacy-admin@example.test', 'rotated-test-hash', 'admin')
                RETURNING id
                """
            )
        ).scalar_one()
        connection.execute(
            sa.text(
                """
                INSERT INTO audit_logs (
                    user_id, actor_email, action, module, target, performed_at
                )
                VALUES (
                    :user_id,
                    'legacy-admin@example.test',
                    'Legacy Action',
                    'Legacy Module',
                    'Legacy Target',
                    TIMESTAMP '2026-07-01 12:00:00'
                )
                """
            ),
            {"user_id": user_id},
        )
    engine.dispose()


def _revision(database_url: str) -> str:
    engine = sa.create_engine(_sync_url(database_url), poolclass=NullPool)
    with engine.connect() as connection:
        revision = connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar_one()
    engine.dispose()
    return revision


def _legacy_audit_count(database_url: str) -> int:
    engine = sa.create_engine(_sync_url(database_url), poolclass=NullPool)
    with engine.connect() as connection:
        count = connection.execute(
            sa.text(
                """
                SELECT count(*)
                FROM audit_logs
                WHERE action = 'Legacy Action'
                  AND target = 'Legacy Target'
                """
            )
        ).scalar_one()
    engine.dispose()
    return count


def _seed_legacy_alerts_and_dictionary(database_url: str) -> None:
    engine = sa.create_engine(_sync_url(database_url), poolclass=NullPool)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """
                INSERT INTO alerts (
                    severity, confidence, duration, location, status, language
                )
                VALUES
                    ('medium', 0.8, 1.2, 'Legacy Room', 'active', NULL),
                    ('high', 0.9, 2.4, 'Legacy Room', 'active', 'Bisaya')
                """
            )
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO slur_dictionary (
                    slur_text, language, severity_weight
                )
                VALUES
                    ('legacy-fil', 'Filipino', 0.6),
                    ('legacy-ceb', 'Bisaya', 0.7),
                    ('legacy-en', 'English', 0.5)
                """
            )
        )
    engine.dispose()


def test_missing_migration_url_never_falls_back_to_application_url(monkeypatch):
    monkeypatch.delenv("ALEMBIC_DATABASE_URL", raising=False)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://must-not-be-used:must-not-be-used@127.0.0.1/must-not-be-used",
    )

    with pytest.raises(render_migrate.MigrationSafetyError, match="never used"):
        render_migrate.run_migrations()


def test_untracked_legacy_baseline_is_stamped_and_upgraded(
    disposable_database,
    monkeypatch,
):
    _upgrade_to(disposable_database, render_migrate.BASELINE_REVISION, monkeypatch)
    _seed_legacy_rows(disposable_database)
    engine = sa.create_engine(_sync_url(disposable_database), poolclass=NullPool)
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE alembic_version")
    engine.dispose()

    render_migrate.run_migrations()

    assert _revision(disposable_database) == render_migrate.HEAD_REVISION
    assert _legacy_audit_count(disposable_database) == 1


def test_database_at_baseline_is_upgraded_without_restamping(
    disposable_database,
    monkeypatch,
):
    _upgrade_to(disposable_database, render_migrate.BASELINE_REVISION, monkeypatch)
    _seed_legacy_rows(disposable_database)

    render_migrate.run_migrations()

    assert _revision(disposable_database) == render_migrate.HEAD_REVISION
    assert _legacy_audit_count(disposable_database) == 1


def test_database_at_head_is_idempotent(disposable_database, monkeypatch):
    _upgrade_to(disposable_database, render_migrate.HEAD_REVISION, monkeypatch)

    render_migrate.run_migrations()
    render_migrate.run_migrations()

    assert _revision(disposable_database) == render_migrate.HEAD_REVISION


def test_existing_alerts_and_dictionary_remain_readable_after_migration(
    disposable_database,
    monkeypatch,
):
    _upgrade_to(disposable_database, render_migrate.BASELINE_REVISION, monkeypatch)
    _seed_legacy_alerts_and_dictionary(disposable_database)

    _upgrade_to(disposable_database, "head", monkeypatch)

    engine = sa.create_engine(_sync_url(disposable_database), poolclass=NullPool)
    with engine.connect() as connection:
        alerts = connection.execute(
            sa.text(
                """
                SELECT severity, language, language_confidence
                FROM alerts
                ORDER BY id
                """
            )
        ).all()
        dictionary_languages = (
            connection.execute(
                sa.text(
                    """
                SELECT language
                FROM slur_dictionary
                ORDER BY term_id
                """
                )
            )
            .scalars()
            .all()
        )
    engine.dispose()

    assert alerts == [
        ("medium", "unknown", None),
        ("high", "ceb", None),
    ]
    assert dictionary_languages == ["fil", "ceb", "en"]


def test_edge_audio_event_migration_upgrades_downgrades_and_upgrades_again(
    disposable_database,
    monkeypatch,
):
    previous_revision = "20260728_0004"
    _upgrade_to(disposable_database, previous_revision, monkeypatch)

    engine = sa.create_engine(_sync_url(disposable_database), poolclass=NullPool)
    with engine.begin() as connection:
        legacy_alert_id = connection.execute(
            sa.text(
                """
                INSERT INTO alerts (severity, confidence, duration, language)
                VALUES ('low', 0.5, 0.4, 'unknown')
                RETURNING id
                """
            )
        ).scalar_one()
    engine.dispose()

    _upgrade_to(disposable_database, render_migrate.HEAD_REVISION, monkeypatch)

    engine = sa.create_engine(_sync_url(disposable_database), poolclass=NullPool)
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        columns = {column["name"]: column for column in inspector.get_columns("alerts")}
        unique_constraints = {
            constraint["name"] for constraint in inspector.get_unique_constraints("alerts")
        }
        check_constraints = {
            constraint["name"] for constraint in inspector.get_check_constraints("alerts")
        }
        legacy_values = connection.execute(
            sa.text(
                """
                SELECT event_id, yamnet_ran
                FROM alerts
                WHERE id = :alert_id
                """
            ),
            {"alert_id": legacy_alert_id},
        ).one()

    assert columns["event_id"]["nullable"] is True
    assert str(columns["event_id"]["type"]) == "UUID"
    assert columns["yamnet_ran"]["nullable"] is True
    assert isinstance(columns["yamnet_ran"]["type"], sa.Boolean)
    assert "uq_alerts_event_id" in unique_constraints
    assert "ck_alerts_yamnet_evidence" in check_constraints
    assert legacy_values == (None, None)
    engine.dispose()

    _downgrade_to(disposable_database, previous_revision, monkeypatch)

    engine = sa.create_engine(_sync_url(disposable_database), poolclass=NullPool)
    with engine.connect() as connection:
        downgraded_columns = {
            column["name"] for column in sa.inspect(connection).get_columns("alerts")
        }
    engine.dispose()

    assert "event_id" not in downgraded_columns
    assert "yamnet_ran" not in downgraded_columns
    assert _revision(disposable_database) == previous_revision

    _upgrade_to(disposable_database, render_migrate.HEAD_REVISION, monkeypatch)

    assert _revision(disposable_database) == render_migrate.HEAD_REVISION


def test_partial_schema_is_rejected_without_mutation(disposable_database, monkeypatch):
    _upgrade_to(disposable_database, render_migrate.BASELINE_REVISION, monkeypatch)
    _seed_legacy_rows(disposable_database)
    engine = sa.create_engine(_sync_url(disposable_database), poolclass=NullPool)
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE alembic_version")
        connection.exec_driver_sql("ALTER TABLE audit_logs RENAME COLUMN module TO resource")

    with pytest.raises(render_migrate.MigrationSafetyError, match="audit_logs"):
        render_migrate.run_migrations()

    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        columns = {column["name"] for column in inspector.get_columns("audit_logs")}
        version_table_exists = inspector.has_table("alembic_version")
        audit_count = connection.execute(sa.text("SELECT count(*) FROM audit_logs")).scalar_one()
    engine.dispose()

    assert "resource" in columns
    assert "module" not in columns
    assert not version_table_exists
    assert audit_count == 1
