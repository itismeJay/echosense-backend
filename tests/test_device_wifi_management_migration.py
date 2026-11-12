"""Migration 0020 adds the Wi-Fi network and command tables and wifi_signal; downgrade drops them."""

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


def _inspect(url):
    engine = sa.create_engine(_sync_url(url), poolclass=NullPool)
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        tables = set(inspector.get_table_names())
        columns = {column["name"] for column in inspector.get_columns("edge_devices")}
    engine.dispose()
    return tables, columns


def test_wifi_management_upgrade_downgrade_upgrade(disposable_database, monkeypatch):
    _upgrade_to(disposable_database, "20261112_0020", monkeypatch)
    assert _revision(disposable_database) == "20261112_0020"
    tables, columns = _inspect(disposable_database)
    assert {"device_wifi_networks", "device_wifi_commands"} <= tables
    assert "wifi_signal" in columns

    _downgrade_to(disposable_database, "20261109_0019", monkeypatch)
    tables, columns = _inspect(disposable_database)
    assert not {"device_wifi_networks", "device_wifi_commands"} & tables
    assert "wifi_signal" not in columns
    assert "wifi_ssid" in columns

    _upgrade_to(disposable_database, "20261112_0020", monkeypatch)
    assert _revision(disposable_database) == "20261112_0020"
