import pytest

from tests.conftest import auth_headers


@pytest.mark.asyncio
async def test_settings_response_returns_new_field_and_deprecated_alias(client, identities):
    response = await client.get("/system-settings/", headers=auth_headers(identities["admin"]))

    assert response.status_code == 200
    body = response.json()
    assert body["stt_model_version"] == "local-test"
    assert body["vosk_version"] == body["stt_model_version"]


@pytest.mark.asyncio
async def test_global_settings_can_no_longer_be_updated(client, identities):
    response = await client.put(
        "/system-settings/",
        json={"confidence_threshold": 0.7},
        headers=auth_headers(identities["admin"]),
    )

    assert response.status_code == 405


@pytest.mark.asyncio
async def test_openapi_marks_vosk_version_deprecated(client):
    schemas = (await client.get("/openapi.json")).json()["components"]["schemas"]

    properties = schemas["SystemSettingsOut"]["properties"]
    assert "stt_model_version" in properties
    assert properties["vosk_version"]["deprecated"] is True
