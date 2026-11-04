"""Migration 0018: existing devices stay active; keyless boxes survive a downgrade."""

from __future__ import annotations

import logging

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import NullPool

from tests.test_render_migrate import _downgrade_to, _revision, _sync_url, _upgrade_to
from tests.test_render_migrate import disposable_database as _render_disposable_database

disposable_database = pytest.fixture(name="disposable_database")(
    _render_disposable_database.__wrapped__
)


@pytest.fixture(autouse=True)
def keep_application_logging():
    yield
    for logger in logging.Logger.manager.loggerDict.values():
        if isinstance(logger, logging.Logger):
            logger.disabled = False


def _engine(url: str):
    return sa.create_engine(_sync_url(url), poolclass=NullPool)


def test_existing_devices_become_active_and_downgrade_handles_unclaimed_boxes(
    disposable_database, monkeypatch
):
    _upgrade_to(disposable_database, "20261105_0017", monkeypatch)
    engine = _engine(disposable_database)
    with engine.begin() as connection:
        school_id = connection.execute(
            sa.text(
                "INSERT INTO schools (id, name) VALUES (gen_random_uuid(), 'Live School') RETURNING id"
            )
        ).scalar_one()
        classroom_id = connection.execute(
            sa.text(
                "INSERT INTO classrooms (id, school_id, name) "
                "VALUES (gen_random_uuid(), :s, 'Room 1') RETURNING id"
            ),
            {"s": school_id},
        ).scalar_one()
        connection.execute(
            sa.text(
                """
                INSERT INTO edge_devices (id, device_code, display_name, school_id, classroom_id,
                                          api_key_hash)
                VALUES (gen_random_uuid(), 'live-pi', 'Live Pi', :s, :c, 'existing-key-hash')
                """
            ),
            {"s": school_id, "c": classroom_id},
        )
    engine.dispose()

    _upgrade_to(disposable_database, "head", monkeypatch)
    engine = _engine(disposable_database)
    with engine.begin() as connection:
        live = connection.execute(
            sa.text(
                "SELECT claim_state, api_key_hash, is_active, classroom_id FROM edge_devices "
                "WHERE device_code = 'live-pi'"
            )
        ).one()
        assert live == ("active", "existing-key-hash", True, classroom_id)
        connection.execute(
            sa.text(
                """
                INSERT INTO edge_devices (id, device_code, display_name, claim_state,
                                          claim_code_hash)
                VALUES (gen_random_uuid(), 'es-new1', 'Box', 'unclaimed', 'h')
                """
            )
        )
        # An unclaimed box can never sit in a school or carry a key.
        with pytest.raises(sa.exc.IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    sa.text("UPDATE edge_devices SET school_id = :s WHERE device_code = 'es-new1'"),
                    {"s": school_id},
                )
        with pytest.raises(sa.exc.IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    sa.text(
                        "UPDATE edge_devices SET claim_state = 'active' "
                        "WHERE device_code = 'es-new1'"
                    )
                )
    engine.dispose()

    _downgrade_to(disposable_database, "20261105_0017", monkeypatch)
    assert _revision(disposable_database) == "20261105_0017"
    engine = _engine(disposable_database)
    with engine.connect() as connection:
        rows = dict(
            connection.execute(
                sa.text("SELECT device_code, is_active FROM edge_devices ORDER BY device_code")
            ).all()
        )
        key_hashes = dict(
            connection.execute(sa.text("SELECT device_code, api_key_hash FROM edge_devices")).all()
        )
    engine.dispose()
    assert rows == {"es-new1": False, "live-pi": True}
    assert key_hashes["live-pi"] == "existing-key-hash"
    assert key_hashes["es-new1"].startswith("!")

    _upgrade_to(disposable_database, "head", monkeypatch)
    assert _revision(disposable_database) == "20261105_0018"
