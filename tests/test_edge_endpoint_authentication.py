"""P1-3: heartbeat, log upload and dictionary sync require device credentials."""

import pytest

from app.config import settings as app_settings
from tests.conftest import TEST_DEVICE_CODE, auth_headers

WRONG_KEY = {"X-EchoSense-Device-Id": TEST_DEVICE_CODE, "X-EchoSense-Device-Key": "wrong"}
EDGE_CALLS = (
    ("POST", "/system-settings/heartbeat", None),
    ("POST", "/system/logs", {"lines": ["[AUDIO] line"]}),
    ("GET", "/dictionary/", None),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "body"), EDGE_CALLS)
async def test_edge_calls_with_device_credentials_are_accepted(client, method, path, body):
    # The default client carries the fixture device's credentials.
    response = await client.request(method, path, json=body)

    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "body"), EDGE_CALLS)
async def test_anonymous_edge_calls_are_refused(unauthenticated_edge_client, method, path, body):
    response = await unauthenticated_edge_client.request(method, path, json=body)

    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "body"), EDGE_CALLS)
async def test_wrong_device_key_is_refused_even_during_transition(
    unauthenticated_edge_client, monkeypatch, method, path, body
):
    monkeypatch.setattr(app_settings, "ECHOSENSE_ALLOW_ANONYMOUS_EDGE", True)

    response = await unauthenticated_edge_client.request(method, path, json=body, headers=WRONG_KEY)

    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "body"), EDGE_CALLS)
async def test_transition_switch_keeps_old_anonymous_edges_working(
    unauthenticated_edge_client, monkeypatch, method, path, body
):
    monkeypatch.setattr(app_settings, "ECHOSENSE_ALLOW_ANONYMOUS_EDGE", True)

    response = await unauthenticated_edge_client.request(method, path, json=body)

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_dashboard_admin_reads_the_dictionary_without_device_credentials(
    unauthenticated_edge_client, identities
):
    admin = await unauthenticated_edge_client.get(
        "/dictionary/", headers=auth_headers(identities["admin"])
    )
    staff = await unauthenticated_edge_client.get(
        "/dictionary/", headers=auth_headers(identities["staff"])
    )

    assert admin.status_code == 200
    assert staff.status_code == 403
