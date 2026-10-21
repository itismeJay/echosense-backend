import pytest

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.user import User
from app.services.provider_test import _clear_provider_test_dry_runs_for_testing
from tests.conftest import auth_headers

TOKEN_A = "ExpoPushToken[school-a-device]"
TOKEN_B = "ExpoPushToken[school-b-device]"


@pytest.fixture(autouse=True)
def provider_test_state(monkeypatch):
    _clear_provider_test_dry_runs_for_testing()
    monkeypatch.setattr(settings, "ECHOSENSE_CONTROLLED_TEST_MODE", False)
    monkeypatch.setattr(settings, "ECHOSENSE_CONTROLLED_TEST_USER_ID", None)
    monkeypatch.setattr(settings, "EXPO_ACCESS_TOKEN", None)
    yield
    _clear_provider_test_dry_runs_for_testing()


async def _give_tokens(org_identities) -> None:
    async with AsyncSessionLocal() as session:
        (await session.get(User, org_identities["staff_a"]["id"])).push_token = TOKEN_A
        (await session.get(User, org_identities["staff_b"]["id"])).push_token = TOKEN_B
        await session.commit()


def _control(monkeypatch, user_id: int) -> None:
    monkeypatch.setattr(settings, "ECHOSENSE_CONTROLLED_TEST_MODE", True)
    monkeypatch.setattr(settings, "ECHOSENSE_CONTROLLED_TEST_USER_ID", user_id)


async def _audit(client, identity):
    return await client.get("/users/notification-recipient-audit", headers=auth_headers(identity))


@pytest.mark.asyncio
async def test_controlled_recipient_is_visible_to_its_school_and_super_admin(
    client, org_identities, monkeypatch
):
    await _give_tokens(org_identities)
    _control(monkeypatch, org_identities["staff_a"]["id"])

    own_school = await _audit(client, org_identities["admin_a"])
    super_admin = await _audit(client, org_identities["super"])

    assert own_school.status_code == 200
    assert (
        own_school.json()["recipient_internal_id"] == f"user_id:{org_identities['staff_a']['id']}"
    )
    assert super_admin.status_code == 200
    assert super_admin.json()["masked_email"] == own_school.json()["masked_email"]


@pytest.mark.asyncio
async def test_other_school_admin_gets_no_recipient_details(client, org_identities, monkeypatch):
    await _give_tokens(org_identities)
    _control(monkeypatch, org_identities["staff_a"]["id"])

    response = await _audit(client, org_identities["admin_b"])

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["configured_recipient_resolved"] is False
    assert detail["recipient_internal_id"] is None
    assert detail["masked_email"] is None
    assert detail["role"] is None
    assert detail["has_push_token"] is False
    assert "staff_a" not in response.text and "org.test" not in response.text


@pytest.mark.asyncio
async def test_other_school_admin_cannot_dry_run_or_send_provider_test(
    client, org_identities, monkeypatch
):
    await _give_tokens(org_identities)
    _control(monkeypatch, org_identities["staff_a"]["id"])
    body = {
        "confirmed_recipient_user_id": org_identities["staff_a"]["id"],
        "physical_device_confirmed": True,
    }

    foreign_dry_run = await client.post(
        "/users/provider-test/dry-run", json=body, headers=auth_headers(org_identities["admin_b"])
    )
    own_dry_run = await client.post(
        "/users/provider-test/dry-run", json=body, headers=auth_headers(org_identities["admin_a"])
    )
    foreign_send = await client.post(
        "/users/provider-test/send",
        json={**body, "test_id": own_dry_run.json()["test_id"], "approve_single_send": True},
        headers=auth_headers(org_identities["admin_b"]),
    )

    assert foreign_dry_run.status_code == 409
    assert own_dry_run.status_code == 200
    assert foreign_send.status_code == 409


@pytest.mark.asyncio
async def test_broadcast_recipient_counts_are_school_scoped(client, org_identities):
    await _give_tokens(org_identities)

    school_a = await _audit(client, org_identities["admin_a"])
    school_b = await _audit(client, org_identities["admin_b"])
    super_admin = await _audit(client, org_identities["super"])

    assert school_a.json()["selected_recipient_count"] == 1
    assert school_b.json()["selected_recipient_count"] == 1
    assert super_admin.json()["selected_recipient_count"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ["staff_a", "counselor_a"])
async def test_non_admins_cannot_use_push_diagnostics(client, org_identities, identity):
    response = await _audit(client, org_identities[identity])

    assert response.status_code == 403
