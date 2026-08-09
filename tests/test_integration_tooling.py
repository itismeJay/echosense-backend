import pytest

from scripts.integration_safety import validate_integration_environment

NEON_URL = "postgresql://integration:private@sample-pooler.us-east-1.aws.neon.tech/app"


def _set_integration_environment(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "integration")
    monkeypatch.setenv("DATABASE_URL", NEON_URL)
    monkeypatch.delenv("ALEMBIC_DATABASE_URL", raising=False)
    monkeypatch.delenv("ECHOSENSE_TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv("RUN_LEGACY_STARTUP_MAINTENANCE", "false")
    monkeypatch.setenv("ECHOSENSE_ALLOW_TEST_ALERTS", "false")
    monkeypatch.setenv("TESTING", "false")


def test_integration_safety_accepts_matching_neon_environment(monkeypatch):
    _set_integration_environment(monkeypatch)
    validate_integration_environment(NEON_URL)


def test_integration_safety_rejects_local_database(monkeypatch):
    _set_integration_environment(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql://local@127.0.0.1:55432/echosense_dev")

    with pytest.raises(RuntimeError, match="must select Neon"):
        validate_integration_environment()


def test_integration_safety_rejects_different_neon_database(monkeypatch):
    _set_integration_environment(monkeypatch)

    with pytest.raises(RuntimeError, match="does not match"):
        validate_integration_environment(
            "postgresql://integration:private@other.us-east-1.aws.neon.tech/app"
        )


@pytest.mark.parametrize("variable", ["ALEMBIC_DATABASE_URL", "ECHOSENSE_TEST_DATABASE_URL"])
def test_integration_safety_rejects_migration_or_test_urls(monkeypatch, variable):
    _set_integration_environment(monkeypatch)
    monkeypatch.setenv(variable, NEON_URL)

    with pytest.raises(RuntimeError, match=f"{variable} must be unset"):
        validate_integration_environment()


@pytest.mark.parametrize(
    "variable",
    ["RUN_LEGACY_STARTUP_MAINTENANCE", "ECHOSENSE_ALLOW_TEST_ALERTS", "TESTING"],
)
def test_integration_safety_rejects_write_capable_flags(monkeypatch, variable):
    _set_integration_environment(monkeypatch)
    monkeypatch.setenv(variable, "true")

    with pytest.raises(RuntimeError):
        validate_integration_environment()
