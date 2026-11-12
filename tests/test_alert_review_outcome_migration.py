"""Migration 0021 adds alert review outcome/note and report outcome counts; downgrade drops them."""

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


def _columns(url: str) -> tuple[set[str], set[str], set[str]]:
    engine = sa.create_engine(_sync_url(url), poolclass=NullPool)
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        alerts = {column["name"] for column in inspector.get_columns("alerts")}
        reports = {column["name"] for column in inspector.get_columns("reports")}
        checks = {check["name"] for check in inspector.get_check_constraints("alerts")}
    engine.dispose()
    return alerts, reports, checks


def test_review_outcome_upgrade_downgrade_upgrade(disposable_database, monkeypatch):
    _upgrade_to(disposable_database, "head", monkeypatch)
    assert _revision(disposable_database) == "20261113_0022"
    alerts, reports, checks = _columns(disposable_database)
    assert {"review_outcome", "review_note"} <= alerts
    assert "outcome_counts" in reports
    assert "ck_alerts_review_outcome" in checks

    _downgrade_to(disposable_database, "20261112_0020", monkeypatch)
    alerts, reports, checks = _columns(disposable_database)
    assert not {"review_outcome", "review_note"} & alerts
    assert "outcome_counts" not in reports
    assert "ck_alerts_review_outcome" not in checks

    _upgrade_to(disposable_database, "head", monkeypatch)
    assert _revision(disposable_database) == "20261113_0022"
