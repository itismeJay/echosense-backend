"""P1-4: repeated failed sign-ins are throttled with 429."""

import pytest

from app.config import settings as app_settings
from app.services.login_rate_limit import LoginRateLimiter


async def _login(client, email, password):
    return await client.post("/auth/login", json={"email": email, "password": password})


@pytest.mark.asyncio
async def test_account_is_throttled_after_repeated_failures(client, identities):
    email = identities["staff"]["email"]
    for _ in range(app_settings.LOGIN_MAX_FAILURES_PER_ACCOUNT):
        assert (await _login(client, email, "wrong")).status_code == 401

    blocked = await _login(client, email, "wrong")
    # Even the right password waits: otherwise the limit would leak which guess was right.
    right_password = await _login(client, email, identities["staff"]["password"])

    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0
    assert right_password.status_code == 429


@pytest.mark.asyncio
async def test_account_limit_ignores_email_case_and_spacing(client, identities):
    email = identities["staff"]["email"]
    for _ in range(app_settings.LOGIN_MAX_FAILURES_PER_ACCOUNT):
        await _login(client, f"  {email.upper()} ", "wrong")

    assert (await _login(client, email, "wrong")).status_code == 429


@pytest.mark.asyncio
async def test_other_accounts_are_not_blocked_by_one_accounts_failures(client, identities):
    for _ in range(app_settings.LOGIN_MAX_FAILURES_PER_ACCOUNT):
        await _login(client, identities["staff"]["email"], "wrong")

    other = await _login(
        client, identities["counselor"]["email"], identities["counselor"]["password"]
    )

    assert other.status_code == 200


@pytest.mark.asyncio
async def test_success_clears_the_accounts_failures(client, identities):
    email = identities["staff"]["email"]
    for _ in range(app_settings.LOGIN_MAX_FAILURES_PER_ACCOUNT - 1):
        await _login(client, email, "wrong")
    assert (await _login(client, email, identities["staff"]["password"])).status_code == 200

    for _ in range(app_settings.LOGIN_MAX_FAILURES_PER_ACCOUNT - 1):
        assert (await _login(client, email, "wrong")).status_code == 401


@pytest.mark.asyncio
async def test_ip_is_throttled_across_many_accounts(client, identities, monkeypatch):
    monkeypatch.setattr(app_settings, "LOGIN_MAX_FAILURES_PER_IP", 3)
    for index in range(3):
        await _login(client, f"nobody{index}@school.test", "wrong")

    response = await _login(client, identities["staff"]["email"], identities["staff"]["password"])

    assert response.status_code == 429


def test_failures_expire_after_the_window(monkeypatch):
    now = [1000.0]
    limiter = LoginRateLimiter(clock=lambda: now[0])
    monkeypatch.setattr(app_settings, "LOGIN_MAX_FAILURES_PER_ACCOUNT", 2)
    limiter.record_failure("a@school.test", "10.0.0.1")
    limiter.record_failure("a@school.test", "10.0.0.1")
    assert (
        limiter.retry_after("a@school.test", "10.0.0.1")
        == app_settings.LOGIN_FAILURE_WINDOW_SECONDS
    )

    now[0] += app_settings.LOGIN_FAILURE_WINDOW_SECONDS
    assert limiter.retry_after("a@school.test", "10.0.0.1") is None


def test_limiter_can_be_switched_off(monkeypatch):
    limiter = LoginRateLimiter()
    monkeypatch.setattr(app_settings, "LOGIN_RATE_LIMIT_ENABLED", False)
    for _ in range(50):
        limiter.record_failure("a@school.test", "10.0.0.1")

    assert limiter.retry_after("a@school.test", "10.0.0.1") is None
