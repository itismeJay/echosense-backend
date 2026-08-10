import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import seed_users

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
EXPLICIT_PASSWORD = "operator-provided-test-value"


def _environment(**overrides):
    return {
        seed_users.SEED_EMAIL_ENV: "operator@example.test",
        seed_users.SEED_PASSWORD_ENV: EXPLICIT_PASSWORD,
        **overrides,
    }


def test_seed_config_requires_explicit_email():
    with pytest.raises(seed_users.SeedConfigurationError, match="no seed email default exists"):
        seed_users.load_seed_config([], {})


@pytest.mark.parametrize("password", [None, "", "   "])
def test_seed_config_requires_explicit_nonblank_password(password):
    environment = {seed_users.SEED_EMAIL_ENV: "operator@example.test"}
    if password is not None:
        environment[seed_users.SEED_PASSWORD_ENV] = password

    with pytest.raises(seed_users.SeedConfigurationError, match="no seed password default exists"):
        seed_users.load_seed_config([], environment)


def test_normal_seeded_admin_is_not_silently_super_admin():
    config = seed_users.load_seed_config([], _environment())

    assert config.role == "admin"
    assert config.is_super_admin is False
    assert config.password == EXPLICIT_PASSWORD
    assert EXPLICIT_PASSWORD not in repr(config)


def test_explicit_bootstrap_super_admin_is_supported():
    config = seed_users.load_seed_config(["--super-admin"], _environment())

    assert config.role == "admin"
    assert config.is_super_admin is True


def test_super_admin_flag_is_rejected_for_non_admin_roles():
    with pytest.raises(seed_users.SeedConfigurationError, match="requires --role admin"):
        seed_users.load_seed_config(["--role", "staff", "--super-admin"], _environment())


def test_seed_main_never_prints_plaintext_password(monkeypatch, capsys):
    mocked_seed = AsyncMock()
    monkeypatch.setattr(seed_users, "seed", mocked_seed)

    assert seed_users.main([], _environment()) == 0
    captured = capsys.readouterr()
    assert EXPLICIT_PASSWORD not in captured.out
    assert EXPLICIT_PASSWORD not in captured.err
    mocked_seed.assert_awaited_once()


def test_local_claude_settings_path_is_ignored():
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--quiet", ".claude/settings.local.json"],
        cwd=REPOSITORY_ROOT,
        check=False,
    )

    assert result.returncode == 0
