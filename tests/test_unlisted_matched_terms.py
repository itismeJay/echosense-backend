"""P0-1: an authenticated device's alert is never rejected over a dictionary gap."""

import pytest
from sqlalchemy import delete, select

from app.database import AsyncSessionLocal
from app.models.alert import AlertMatchedTerm
from app.models.school import School
from app.models.school_settings import SchoolTerm
from app.models.slur import SlurEntry
from tests.conftest import auth_headers, finalized_alert_fields


def _payload(matched_terms, **overrides) -> dict:
    payload = finalized_alert_fields()
    payload.update(
        {
            "severity": "medium",
            "confidence": 0.9,
            "duration": 1.5,
            "transcribed_text": "Synthetic transcript.",
            "language": "ceb",
            "matched_terms": matched_terms,
        }
    )
    payload.update(overrides)
    return payload


async def _school_id(name="Synthetic Test School"):
    async with AsyncSessionLocal() as session:
        return await session.scalar(select(School.id).where(School.name == name))


async def _add_school_term(school_id, text="pulong sa eskwelahan", language="ceb") -> int:
    async with AsyncSessionLocal() as session:
        term = SchoolTerm(school_id=school_id, term_text=text, language=language)
        session.add(term)
        await session.commit()
        return term.id


@pytest.mark.asyncio
async def test_the_lost_buanga_payload_is_now_stored(client, identities):
    # The exact matched_terms of the FAILED_PERMANENT outbox row from 2026-11-04.
    response = await client.post(
        "/alerts/", json=_payload([{"match_type": "word", "term": "buanga"}])
    )

    assert response.status_code == 200
    body = response.json()
    assert body["matched_terms"] == []
    assert body["unlisted_terms"] == [
        {"term": "buanga", "language": None, "match_type": "word", "source": "unlisted"}
    ]

    listed = await client.get("/alerts/", headers=auth_headers(identities["staff"]))
    detail = await client.get(f"/alerts/{body['id']}", headers=auth_headers(identities["staff"]))
    assert listed.json()[0]["unlisted_terms"] == body["unlisted_terms"]
    assert detail.json()["unlisted_terms"] == body["unlisted_terms"]


@pytest.mark.asyncio
async def test_known_and_unknown_terms_are_split_between_the_two_lists(client):
    async with AsyncSessionLocal() as session:
        entry = SlurEntry(slur_text="buang ka", language="ceb", severity_weight=0.8)
        session.add(entry)
        await session.commit()
        term_id = entry.term_id

    response = await client.post(
        "/alerts/",
        json=_payload(
            [
                {"term": "buang ka", "language": "ceb", "match_type": "phrase"},
                {"term": "bag-ong pulong", "language": "ceb", "match_type": "phrase"},
            ]
        ),
    )

    assert response.status_code == 200
    assert response.json()["matched_terms"] == [
        {"term_id": term_id, "term": "buang ka", "language": "ceb", "match_type": "phrase"}
    ]
    assert response.json()["unlisted_terms"] == [
        {
            "term": "bag-ong pulong",
            "language": "ceb",
            "match_type": "phrase",
            "source": "unlisted",
        }
    ]


@pytest.mark.asyncio
async def test_school_word_is_linked_by_text_and_by_school_term_id(client):
    school_term_id = await _add_school_term(await _school_id())

    by_text = await client.post("/alerts/", json=_payload([{"term": "Pulong sa Eskwelahan"}]))
    by_id = await client.post(
        "/alerts/",
        json=_payload([{"term_id": school_term_id, "source": "school"}]),
    )

    for response in (by_text, by_id):
        assert response.status_code == 200
        assert response.json()["matched_terms"] == []
        assert response.json()["unlisted_terms"] == [
            {
                "term": "pulong sa eskwelahan",
                "language": "ceb",
                "match_type": "exact",
                "source": "school",
            }
        ]
    async with AsyncSessionLocal() as session:
        linked = (await session.scalars(select(AlertMatchedTerm.school_term_id))).all()
    assert linked == [school_term_id, school_term_id]


@pytest.mark.asyncio
async def test_another_schools_word_is_never_linked(client):
    async with AsyncSessionLocal() as session:
        other = School(name="Other Synthetic School")
        session.add(other)
        await session.commit()
        other_id = other.id
    foreign_term_id = await _add_school_term(other_id, text="sekreto nga pulong")

    response = await client.post(
        "/alerts/",
        json=_payload(
            [
                {"term_id": foreign_term_id, "source": "school"},
                {"term": "sekreto nga pulong"},
            ]
        ),
    )

    assert response.status_code == 200
    sources = [item["source"] for item in response.json()["unlisted_terms"]]
    assert sources == ["unlisted", "unlisted"]
    async with AsyncSessionLocal() as session:
        linked = (await session.scalars(select(AlertMatchedTerm.school_term_id))).all()
    assert linked == [None, None]


@pytest.mark.asyncio
async def test_deleting_a_school_word_keeps_the_alert_evidence(client, identities):
    school_term_id = await _add_school_term(await _school_id())
    created = await client.post("/alerts/", json=_payload([{"term": "pulong sa eskwelahan"}]))
    assert created.status_code == 200

    async with AsyncSessionLocal() as session:
        await session.execute(delete(SchoolTerm).where(SchoolTerm.id == school_term_id))
        await session.commit()

    detail = await client.get(
        f"/alerts/{created.json()['id']}", headers=auth_headers(identities["staff"])
    )
    assert detail.status_code == 200
    assert detail.json()["unlisted_terms"] == [
        {
            "term": "pulong sa eskwelahan",
            "language": "ceb",
            "match_type": "exact",
            "source": "school",
        }
    ]


@pytest.mark.asyncio
async def test_odd_but_real_evidence_is_cleaned_not_rejected(client):
    long_phrase = "pulong " * 30
    response = await client.post(
        "/alerts/",
        json=_payload(
            [
                {"term": "buanga", "language": "mixed", "confidence": 0.7},
                {"term": long_phrase, "match_type": "", "term_id": True},
            ]
        ),
    )

    assert response.status_code == 200
    unlisted = response.json()["unlisted_terms"]
    assert unlisted[0] == {
        "term": "buanga",
        "language": None,
        "match_type": "exact",
        "source": "unlisted",
    }
    assert len(unlisted[1]["term"]) == 100
    assert unlisted[1]["match_type"] == "exact"


@pytest.mark.asyncio
async def test_a_term_without_text_or_id_is_still_rejected(client):
    # Not evidence at all: the device sent an empty object.
    response = await client.post("/alerts/", json=_payload([{"match_type": "word"}]))

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_unlisted_terms_still_require_an_authenticated_device(unauthenticated_edge_client):
    response = await unauthenticated_edge_client.post(
        "/alerts/", json=_payload([{"term": "buanga"}])
    )

    assert response.status_code == 401
