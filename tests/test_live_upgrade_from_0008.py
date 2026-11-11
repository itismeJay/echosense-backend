"""P0-2: upgrading a live-shaped 0008 database to head leaves nobody locked out.

Each test builds a disposable database at 20260804_0008 (what live runs), fills it the way
live is filled, and runs scripts/render_migrate.py exactly as Render's preDeployCommand.
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import NullPool

from scripts import render_migrate
from tests.test_render_migrate import (
    _downgrade_to,
    _revision,
    _sync_url,
    _upgrade_to,
)
from tests.test_render_migrate import disposable_database as _render_disposable_database

# Same throwaway database as the Render migration tests.
disposable_database = pytest.fixture(name="disposable_database")(
    _render_disposable_database.__wrapped__
)

LIVE_REVISION = "20260804_0008"
VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, VERSIONS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lexicon_migration = _load("20261105_0015_seed_bisaya_lexicon_dictionary")
mapping_migration = _load("20261105_0016_assign_users_to_default_school")


@pytest.fixture(autouse=True)
def keep_application_logging():
    """Alembic's fileConfig disables every existing logger; undo that for later tests."""

    yield
    for logger in logging.Logger.manager.loggerDict.values():
        if isinstance(logger, logging.Logger):
            logger.disabled = False


def _engine(database_url: str):
    return sa.create_engine(_sync_url(database_url), poolclass=NullPool)


def seed_live_shaped_0008(database_url: str, *, school_name: str | None) -> None:
    """Users, a device and alerts as the August main branch stored them."""

    engine = _engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """
                INSERT INTO users (email, hashed_password, role) VALUES
                  ('Admin@EchoSense.app', 'hash', 'admin'),
                  ('second-admin@example.test', 'hash', 'admin'),
                  ('teacher@example.test', 'hash', 'staff'),
                  ('counselor@example.test', 'hash', 'counselor')
                """
            )
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO slur_dictionary (slur_text, language, severity_weight)
                VALUES ('yawa', 'ceb', 0.85), ('bobo', 'fil', 0.6)
                """
            )
        )
        device_id = connection.execute(
            sa.text(
                """
                INSERT INTO edge_devices (id, device_code, display_name, classroom_name,
                                          school_name, api_key_hash)
                VALUES (gen_random_uuid(), 'classroom-pi-01', 'Classroom Pi', 'Grade 6',
                        :school, 'hash')
                RETURNING id
                """
            ),
            {"school": school_name},
        ).scalar_one()
        for snapshot in (school_name, None):
            connection.execute(
                sa.text(
                    """
                    INSERT INTO alerts (severity, confidence, duration, location,
                                        edge_device_id, classroom_name_snapshot,
                                        school_name_snapshot)
                    VALUES ('HIGH', 0.9, 2.0, 'Grade 6', :device, 'Grade 6', :school)
                    """
                ),
                {"device": device_id, "school": snapshot},
            )
    engine.dispose()


def _users(database_url: str) -> dict[str, tuple]:
    engine = _engine(database_url)
    with engine.connect() as connection:
        rows = connection.execute(
            sa.text(
                """
                SELECT lower(u.email), u.role, u.is_super_admin, s.name
                FROM users u LEFT JOIN schools s ON s.id = u.school_id
                """
            )
        ).all()
    engine.dispose()
    return {email: (role, is_super, school) for email, role, is_super, school in rows}


def _scalar(database_url: str, sql: str):
    engine = _engine(database_url)
    with engine.connect() as connection:
        value = connection.execute(sa.text(sql)).scalar()
    engine.dispose()
    return value


@pytest.mark.parametrize(
    ("school_name", "expected_school"),
    [
        ("Panabo Pilot ES", "Panabo Pilot ES"),
        # No school name anywhere on live: a default school is created.
        (None, mapping_migration.DEFAULT_SCHOOL_NAME),
    ],
)
def test_live_upgrade_maps_every_user(
    disposable_database, monkeypatch, school_name, expected_school
):
    _upgrade_to(disposable_database, LIVE_REVISION, monkeypatch)
    seed_live_shaped_0008(disposable_database, school_name=school_name)

    render_migrate.run_migrations()

    assert _revision(disposable_database) == render_migrate.HEAD_REVISION
    assert _users(disposable_database) == {
        "admin@echosense.app": ("admin", True, None),
        "second-admin@example.test": ("admin", False, expected_school),
        "teacher@example.test": ("staff", False, expected_school),
        "counselor@example.test": ("counselor", False, expected_school),
    }
    assert _scalar(disposable_database, "SELECT count(*) FROM alerts WHERE school_id IS NULL") == 0
    assert _scalar(disposable_database, "SELECT count(*) FROM school_settings") == 1
    assert (
        _scalar(
            disposable_database,
            "SELECT count(*) FROM audit_logs WHERE action = 'MIGRATION_ASSIGN_DEFAULT_SCHOOL'",
        )
        == 1
    )
    # The live dictionary now holds the lexicon (plus the 0011 seed and live's own rows).
    assert _scalar(disposable_database, "SELECT count(*) FROM slur_dictionary") >= len(
        lexicon_migration.LEXICON_TERMS
    )


def test_live_upgrade_is_idempotent_and_keeps_admin_edits(disposable_database, monkeypatch):
    _upgrade_to(disposable_database, LIVE_REVISION, monkeypatch)
    seed_live_shaped_0008(disposable_database, school_name="Panabo Pilot ES")
    engine = _engine(disposable_database)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO slur_dictionary (slur_text, language, severity_weight) "
                "VALUES ('Buang Ka', 'ceb', 0.3)"
            )
        )
    render_migrate.run_migrations()
    first = _users(disposable_database)
    terms = _scalar(disposable_database, "SELECT count(*) FROM slur_dictionary")

    with engine.begin() as connection:
        lexicon_migration.seed(connection)
        mapping_migration.assign(connection)
    render_migrate.run_migrations()
    engine.dispose()

    assert _users(disposable_database) == first
    assert _scalar(disposable_database, "SELECT count(*) FROM slur_dictionary") == terms
    # An existing spelling of a lexicon phrase is never duplicated or overwritten.
    assert (
        _scalar(
            disposable_database,
            "SELECT string_agg(slur_text || ':' || severity_weight, ',') FROM slur_dictionary "
            "WHERE lower(slur_text) = 'buang ka'",
        )
        == "Buang Ka:0.3"
    )
    assert (
        _scalar(
            disposable_database,
            "SELECT count(*) FROM audit_logs WHERE action = 'MIGRATION_ASSIGN_DEFAULT_SCHOOL'",
        )
        == 1
    )


def test_without_the_super_admin_account_nobody_is_changed(disposable_database, monkeypatch):
    _upgrade_to(disposable_database, LIVE_REVISION, monkeypatch)
    engine = _engine(disposable_database)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO users (email, hashed_password, role) VALUES "
                "('principal@example.test', 'hash', 'admin'), ('t@example.test', 'hash', 'staff')"
            )
        )
    engine.dispose()

    render_migrate.run_migrations()

    # 0009 made the admin a super admin; with no admin@echosense.app, that stays.
    assert _users(disposable_database) == {
        "principal@example.test": ("admin", True, None),
        "t@example.test": ("staff", False, None),
    }


def test_new_revisions_downgrade_and_upgrade_again(disposable_database, monkeypatch):
    _upgrade_to(disposable_database, LIVE_REVISION, monkeypatch)
    seed_live_shaped_0008(disposable_database, school_name="Panabo Pilot ES")
    render_migrate.run_migrations()
    engine = _engine(disposable_database)
    with engine.begin() as connection:
        alert_id = connection.execute(sa.text("SELECT min(id) FROM alerts")).scalar_one()
        connection.execute(
            sa.text(
                "INSERT INTO alert_matched_terms (alert_id, source, matched_text) "
                "VALUES (:alert_id, 'unlisted', 'buanga')"
            ),
            {"alert_id": alert_id},
        )
    engine.dispose()

    _downgrade_to(disposable_database, "20261027_0013", monkeypatch)
    assert _scalar(disposable_database, "SELECT count(*) FROM alert_matched_terms") == 0
    render_migrate.run_migrations()

    assert _revision(disposable_database) == render_migrate.HEAD_REVISION
