import pytest

from app.database import AsyncSessionLocal
from app.models.slur import SlurEntry
from tests.conftest import auth_headers

NEW_TERM = {"slur_text": "synthetic term", "language": "en", "severity_weight": 0.5}


async def _seed_term() -> int:
    async with AsyncSessionLocal() as session:
        entry = SlurEntry(slur_text="seeded synthetic term", language="en", severity_weight=0.5)
        session.add(entry)
        await session.commit()
        return entry.term_id


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ["admin_a", "admin_b"])
async def test_school_admins_cannot_read_or_change_global_settings(
    client, org_identities, identity
):
    headers = auth_headers(org_identities[identity])

    read = await client.get("/system-settings/", headers=headers)
    ota = await client.post("/system-settings/ota-push", headers=headers)

    assert read.status_code == 403
    assert ota.status_code == 403


@pytest.mark.asyncio
async def test_super_admin_reads_engine_info_without_fake_sliders(client, org_identities):
    headers = auth_headers(org_identities["super"])

    read = await client.get("/system-settings/", headers=headers)
    removed_update = await client.put(
        "/system-settings/", json={"confidence_threshold": 0.9}, headers=headers
    )
    ota = await client.post("/system-settings/ota-push", headers=headers)

    assert read.status_code == 200
    assert "confidence_threshold" not in read.json()
    assert "aggression_duration_threshold" not in read.json()
    assert read.json()["stt_model_version"]
    assert removed_update.status_code == 405
    assert ota.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ["admin_a", "admin_b"])
async def test_school_admins_can_read_but_not_change_dictionary(client, org_identities, identity):
    term_id = await _seed_term()
    headers = auth_headers(org_identities[identity])

    read = await client.get("/dictionary/", headers=headers)
    added = await client.post("/dictionary/", json=NEW_TERM, headers=headers)
    deleted = await client.delete(f"/dictionary/{term_id}", headers=headers)
    after = await client.get("/dictionary/")

    assert read.status_code == 200
    assert added.status_code == 403
    assert deleted.status_code == 403
    assert [entry["term_id"] for entry in after.json()] == [term_id]


@pytest.mark.asyncio
async def test_super_admin_can_change_dictionary(client, org_identities):
    term_id = await _seed_term()
    headers = auth_headers(org_identities["super"])

    added = await client.post("/dictionary/", json=NEW_TERM, headers=headers)
    deleted = await client.delete(f"/dictionary/{term_id}", headers=headers)

    assert added.status_code == 201
    assert deleted.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ["staff_a", "counselor_a"])
async def test_non_admins_cannot_read_settings_or_change_dictionary(
    client, org_identities, identity
):
    headers = auth_headers(org_identities[identity])

    settings = await client.get("/system-settings/", headers=headers)
    added = await client.post("/dictionary/", json=NEW_TERM, headers=headers)

    assert settings.status_code == 403
    assert added.status_code == 403
