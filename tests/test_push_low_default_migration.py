"""Migration 0022: LOW is pushed by default; schools with custom push levels keep them."""

import logging

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import NullPool

from tests.test_render_migrate import (
    _alembic_head,
    _downgrade_to,
    _revision,
    _sync_url,
    _upgrade_to,
)
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


def _execute(url, statement, **params):
    engine = sa.create_engine(_sync_url(url), poolclass=NullPool)
    with engine.begin() as connection:
        result = connection.execute(sa.text(statement), params)
        rows = result.fetchall() if result.returns_rows else None
    engine.dispose()
    return rows


def _school(url, name, levels=None):
    [(school_id,)] = _execute(
        url,
        "INSERT INTO schools (id, name) VALUES (gen_random_uuid(), :name) RETURNING id",
        name=name,
    )
    if levels is None:
        _execute(url, "INSERT INTO school_settings (school_id) VALUES (:id)", id=school_id)
    else:
        _execute(
            url,
            "INSERT INTO school_settings (school_id, push_levels) VALUES (:id, :levels)",
            id=school_id,
            levels=levels,
        )
    return school_id


def _levels(url, school_id):
    [(levels,)] = _execute(
        url, "SELECT push_levels FROM school_settings WHERE school_id = :id", id=school_id
    )
    return levels


def test_low_added_only_to_schools_on_the_old_default(disposable_database, monkeypatch):
    url = disposable_database
    _upgrade_to(url, "20261113_0021", monkeypatch)
    default_school = _school(url, "Default School")
    custom_school = _school(url, "High Only School", ["HIGH"])
    assert _levels(url, default_school) == ["HIGH", "MEDIUM"]

    _upgrade_to(url, "head", monkeypatch)
    assert _revision(url) == _alembic_head()
    assert _levels(url, default_school) == ["HIGH", "MEDIUM", "LOW"]
    assert _levels(url, custom_school) == ["HIGH"]
    new_school = _school(url, "New School")
    assert _levels(url, new_school) == ["HIGH", "MEDIUM", "LOW"]

    _downgrade_to(url, "20261113_0021", monkeypatch)
    assert _levels(url, default_school) == ["HIGH", "MEDIUM"]
    assert _levels(url, custom_school) == ["HIGH"]

    _upgrade_to(url, "head", monkeypatch)
    _upgrade_to(url, "head", monkeypatch)
    assert _levels(url, default_school) == ["HIGH", "MEDIUM", "LOW"]
