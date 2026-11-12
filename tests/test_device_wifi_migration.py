"""Migration 0019 adds nullable, non-secret device Wi-Fi status fields."""

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


def test_wifi_status_columns_upgrade_and_downgrade(disposable_database, monkeypatch):
    _upgrade_to(disposable_database, "20261105_0018", monkeypatch)
    _upgrade_to(disposable_database, "head", monkeypatch)

    engine = sa.create_engine(_sync_url(disposable_database), poolclass=NullPool)
    with engine.connect() as connection:
        columns = {
            column["name"]: column for column in sa.inspect(connection).get_columns("edge_devices")
        }
        checks = {
            check["name"] for check in sa.inspect(connection).get_check_constraints("edge_devices")
        }
    engine.dispose()

    assert _revision(disposable_database) == "20261113_0022"
    assert columns["wifi_status"]["nullable"] is True
    assert columns["wifi_ssid"]["type"].length == 32
    assert columns["wifi_local_url"]["type"].length == 255
    assert "ck_edge_devices_wifi_status" in checks

    _downgrade_to(disposable_database, "20261105_0018", monkeypatch)
    engine = sa.create_engine(_sync_url(disposable_database), poolclass=NullPool)
    with engine.connect() as connection:
        downgraded = {
            column["name"] for column in sa.inspect(connection).get_columns("edge_devices")
        }
    engine.dispose()
    assert not {"wifi_status", "wifi_ssid", "wifi_local_url"} & downgraded
