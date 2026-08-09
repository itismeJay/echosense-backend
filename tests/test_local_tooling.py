import pytest

from scripts.local_safety import validate_local_environment


def _set_local_environment(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ECHOSENSE_POSTGRES_PORT", "55432")
    monkeypatch.setenv("ECHOSENSE_POSTGRES_DB", "echosense_dev")
    monkeypatch.setenv("ECHOSENSE_POSTGRES_TEST_DB", "echosense_test")
    monkeypatch.setenv("ECHOSENSE_POSTGRES_USER", "echosense_dev")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://echosense_dev:local@127.0.0.1:55432/echosense_dev",
    )
    monkeypatch.setenv(
        "ALEMBIC_DATABASE_URL",
        "postgresql://echosense_dev:local@127.0.0.1:55432/echosense_dev",
    )
    monkeypatch.setenv(
        "ECHOSENSE_TEST_DATABASE_URL",
        "postgresql+asyncpg://echosense_dev:local@127.0.0.1:55432/echosense_test",
    )


def test_local_safety_accepts_isolated_loopback_databases(monkeypatch):
    _set_local_environment(monkeypatch)
    validate_local_environment()


def test_local_safety_rejects_remote_application_database(monkeypatch):
    _set_local_environment(monkeypatch)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://echosense_dev:local@remote.example/echosense_dev",
    )

    with pytest.raises(RuntimeError, match="loopback"):
        validate_local_environment()


def test_local_safety_rejects_shared_development_and_test_database(monkeypatch):
    _set_local_environment(monkeypatch)
    monkeypatch.setenv(
        "ECHOSENSE_TEST_DATABASE_URL",
        "postgresql+asyncpg://echosense_dev:local@127.0.0.1:55432/echosense_dev",
    )

    with pytest.raises(RuntimeError, match="must select echosense_test"):
        validate_local_environment()
