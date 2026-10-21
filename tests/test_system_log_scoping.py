import pytest

from app.database import AsyncSessionLocal
from app.models.edge_device import EdgeDevice
from app.routers import system_logs
from tests.conftest import TEST_DEVICE_KEY, TEST_DEVICE_KEY_HASH, auth_headers

SCHOOL_B_DEVICE = "school-b-test-pi"


@pytest.fixture(autouse=True)
def empty_log_buffer():
    with system_logs._log_lock:
        system_logs._log_lines.clear()
    yield
    with system_logs._log_lock:
        system_logs._log_lines.clear()


async def _add_school_b_device(org_identities) -> dict[str, str]:
    async with AsyncSessionLocal() as session:
        session.add(
            EdgeDevice(
                device_code=SCHOOL_B_DEVICE,
                display_name="School B device",
                school_id=org_identities["school_b_id"],
                api_key_hash=TEST_DEVICE_KEY_HASH,
            )
        )
        await session.commit()
    return {"X-EchoSense-Device-Id": SCHOOL_B_DEVICE, "X-EchoSense-Device-Key": TEST_DEVICE_KEY}


async def _messages(client, identity) -> list[str]:
    response = await client.get("/system/logs", headers=auth_headers(identity))
    assert response.status_code == 200
    return [line["message"] for line in response.json()["lines"]]


@pytest.mark.asyncio
async def test_device_logs_are_visible_only_to_their_school_and_super_admin(
    client, unauthenticated_edge_client, org_identities
):
    school_b_headers = await _add_school_b_device(org_identities)

    # The default client carries the school A fixture device's credentials.
    posted_a = await client.post("/system/logs", json={"lines": ["[AUDIO] school A line"]})
    posted_b = await unauthenticated_edge_client.post(
        "/system/logs", json={"lines": ["[AUDIO] school B line"]}, headers=school_b_headers
    )
    posted_untagged = await unauthenticated_edge_client.post(
        "/system/logs", json={"lines": ["[AUDIO] untagged line"]}
    )

    assert [posted_a.status_code, posted_b.status_code, posted_untagged.status_code] == [
        200,
        200,
        200,
    ]
    assert await _messages(client, org_identities["admin_a"]) == ["[AUDIO] school A line"]
    assert await _messages(client, org_identities["admin_b"]) == ["[AUDIO] school B line"]
    assert await _messages(client, org_identities["super"]) == [
        "[AUDIO] school A line",
        "[AUDIO] school B line",
        "[AUDIO] untagged line",
    ]


@pytest.mark.asyncio
async def test_search_cannot_reach_other_schools_lines(
    client, unauthenticated_edge_client, org_identities
):
    school_b_headers = await _add_school_b_device(org_identities)
    await unauthenticated_edge_client.post(
        "/system/logs", json={"lines": ["[NETWORK] secret-b"]}, headers=school_b_headers
    )

    response = await client.get(
        "/system/logs?search=secret-b", headers=auth_headers(org_identities["admin_a"])
    )

    assert response.json() == {"lines": [], "total": 0}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {"X-EchoSense-Device-Id": "classroom-test-pi", "X-EchoSense-Device-Key": "wrong"},
        {"X-EchoSense-Device-Id": "classroom-test-pi"},
        {"X-EchoSense-Device-Key": TEST_DEVICE_KEY},
        {"X-EchoSense-Device-Id": "unknown-device", "X-EchoSense-Device-Key": TEST_DEVICE_KEY},
    ],
    ids=["wrong-key", "id-only", "key-only", "unknown-device"],
)
async def test_invalid_device_credentials_are_rejected_and_not_stored(
    client, unauthenticated_edge_client, org_identities, headers
):
    response = await unauthenticated_edge_client.post(
        "/system/logs", json={"lines": ["[AUDIO] spoofed line"]}, headers=headers
    )

    assert response.status_code == 401
    assert await _messages(client, org_identities["super"]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ["staff_a", "counselor_a"])
async def test_non_admins_cannot_read_system_logs(client, org_identities, identity):
    response = await client.get("/system/logs", headers=auth_headers(org_identities[identity]))

    assert response.status_code == 403
