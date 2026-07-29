import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database import AsyncSessionLocal
from app.models.alert import Alert, AlertMatchedTerm
from app.models.slur import SlurEntry
from tests.conftest import auth_headers


def _alert_payload(**overrides) -> dict:
    payload = {
        "severity": "medium",
        "confidence": 0.91,
        "duration": 1.25,
        "location": "Room 101",
    }
    payload.update(overrides)
    return payload


async def _seed_terms() -> dict[str, SlurEntry]:
    async with AsyncSessionLocal() as session:
        entries = {
            "fil": SlurEntry(
                slur_text="halimbawang salita",
                language="fil",
                severity_weight=0.7,
            ),
            "ceb": SlurEntry(
                slur_text="pananglitan nga pulong",
                language="ceb",
                severity_weight=0.8,
            ),
            "en": SlurEntry(
                slur_text="example phrase",
                language="en",
                severity_weight=0.6,
            ),
        }
        session.add_all(entries.values())
        await session.commit()
        for entry in entries.values():
            await session.refresh(entry)
        return entries


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["fil", "ceb", "en", "mixed", "unknown"])
async def test_alert_accepts_shared_language_values(client, language):
    response = await client.post(
        "/alerts/",
        json=_alert_payload(language=language, language_confidence=0.82),
    )

    assert response.status_code == 200
    assert response.json()["language"] == language
    assert response.json()["language_confidence"] == 0.82


@pytest.mark.asyncio
async def test_invalid_alert_language_is_rejected(client):
    response = await client.post(
        "/alerts/",
        json=_alert_payload(language="Bisaya"),
    )

    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("confidence", [-0.01, 1.01])
async def test_language_confidence_bounds_are_enforced(client, confidence):
    response = await client.post(
        "/alerts/",
        json=_alert_payload(language="ceb", language_confidence=confidence),
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_alert_with_no_matched_terms(client):
    response = await client.post(
        "/alerts/",
        json=_alert_payload(language="unknown"),
    )

    assert response.status_code == 200
    assert response.json()["matched_terms"] == []


@pytest.mark.asyncio
async def test_alert_with_one_matched_term_uses_canonical_dictionary_data(client):
    terms = await _seed_terms()
    response = await client.post(
        "/alerts/",
        json=_alert_payload(
            transcript="Pananglitan nga pulong",
            language="ceb",
            language_confidence=0.82,
            matched_terms=[
                {
                    "term_id": terms["ceb"].term_id,
                    "term": "  PANANGLITAN   NGA PULONG ",
                    "language": "ceb",
                    "match_type": "phrase",
                }
            ],
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["transcribed_text"] == "Pananglitan nga pulong"
    assert body["matched_terms"] == [
        {
            "term_id": terms["ceb"].term_id,
            "term": "pananglitan nga pulong",
            "language": "ceb",
            "match_type": "phrase",
        }
    ]


@pytest.mark.asyncio
async def test_alert_with_multiple_matched_terms_resolves_missing_ids(client):
    terms = await _seed_terms()
    response = await client.post(
        "/alerts/",
        json=_alert_payload(
            language="mixed",
            matched_terms=[
                {
                    "term_id": terms["fil"].term_id,
                    "term": "halimbawang salita",
                    "language": "fil",
                    "match_type": "phrase",
                },
                {
                    "term": " EXAMPLE   PHRASE ",
                    "language": "en",
                    "match_type": "phrase",
                },
            ],
        ),
    )

    assert response.status_code == 200
    assert response.json()["matched_terms"] == [
        {
            "term_id": terms["fil"].term_id,
            "term": "halimbawang salita",
            "language": "fil",
            "match_type": "phrase",
        },
        {
            "term_id": terms["en"].term_id,
            "term": "example phrase",
            "language": "en",
            "match_type": "phrase",
        },
    ]


@pytest.mark.asyncio
async def test_nonexistent_term_id_is_rejected(client):
    response = await client.post(
        "/alerts/",
        json=_alert_payload(
            language="en",
            matched_terms=[{"term_id": 999999, "match_type": "exact"}],
        ),
    )

    assert response.status_code == 422
    assert "does not exist" in response.json()["detail"]


@pytest.mark.asyncio
async def test_client_dictionary_data_must_match_existing_term(client):
    terms = await _seed_terms()
    response = await client.post(
        "/alerts/",
        json=_alert_payload(
            language="ceb",
            matched_terms=[
                {
                    "term_id": terms["ceb"].term_id,
                    "term": "invented phrase",
                    "language": "fil",
                }
            ],
        ),
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_duplicate_matched_term_is_rejected_by_api(client):
    terms = await _seed_terms()
    response = await client.post(
        "/alerts/",
        json=_alert_payload(
            language="fil",
            matched_terms=[
                {"term_id": terms["fil"].term_id},
                {
                    "term": "HALIMBAWANG SALITA",
                    "language": "fil",
                },
            ],
        ),
    )

    assert response.status_code == 422
    assert "duplicated" in response.json()["detail"]


@pytest.mark.asyncio
async def test_duplicate_alert_term_relation_is_blocked_by_database():
    terms = await _seed_terms()
    async with AsyncSessionLocal() as session:
        alert = Alert(
            severity="low",
            confidence=0.6,
            duration=0.5,
            language="fil",
        )
        session.add(alert)
        await session.flush()
        session.add_all(
            [
                AlertMatchedTerm(
                    alert_id=alert.id,
                    term_id=terms["fil"].term_id,
                    matched_text=terms["fil"].slur_text,
                ),
                AlertMatchedTerm(
                    alert_id=alert.id,
                    term_id=terms["fil"].term_id,
                    matched_text=terms["fil"].slur_text,
                ),
            ]
        )

        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_legacy_alert_payload_remains_accepted(client):
    response = await client.post("/alerts/", json=_alert_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["language"] == "unknown"
    assert body["language_confidence"] is None
    assert body["matched_terms"] == []


@pytest.mark.asyncio
async def test_alert_list_and_detail_include_matched_terms(client, identities):
    terms = await _seed_terms()
    created = await client.post(
        "/alerts/",
        json=_alert_payload(
            language="en",
            matched_terms=[
                {
                    "term_id": terms["en"].term_id,
                    "term": terms["en"].slur_text,
                    "language": "en",
                    "match_type": "phrase",
                }
            ],
        ),
    )
    alert_id = created.json()["id"]

    headers = auth_headers(identities["staff"])
    listed = await client.get("/alerts/", headers=headers)
    detail = await client.get(f"/alerts/{alert_id}", headers=headers)

    assert listed.status_code == 200
    assert detail.status_code == 200
    assert listed.json()[0]["matched_terms"] == created.json()["matched_terms"]
    assert detail.json()["matched_terms"] == created.json()["matched_terms"]


@pytest.mark.asyncio
async def test_linked_dictionary_term_cannot_be_deleted(client, identities):
    terms = await _seed_terms()
    created = await client.post(
        "/alerts/",
        json=_alert_payload(
            language="ceb",
            matched_terms=[{"term_id": terms["ceb"].term_id}],
        ),
    )
    assert created.status_code == 200

    deleted = await client.delete(
        f"/dictionary/{terms['ceb'].term_id}",
        headers={"Authorization": f"Bearer {identities['admin']['token']}"},
    )

    assert deleted.status_code == 409


@pytest.mark.asyncio
async def test_dictionary_accepts_only_dictionary_language_codes(client, identities):
    response = await client.post(
        "/dictionary/",
        headers={"Authorization": f"Bearer {identities['admin']['token']}"},
        json={
            "slur_text": "invalid language term",
            "language": "mixed",
            "severity_weight": 0.5,
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_association_stores_submitted_matched_text(client):
    terms = await _seed_terms()
    created = await client.post(
        "/alerts/",
        json=_alert_payload(
            language="en",
            matched_terms=[
                {
                    "term_id": terms["en"].term_id,
                    "term": "EXAMPLE PHRASE",
                    "match_type": "casefolded",
                }
            ],
        ),
    )

    async with AsyncSessionLocal() as session:
        relation = (
            await session.execute(
                select(AlertMatchedTerm).where(AlertMatchedTerm.alert_id == created.json()["id"])
            )
        ).scalar_one()

    assert relation.matched_text == "EXAMPLE PHRASE"
    assert relation.match_type == "casefolded"
