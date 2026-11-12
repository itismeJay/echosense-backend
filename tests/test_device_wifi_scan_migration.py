"""Migration 0023 adds Wi-Fi scan columns and the 'scan' action; downgrade removes them."""

import logging

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import NullPool

from tests.test_render_migrate import _downgrade_to, _revision, _sync_url, _upgrade_to
from tests.test_render_migrate import disposable_database as _render_disposable_database

disposable_database = pytest.fixture(name="disposable_database")(
    _render_disposable_database.__wrapped__
)
SCAN_COLUMNS = {"wifi_remote_scan_supported", "wifi_last_scan", "wifi_last_scan_at"}


@pytest.fixture(autouse=True)
def keep_application_logging():
    yield
    for logger in logging.Logger.manager.loggerDict.values():
        if isinstance(logger, logging.Logger):
            logger.disabled = False


def _state(url):
    engine = sa.create_engine(_sync_url(url), poolclass=NullPool)
    with engine.connect() as connection:
        columns = {c["name"] for c in sa.inspect(connection).get_columns("edge_devices")}
        check = connection.scalar(
            sa.text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_device_wifi_commands_action'"
            )
        )
    engine.dispose()
    return columns, check


def test_wifi_scan_upgrade_downgrade_upgrade(disposable_database, monkeypatch):
    _upgrade_to(disposable_database, "20261113_0023", monkeypatch)
    columns, check = _state(disposable_database)
    assert SCAN_COLUMNS <= columns and "'scan'" in check

    _downgrade_to(disposable_database, "20261113_0022", monkeypatch)
    columns, check = _state(disposable_database)
    assert not SCAN_COLUMNS & columns and "'scan'" not in check

    _upgrade_to(disposable_database, "20261113_0023", monkeypatch)
    assert _revision(disposable_database) == "20261113_0023"
